"""
Vérification des points de fidélité article
--------------------------------------------
Lit le fichier 999_articlespoints_*.csv (format: shop_ref;article_code;points)
et vérifie pour chaque article si le champ loyalty_earn_points sur le POS
correspond à la valeur attendue.

Endpoints POS essayés dans l'ordre :
  1. /api/product_loyalty_option/?product__code=XXX   (loyalty seul)
  2. /api/product/?code=XXX                           (produit complet)

Produit un rapport JSON + CSV dans logs/.
Peut émettre des événements SSE via un fichier de progression (progress.json).
"""

import csv
import json
import logging
import os
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from time import perf_counter

import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ─── Chemins ────────────────────────────────────────────────────────────────

BASE_DIR  = Path(__file__).parent
LOGS_DIR  = BASE_DIR / "logs"
MAGASIN   = BASE_DIR / "magasin.json"
PROGRESS_FILE = LOGS_DIR / "verif_progress.json"

LOGS_DIR.mkdir(exist_ok=True)

SMB_BACKUP = Path(
    "/run/user/1000/gvfs/smb-share:server=10.0.70.104,share=d/"
    "Cegid/AstenProd/ReferentielPrecom/Backup"
)

# ─── Logger ─────────────────────────────────────────────────────────────────

def setup_logger():
    logger = logging.getLogger("verif_points")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s | %(levelname)-8s | %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    # Éviter les handlers en doublon si le module est rechargé
    if not logger.handlers:
        fh = logging.FileHandler(LOGS_DIR / "verif_points.log", encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(fmt)
        ch = logging.StreamHandler(sys.stdout)
        ch.setLevel(logging.INFO)
        ch.setFormatter(fmt)
        logger.addHandler(fh)
        logger.addHandler(ch)
    return logger

log = setup_logger()

# ─── Config ─────────────────────────────────────────────────────────────────

def load_config():
    load_dotenv(BASE_DIR / ".env")
    config = {
        "user":       os.getenv("USER_POS", ""),
        "password":   os.getenv("PASSWORD_POS", ""),
        "pos4_user":  os.getenv("POS4_USER_POS", "") or os.getenv("USER_POS", ""),
        "pos4_pass":  os.getenv("POS4_PASSWORD_POS", "") or os.getenv("PASSWORD_POS", ""),
        "verify_tls": os.getenv("VERIFY_TLS", "false").lower() in ("1", "true", "yes"),
        "timeout":    float(os.getenv("REQUEST_TIMEOUT", "30")),
    }
    if not config["user"] or not config["password"]:
        log.error("USER_POS ou PASSWORD_POS manquant dans .env")
        sys.exit(1)
    return config


def load_magasin():
    with open(MAGASIN, encoding="utf-8") as f:
        return json.load(f)


def make_session(pos_id, config):
    session = requests.Session()
    if pos_id == "pos4":
        session.auth = (config["pos4_user"], config["pos4_pass"])
    else:
        session.auth = (config["user"], config["password"])
    session.verify = config["verify_tls"]
    session.headers.update({"Accept": "application/json"})
    return session

# ─── Progression (pour SSE) ─────────────────────────────────────────────────

def write_progress(state: dict):
    """Écrit l'état de progression dans un fichier JSON lu par la vue SSE."""
    try:
        PROGRESS_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def clear_progress():
    try:
        PROGRESS_FILE.unlink(missing_ok=True)
    except Exception:
        pass

# ─── Lecture fichier articlespoints ─────────────────────────────────────────

def find_latest_articlespoints():
    candidates = sorted(SMB_BACKUP.glob("999_articlespoints_*.csv"), reverse=True)
    if not candidates:
        sous = SMB_BACKUP / "articlespoints"
        if sous.exists():
            candidates = sorted(sous.glob("999_articlespoints_*.csv"), reverse=True)
    if not candidates:
        log.error("Aucun fichier 999_articlespoints_*.csv trouvé dans %s", SMB_BACKUP)
        return None
    latest = candidates[0]
    log.info("Fichier articlespoints : %s (%d dispo)", latest.name, len(candidates))
    return latest


def read_articlespoints(path):
    data = defaultdict(dict)
    with open(path, encoding="utf-8-sig", newline="") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(";")
            if len(parts) < 3:
                continue
            shop_ref     = parts[0].strip().lstrip("0") or "0"
            article_raw  = parts[1].strip()
            article_code = article_raw.lstrip("0") or "0"
            try:
                points = int(parts[2].strip())
            except ValueError:
                continue
            data[shop_ref][article_code] = points
    total = sum(len(v) for v in data.values())
    log.info("Fichier lu : %d articles dans %d magasins", total, len(data))
    return data

# ─── Mapping shop_ref → POS ─────────────────────────────────────────────────

def build_shop_to_pos(magasin):
    mapping = {}
    for pos_id, info in magasin.items():
        for shop in info["shops"]:
            ref = shop["reference"].lstrip("0") or "0"
            mapping[ref] = {
                "pos_id":    pos_id,
                "url":       info["url"],
                "shop_id":   shop["id"],
                "shop_name": shop["name"],
            }
    return mapping

# ─── Détection endpoint POS ─────────────────────────────────────────────────

_ENDPOINT_CACHE = {}  # pos_id → endpoint trouvé

def detect_endpoint(session, base_url, pos_id, timeout):
    """
    Détecte l'endpoint utilisable sur ce POS pour récupérer loyalty_earn_points.
    Essaie dans l'ordre :
      1. /api/product_loyalty_option/   → {results:[{product:{code}, earn_points}]}
      2. /api/product/                  → {results:[{code, loyalty_earn_points}]}
    Retourne le nom de l'endpoint ou None.
    """
    if pos_id in _ENDPOINT_CACHE:
        return _ENDPOINT_CACHE[pos_id]

    candidates = [
        ("product_loyalty_option", "/api/product_loyalty_option/", "earn_points"),
        ("product",                "/api/product/",                 "loyalty_earn_points"),
        ("product_loyalty_options","/api/product_loyalty_options/", "earn_points"),
    ]
    for name, ep, field in candidates:
        try:
            r = session.get(f"{base_url}{ep}", params={"page_size": 1}, timeout=timeout)
            if r.status_code == 200:
                data = r.json()
                results = data.get("results", []) if isinstance(data, dict) else data
                # Vérifier que le champ loyalty est présent
                if results and field in results[0]:
                    log.info("[%s] Endpoint détecté : %s (champ '%s')", pos_id, ep, field)
                    _ENDPOINT_CACHE[pos_id] = (name, ep, field)
                    return _ENDPOINT_CACHE[pos_id]
                elif not results:
                    # Endpoint existe mais vide — on garde quand même
                    log.info("[%s] Endpoint %s vide mais accessible", pos_id, ep)
                    _ENDPOINT_CACHE[pos_id] = (name, ep, field)
                    return _ENDPOINT_CACHE[pos_id]
        except requests.RequestException:
            pass

    log.warning("[%s] Aucun endpoint de points fidélité trouvé", pos_id)
    _ENDPOINT_CACHE[pos_id] = None
    return None

# ─── Récupération des points POS ────────────────────────────────────────────

def fetch_loyalty_points(session, base_url, pos_id, article_codes, timeout, shop_id=None):
    """
    Récupère {article_code_normalisé: points} depuis le POS.
    Essaie plusieurs stratégies selon l'endpoint détecté.
    """
    endpoint_info = detect_endpoint(session, base_url, pos_id, timeout)
    if not endpoint_info:
        return {}

    name, ep, field = endpoint_info
    result = {}
    url = f"{base_url}{ep}"

    if name in ("product_loyalty_option", "product_loyalty_options"):
        # Format: {product: {code: "XXX"}, earn_points: N}
        # Chercher article par article (pas de filtre multi-code connu)
        for code in article_codes:
            for search_val in [code, code.zfill(9), code.zfill(13)]:
                try:
                    params = {"page_size": 10, "product__code": search_val}
                    if shop_id:
                        params["shop"] = shop_id
                    r = session.get(url, params=params, timeout=timeout)
                    if r.status_code != 200:
                        continue
                    data = r.json()
                    items = data.get("results", data) if isinstance(data, dict) else data
                    for item in items:
                        prod = item.get("product", {})
                        raw_code = str(prod.get("code", "")).strip().lstrip("0") or "0"
                        if raw_code == code:
                            lp = item.get(field)
                            result[code] = int(lp) if lp not in (None, "") else 0
                            break
                    if code in result:
                        break
                except requests.RequestException:
                    break

    else:
        # Format: {code: "XXX", loyalty_earn_points: N}
        # Essayer en batch d'abord
        BATCH = 50
        codes_list = list(article_codes)
        for i in range(0, len(codes_list), BATCH):
            batch = codes_list[i:i + BATCH]
            page = 1
            while True:
                params = {"page_size": 200, "page": page}
                if len(batch) == 1:
                    params["search"] = batch[0]
                    params["code"] = batch[0]
                else:
                    params["code__in"] = ",".join(batch)
                try:
                    r = session.get(url, params=params, timeout=timeout)
                    if r.status_code != 200:
                        break
                    data = r.json()
                    items = data.get("results", data) if isinstance(data, dict) else data
                    for item in items:
                        raw_code = str(item.get("code", "")).strip().lstrip("0") or "0"
                        lp = item.get(field)
                        if lp is not None and raw_code in article_codes:
                            result[raw_code] = int(lp) if lp != "" else 0
                    has_next = bool(data.get("next")) or bool(data.get("next_page")) if isinstance(data, dict) else False
                    if not has_next:
                        break
                    page += 1
                except requests.RequestException:
                    break

    return result

# ─── Vérification par POS ────────────────────────────────────────────────────

def verifier_pos(pos_id, pos_info, shop_articles, config, progress_cb=None):
    base_url = pos_info["url"]
    session  = make_session(pos_id, config)
    timeout  = config["timeout"]

    # Regrouper tous les articles de tous les shops de ce POS
    all_articles = {}  # article_code → {shop_ref: points_attendus}
    for shop_ref, articles in shop_articles.items():
        for code, pts in articles.items():
            if code not in all_articles:
                all_articles[code] = {}
            all_articles[code][shop_ref] = pts

    total_articles = len(all_articles)
    log.info("[%s] %d articles à vérifier sur %s", pos_id, total_articles, base_url)

    endpoint_info = detect_endpoint(session, base_url, pos_id, timeout)
    if not endpoint_info:
        # Endpoint inconnu — marquer tous ABSENT
        rows = []
        for article_code, shops in all_articles.items():
            for shop_ref, points_attendus in shops.items():
                rows.append({
                    "pos_id": pos_id, "shop_ref": shop_ref,
                    "article_code": article_code,
                    "points_attendus": points_attendus,
                    "points_pos": "", "statut": "ENDPOINT_INCONNU",
                })
        return rows

    name, ep, field = endpoint_info
    rows = []
    done = 0

    if name in ("product_loyalty_option", "product_loyalty_options"):
        # Article par article avec progression
        for article_code, shops in all_articles.items():
            pos_data = fetch_loyalty_points(
                session, base_url, pos_id, {article_code}, timeout
            )
            points_pos = pos_data.get(article_code)
            done += 1
            for shop_ref, points_attendus in shops.items():
                if points_pos is None:
                    statut = "ARTICLE_ABSENT"
                elif points_pos == points_attendus:
                    statut = "OK"
                else:
                    statut = "ECART"
                rows.append({
                    "pos_id": pos_id, "shop_ref": shop_ref,
                    "article_code": article_code,
                    "points_attendus": points_attendus,
                    "points_pos": points_pos if points_pos is not None else "",
                    "statut": statut,
                })
            if progress_cb and done % 10 == 0:
                progress_cb(pos_id, done, total_articles, rows)
    else:
        # Batch
        pos_data = fetch_loyalty_points(
            session, base_url, pos_id, set(all_articles.keys()), timeout
        )
        for article_code, shops in all_articles.items():
            points_pos = pos_data.get(article_code)
            for shop_ref, points_attendus in shops.items():
                if points_pos is None:
                    statut = "ARTICLE_ABSENT"
                elif points_pos == points_attendus:
                    statut = "OK"
                else:
                    statut = "ECART"
                rows.append({
                    "pos_id": pos_id, "shop_ref": shop_ref,
                    "article_code": article_code,
                    "points_attendus": points_attendus,
                    "points_pos": points_pos if points_pos is not None else "",
                    "statut": statut,
                })

    ok     = sum(1 for r in rows if r["statut"] == "OK")
    ecart  = sum(1 for r in rows if r["statut"] == "ECART")
    absent = sum(1 for r in rows if r["statut"] == "ARTICLE_ABSENT")
    log.info("[%s] OK=%d  ECART=%d  ABSENT=%d", pos_id, ok, ecart, absent)
    return rows

# ─── Rapport ────────────────────────────────────────────────────────────────

def save_report(all_rows, source_file, started_at, elapsed_s):
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    csv_path  = LOGS_DIR / f"verif_points_{ts}.csv"
    json_path = LOGS_DIR / f"verif_points_{ts}.json"

    fieldnames = ["pos_id", "shop_ref", "article_code", "points_attendus", "points_pos", "statut"]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        w.writeheader()
        w.writerows(all_rows)

    total  = len(all_rows)
    ok     = sum(1 for r in all_rows if r["statut"] == "OK")
    ecart  = sum(1 for r in all_rows if r["statut"] == "ECART")
    absent = sum(1 for r in all_rows if r["statut"] == "ARTICLE_ABSENT")

    report = {
        "started_at":  started_at,
        "elapsed_s":   round(elapsed_s, 1),
        "source_file": source_file,
        "total":       total,
        "ok":          ok,
        "ecart":       ecart,
        "absent":      absent,
        "ecarts":      [r for r in all_rows if r["statut"] in ("ECART", "ARTICLE_ABSENT")][:500],
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    log.info("Rapport : %s", json_path)
    log.info("Résumé  : total=%d  OK=%d  ECART=%d  ABSENT=%d", total, ok, ecart, absent)
    return json_path, csv_path

# ─── Main ────────────────────────────────────────────────────────────────────

def run(pos_filter=None, source_file=None, progress_cb=None):
    """
    Lance la vérification complète.
    progress_cb(pos_id, done, total, rows_so_far) est appelé toutes les 10 lignes.
    """
    started_at = datetime.now().isoformat()
    t0 = perf_counter()

    write_progress({"running": True, "started_at": started_at,
                    "pos_current": None, "done": 0, "total": 0, "rows": []})

    config  = load_config()
    magasin = load_magasin()

    src = Path(source_file) if source_file else find_latest_articlespoints()
    if not src or not src.exists():
        log.error("Fichier source introuvable : %s", src)
        write_progress({"running": False, "error": "Fichier source introuvable"})
        return None

    articles_par_shop = read_articlespoints(src)
    shop_to_pos       = build_shop_to_pos(magasin)

    articles_par_pos = defaultdict(dict)
    shops_sans_pos   = set()
    for shop_ref, articles in articles_par_shop.items():
        pos_info = shop_to_pos.get(shop_ref)
        if not pos_info:
            shops_sans_pos.add(shop_ref)
            continue
        articles_par_pos[pos_info["pos_id"]][shop_ref] = articles

    if shops_sans_pos:
        log.warning("Shops sans POS : %s", ", ".join(sorted(shops_sans_pos)))

    targets = {
        pid: magasin[pid]
        for pid in articles_par_pos
        if pid in magasin and (pos_filter is None or pid in pos_filter)
    }

    total_articles = sum(
        len(articles)
        for pid in targets
        for articles in articles_par_pos[pid].values()
    )
    log.info("POS à vérifier : %d  |  articles total : %d", len(targets), total_articles)

    all_rows   = []
    done_total = 0

    def _progress(pos_id, done_pos, total_pos, rows_pos):
        nonlocal done_total
        done_total = sum(1 for r in all_rows) + done_pos
        pct = round(done_total / max(total_articles, 1) * 100, 1)
        write_progress({
            "running":     True,
            "started_at":  started_at,
            "pos_current": pos_id,
            "done":        done_total,
            "total":       total_articles,
            "pct":         pct,
            "rows":        rows_pos[-20:],  # les 20 dernières lignes du POS en cours
        })
        if progress_cb:
            progress_cb(pos_id, done_pos, total_pos, rows_pos)

    for pos_id, pos_info in sorted(targets.items()):
        try:
            rows = verifier_pos(pos_id, pos_info, articles_par_pos[pos_id], config,
                                progress_cb=_progress)
            all_rows.extend(rows)
            done_total = len(all_rows)
        except Exception as e:
            log.error("[%s] Erreur : %s", pos_id, e)

    elapsed = perf_counter() - t0
    json_path, csv_path = save_report(all_rows, src.name, started_at, elapsed)

    write_progress({
        "running":    False,
        "finished":   True,
        "started_at": started_at,
        "elapsed_s":  round(elapsed, 1),
        "total":      len(all_rows),
        "ok":         sum(1 for r in all_rows if r["statut"] == "OK"),
        "ecart":      sum(1 for r in all_rows if r["statut"] == "ECART"),
        "absent":     sum(1 for r in all_rows if r["statut"] == "ARTICLE_ABSENT"),
        "rapport":    json_path.name,
    })
    return json_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pos",    help="POS à vérifier, ex: pos4,pos1")
    parser.add_argument("--fichier",help="Chemin vers 999_articlespoints_*.csv")
    args = parser.parse_args()
    pos_filter = {p.strip() for p in args.pos.split(",") if p.strip()} if args.pos else None
    run(pos_filter=pos_filter, source_file=args.fichier)
