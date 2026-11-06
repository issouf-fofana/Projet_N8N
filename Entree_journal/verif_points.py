"""
Vérification des points de fidélité article
--------------------------------------------
Lit le fichier 999_articlespoints_*.csv (format: shop_ref;article_code;points)
et vérifie pour chaque article si le champ loyalty_earn_points sur le POS
correspond à la valeur attendue.

Produit un rapport JSON + CSV dans logs/.
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

# ─── Lecture fichier articlespoints ─────────────────────────────────────────

def find_latest_articlespoints():
    """Trouve le fichier 999_articlespoints_*.csv le plus récent dans Backup/."""
    candidates = sorted(SMB_BACKUP.glob("999_articlespoints_*.csv"), reverse=True)
    if not candidates:
        # Chercher aussi dans sous-dossier articlespoints/
        candidates = sorted(
            (SMB_BACKUP / "articlespoints").glob("999_articlespoints_*.csv"),
            reverse=True
        )
    if not candidates:
        log.error("Aucun fichier 999_articlespoints_*.csv trouvé dans %s", SMB_BACKUP)
        return None
    latest = candidates[0]
    log.info("Fichier articlespoints : %s (%d fichiers disponibles)", latest.name, len(candidates))
    return latest


def read_articlespoints(path):
    """
    Lit le CSV shop_ref;article_code;points
    Retourne dict: {shop_ref: {article_code: points_attendus}}
    """
    data = defaultdict(dict)
    with open(path, encoding="utf-8-sig", newline="") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(";")
            if len(parts) < 3:
                continue
            shop_ref    = parts[0].strip().lstrip("0") or "0"
            article_raw = parts[1].strip()
            # Normaliser le code article : supprimer zéros de tête
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
    """Retourne dict: {shop_ref: {'pos_id': ..., 'url': ..., 'shop_id': ..., 'shop_name': ...}}"""
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

# ─── Appel API POS items ─────────────────────────────────────────────────────

def fetch_items_by_codes(session, base_url, article_codes, timeout, shop_id=None):
    """
    Récupère les informations d'articles depuis l'API POS.
    Retourne dict: {code_normalise: loyalty_earn_points}

    Stratégie : interroge l'endpoint /api/items/ par lot de codes (search),
    puis pagine jusqu'à tout récupérer.
    """
    url = f"{base_url}/api/items/"
    result = {}

    # Requêter par lot de 50 codes pour éviter des URLs trop longues
    BATCH = 50
    codes_list = list(article_codes)

    for i in range(0, len(codes_list), BATCH):
        batch = codes_list[i:i + BATCH]
        page = 1
        while True:
            params = {"page_size": 200, "page": page}
            if shop_id:
                params["shop"] = shop_id
            # Essayer avec search multi ou code exact
            if len(batch) == 1:
                params["search"] = batch[0]
            else:
                params["code__in"] = ",".join(batch)

            try:
                resp = session.get(url, params=params, timeout=timeout)
            except requests.RequestException as e:
                log.warning("Erreur HTTP %s : %s", url, e)
                break

            if resp.status_code != 200:
                log.warning("HTTP %d sur %s", resp.status_code, url)
                break

            try:
                data = resp.json()
            except ValueError:
                log.warning("Réponse non-JSON sur %s", url)
                break

            items = data.get("results", data) if isinstance(data, dict) else data

            for item in items:
                raw_code = str(item.get("code", "")).strip().lstrip("0") or "0"
                lp = item.get("loyalty_earn_points")
                if lp is not None:
                    result[raw_code] = int(lp) if lp != "" else 0

            # Pagination
            has_next = bool(data.get("next")) or bool(data.get("next_page")) if isinstance(data, dict) else False
            if not has_next:
                break
            page += 1

    return result


def fetch_single_item(session, base_url, article_code, timeout):
    """Cherche un article par code exact (fallback si batch échoue)."""
    url = f"{base_url}/api/items/"
    for search_val in [article_code, article_code.zfill(9), article_code.zfill(13)]:
        try:
            resp = session.get(url, params={"search": search_val, "page_size": 10}, timeout=timeout)
            if resp.status_code != 200:
                continue
            data = resp.json()
            items = data.get("results", data) if isinstance(data, dict) else data
            for item in items:
                raw_code = str(item.get("code", "")).strip().lstrip("0") or "0"
                if raw_code == article_code:
                    lp = item.get("loyalty_earn_points")
                    return int(lp) if lp not in (None, "") else 0
        except Exception:
            pass
    return None

# ─── Vérification par POS ────────────────────────────────────────────────────

def verifier_pos(pos_id, pos_info, shop_articles, config):
    """
    Vérifie les articles d'un POS.
    Retourne liste de dicts {shop_ref, article_code, points_attendus, points_pos, statut}
    """
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

    log.info("[%s] %d articles à vérifier sur %s", pos_id, len(all_articles), base_url)

    # Tenter fetch par lot
    pos_data = fetch_items_by_codes(session, base_url, set(all_articles.keys()), timeout)

    # Si batch vide, essayer individuellement (au moins quelques-uns pour diagnostiquer)
    if not pos_data and all_articles:
        log.info("[%s] Batch vide, tentative individuelle sur 5 articles...", pos_id)
        sample = list(all_articles.keys())[:5]
        for code in sample:
            val = fetch_single_item(session, base_url, code, timeout)
            if val is not None:
                pos_data[code] = val

    # Compiler les résultats
    rows = []
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
                "pos_id":          pos_id,
                "shop_ref":        shop_ref,
                "article_code":    article_code,
                "points_attendus": points_attendus,
                "points_pos":      points_pos if points_pos is not None else "",
                "statut":          statut,
            })

    ok     = sum(1 for r in rows if r["statut"] == "OK")
    ecart  = sum(1 for r in rows if r["statut"] == "ECART")
    absent = sum(1 for r in rows if r["statut"] == "ARTICLE_ABSENT")
    log.info("[%s] OK=%d  ECART=%d  ABSENT=%d", pos_id, ok, ecart, absent)
    return rows

# ─── Rapport ────────────────────────────────────────────────────────────────

def save_report(all_rows, source_file, started_at, elapsed_s):
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

    # CSV complet
    csv_path = LOGS_DIR / f"verif_points_{ts}.csv"
    fieldnames = ["pos_id", "shop_ref", "article_code", "points_attendus", "points_pos", "statut"]
    with open(csv_path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        w.writeheader()
        w.writerows(all_rows)

    # JSON résumé
    total  = len(all_rows)
    ok     = sum(1 for r in all_rows if r["statut"] == "OK")
    ecart  = sum(1 for r in all_rows if r["statut"] == "ECART")
    absent = sum(1 for r in all_rows if r["statut"] == "ARTICLE_ABSENT")

    # Détail des écarts
    ecarts_detail = [r for r in all_rows if r["statut"] in ("ECART", "ARTICLE_ABSENT")]

    report = {
        "started_at":   started_at,
        "elapsed_s":    round(elapsed_s, 1),
        "source_file":  source_file,
        "total":        total,
        "ok":           ok,
        "ecart":        ecart,
        "absent":       absent,
        "ecarts":       ecarts_detail[:500],  # limiter taille JSON
    }

    json_path = LOGS_DIR / f"verif_points_{ts}.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    log.info("Rapport : %s", json_path)
    log.info("CSV     : %s", csv_path)
    log.info("Résumé  : total=%d  OK=%d  ECART=%d  ABSENT=%d", total, ok, ecart, absent)
    return json_path, csv_path

# ─── Main ────────────────────────────────────────────────────────────────────

def run(pos_filter=None, source_file=None):
    started_at = datetime.now().isoformat()
    t0 = perf_counter()

    config  = load_config()
    magasin = load_magasin()

    # Fichier source
    if source_file:
        src = Path(source_file)
    else:
        src = find_latest_articlespoints()
    if not src or not src.exists():
        log.error("Fichier source introuvable : %s", src)
        return None

    # Lecture du fichier de référence
    articles_par_shop = read_articlespoints(src)  # {shop_ref: {article_code: points}}

    # Mapping shop_ref → POS
    shop_to_pos = build_shop_to_pos(magasin)

    # Regrouper les articles par POS
    articles_par_pos = defaultdict(dict)  # {pos_id: {shop_ref: {article_code: points}}}
    shops_sans_pos   = set()

    for shop_ref, articles in articles_par_shop.items():
        pos_info = shop_to_pos.get(shop_ref)
        if not pos_info:
            shops_sans_pos.add(shop_ref)
            continue
        pos_id = pos_info["pos_id"]
        articles_par_pos[pos_id][shop_ref] = articles

    if shops_sans_pos:
        log.warning("Shops sans POS configuré (ignorés) : %s", ", ".join(sorted(shops_sans_pos)))

    # Filtrer POS si demandé
    targets = {
        pid: magasin[pid]
        for pid in articles_par_pos
        if pid in magasin and (pos_filter is None or pid in pos_filter)
    }

    log.info("POS à vérifier : %d", len(targets))

    all_rows = []
    for pos_id, pos_info in sorted(targets.items()):
        try:
            rows = verifier_pos(pos_id, pos_info, articles_par_pos[pos_id], config)
            all_rows.extend(rows)
        except Exception as e:
            log.error("[%s] Erreur inattendue : %s", pos_id, e)

    elapsed = perf_counter() - t0
    json_path, csv_path = save_report(all_rows, src.name, started_at, elapsed)
    return json_path


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Vérifie les points de fidélité articles sur les POS")
    parser.add_argument("--pos", help="POS à vérifier (ex: pos4,pos1). Vide = tous")
    parser.add_argument("--fichier", help="Chemin vers le fichier 999_articlespoints_*.csv (défaut: plus récent)")
    args = parser.parse_args()

    pos_filter = {p.strip() for p in args.pos.split(",") if p.strip()} if args.pos else None
    run(pos_filter=pos_filter, source_file=args.fichier)
