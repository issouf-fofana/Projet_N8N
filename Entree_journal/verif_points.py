"""
Vérification des points de fidélité article
--------------------------------------------
Lit le fichier 999_articlespoints_*.csv (format: shop_ref;article_code;points)
et vérifie pour chaque article si le champ earned_points sur le POS
correspond à la valeur attendue.

Endpoint POS :
  /api/product/?ean=<code_article>  → results[0].earned_points

Produit un rapport JSON + CSV dans logs/.
Peut émettre des événements SSE via un fichier de progression (progress.json).
"""

import csv
import json
import logging
import os
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
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
    os.getenv(
        "SMB_BACKUP_PATH",
        "/run/user/1000/gvfs/smb-share:server=10.0.70.104,share=d/"
        "Cegid/AstenProd/ReferentielPrecom/Backup"
    )
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

# ─── Vérification de connectivité POS ───────────────────────────────────────

_REACHABLE_CACHE = {}  # pos_id → bool

def is_pos_reachable(session, base_url, pos_id, timeout):
    """Vérifie que le POS répond sur /api/product/ (page_size=1)."""
    if pos_id in _REACHABLE_CACHE:
        return _REACHABLE_CACHE[pos_id]
    try:
        r = session.get(f"{base_url}/api/product/", params={"page_size": 1}, timeout=timeout)
        ok = r.status_code == 200
    except requests.RequestException:
        ok = False
    _REACHABLE_CACHE[pos_id] = ok
    if not ok:
        log.warning("[%s] POS inaccessible : %s", pos_id, base_url)
    return ok

# ─── Chargement bulk EAN→UUID ────────────────────────────────────────────────

def load_ean_to_uuid(session, base_url, pos_id, article_codes, timeout):
    """
    Charge toute la liste produits paginée et construit un dict ean→uuid
    pour les articles demandés uniquement. Une seule passe sur l'API.
    """
    url     = f"{base_url}/api/product/"
    result  = {}
    page    = 1
    target  = set(article_codes)
    found   = 0
    total_target = len(target)

    log.info("[%s] Chargement EAN→UUID pour %d articles…", pos_id, total_target)
    while target:
        try:
            r = session.get(url, params={"page_size": 500, "page": page}, timeout=timeout)
            if r.status_code != 200:
                break
            data = r.json()
        except requests.RequestException as e:
            log.warning("[%s] Erreur pagination page %d : %s", pos_id, page, e)
            break

        items = data.get("results", []) if isinstance(data, dict) else []
        if not items:
            break

        for item in items:
            ean = str(item.get("ean", "")).strip().lstrip("0") or "0"
            if ean in target:
                result[ean] = item["id"]
                target.discard(ean)
                found += 1

        final_page = data.get("final_page") or data.get("num_pages")
        if final_page and page >= int(final_page):
            break
        if not data.get("next_page") and not data.get("next"):
            break
        page += 1

    log.info("[%s] EAN→UUID : %d/%d trouvés en %d pages", pos_id, found, total_target, page)
    return result

# ─── Récupération earned_points par UUID ────────────────────────────────────

def fetch_earned_points_by_uuid(session, base_url, uuid, timeout):
    """
    Retourne extras.earned_points depuis /api/product/<uuid>/.
    Retourne None si absent, -1 si erreur réseau.
    """
    try:
        r = session.get(f"{base_url}/api/product/{uuid}/", timeout=timeout)
        if r.status_code != 200:
            return None
        item   = r.json()
        extras = item.get("extras") or {}
        ep     = extras.get("earned_points") if isinstance(extras, dict) else None
        return int(ep) if ep not in (None, "") else 0
    except requests.RequestException:
        return -1

# ─── Vérification par POS ────────────────────────────────────────────────────

WORKERS_PER_POS = int(os.getenv("VERIF_WORKERS", "20"))  # requêtes parallèles par POS


def verifier_pos(pos_id, pos_info, shop_articles, config, progress_cb=None):
    base_url = pos_info["url"]
    timeout  = config["timeout"]

    # Regrouper tous les articles de tous les shops de ce POS
    all_articles = {}  # article_code → {shop_ref: points_attendus}
    for shop_ref, articles in shop_articles.items():
        for code, pts in articles.items():
            if code not in all_articles:
                all_articles[code] = {}
            all_articles[code][shop_ref] = pts

    total_articles = len(all_articles)
    log.info("[%s] %d articles à vérifier sur %s (workers=%d)", pos_id, total_articles, base_url, WORKERS_PER_POS)

    # Session partagée pour le bulk (lecture seule, pagination)
    bulk_session = make_session(pos_id, config)
    if not is_pos_reachable(bulk_session, base_url, pos_id, timeout):
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

    # Étape 1 : une seule passe paginée pour obtenir ean→uuid
    ean_to_uuid = load_ean_to_uuid(bulk_session, base_url, pos_id, list(all_articles.keys()), timeout)

    rows = []
    done = 0
    lock = __import__("threading").Lock()

    def fetch_detail(article_code):
        uuid = ean_to_uuid.get(article_code)
        if not uuid:
            return article_code, None   # article absent de la liste
        sess = make_session(pos_id, config)
        return article_code, fetch_earned_points_by_uuid(sess, base_url, uuid, timeout)

    # Étape 2 : détails en parallèle (seulement pour les articles trouvés)
    with ThreadPoolExecutor(max_workers=WORKERS_PER_POS) as executor:
        futures = {executor.submit(fetch_detail, code): code for code in all_articles}
        for future in as_completed(futures):
            article_code, points_pos = future.result()
            shops = all_articles[article_code]
            new_rows = []
            for shop_ref, points_attendus in shops.items():
                if points_pos is None:
                    statut = "ARTICLE_ABSENT"
                elif points_pos == -1:
                    statut = "ENDPOINT_INCONNU"
                elif points_pos == points_attendus:
                    statut = "OK"
                else:
                    statut = "ECART"
                new_rows.append({
                    "pos_id": pos_id, "shop_ref": shop_ref,
                    "article_code": article_code,
                    "points_attendus": points_attendus,
                    "points_pos": points_pos if points_pos not in (None, -1) else "",
                    "statut": statut,
                })
            with lock:
                rows.extend(new_rows)
                done += 1
                if progress_cb and done % 10 == 0:
                    progress_cb(pos_id, done, total_articles, rows)

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
    absent = sum(1 for r in all_rows if r["statut"] in ("ARTICLE_ABSENT", "ENDPOINT_INCONNU"))

    report = {
        "started_at":  started_at,
        "elapsed_s":   round(elapsed_s, 1),
        "source_file": source_file,
        "total":       total,
        "ok":          ok,
        "ecart":       ecart,
        "absent":      absent,
        "ecarts":      [r for r in all_rows if r["statut"] in ("ECART", "ARTICLE_ABSENT", "ENDPOINT_INCONNU")][:500],
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
