"""
Collector — Entrées du journal POS
Se connecte à chaque POS, récupère TOUT /api/journal_entry/ sans filtre,
sauvegarde les données brutes en CSV + log complet.
"""

import csv
import json
import logging
import os
import sys
from datetime import datetime, timezone, timedelta, date
from pathlib import Path
from time import perf_counter

import requests
import urllib3
from dotenv import load_dotenv

# ─── Chemins ────────────────────────────────────────────────────────────────

BASE_DIR   = Path(__file__).parent
DATA_DIR   = BASE_DIR.parent / "media" / "entree_journal"
LOGS_DIR   = BASE_DIR / "logs"
MAGASIN    = BASE_DIR / "magasin.json"

DATA_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# ─── Logger ─────────────────────────────────────────────────────────────────

def setup_logger():
    logger = logging.getLogger("collector")
    logger.setLevel(logging.DEBUG)

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    # Fichier — tout les logs
    fh = logging.FileHandler(LOGS_DIR / "collector.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)

    # Console — INFO et +
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
        "user":         os.getenv("USER_POS",       ""),
        "password":     os.getenv("PASSWORD_POS",   ""),
        "pos4_user":    os.getenv("POS4_USER_POS",  "") or os.getenv("USER_POS", ""),
        "pos4_pass":    os.getenv("POS4_PASSWORD_POS", "") or os.getenv("PASSWORD_POS", ""),
        "verify_tls":   os.getenv("VERIFY_TLS", "false").lower() in ("1", "true", "yes"),
        "page_size":    int(os.getenv("PAGE_SIZE", "500")),
        "timeout":      float(os.getenv("REQUEST_TIMEOUT", "30")),
        "debug":        os.getenv("DEBUG", "false").lower() in ("1", "true", "yes"),
    }

    if not config["user"] or not config["password"]:
        log.error("USER_POS ou PASSWORD_POS manquant dans .env")
        sys.exit(1)

    return config


def load_magasin():
    with open(MAGASIN, encoding="utf-8") as f:
        data = json.load(f)
    log.info(f"Magasin chargé : {len(data)} POS")
    return data

# ─── Session ────────────────────────────────────────────────────────────────

def make_session(pos_id, config):
    session = requests.Session()
    if pos_id == "pos4":
        session.auth = (config["pos4_user"], config["pos4_pass"])
        log.debug(f"[{pos_id}] Credentials POS4 utilisés")
    else:
        session.auth = (config["user"], config["password"])
    session.verify = config["verify_tls"]
    session.headers.update({"Accept": "application/json"})
    return session

# ─── Fetch ──────────────────────────────────────────────────────────────────

def get_window(target_date=None):
    """
    Retourne la fenêtre de collecte Asten :
      J-1 à 20h00 → J à 01h40
    Si target_date est None, J = aujourd'hui.
    """
    j = target_date or date.today()
    j1 = j - timedelta(days=1)
    debut = datetime(j1.year, j1.month, j1.day, 20, 0, 0)
    fin   = datetime(j.year,  j.month,  j.day,  1, 40, 0)
    return debut, fin


def fetch_all_pages(session, url, page_size, timeout, pos_id, date_debut=None, date_fin=None, debug=False):
    """Récupère toutes les pages filtrées sur la fenêtre horaire et entry_type=30."""
    all_results = []
    page = 1
    total_pages = "?"

    while True:
        params = {
            "page":       page,
            "page_size":  page_size,
            "entry_type": 30,
        }
        if date_debut:
            params["created_at_0"] = date_debut.strftime("%Y-%m-%dT%H:%M:%S")
        if date_fin:
            params["created_at_1"] = date_fin.strftime("%Y-%m-%dT%H:%M:%S")
        t0 = perf_counter()
        try:
            resp = session.get(url, params=params, timeout=timeout)
            elapsed = perf_counter() - t0

            log.debug(
                f"[{pos_id}] GET {url} page={page} "
                f"status={resp.status_code} {elapsed:.2f}s"
            )

            if resp.status_code == 404:
                log.warning(f"[{pos_id}] Endpoint 404 — POS peut-être hors ligne")
                break

            if resp.status_code == 401:
                log.error(f"[{pos_id}] Authentification refusée (401)")
                break

            if resp.status_code == 403:
                log.error(f"[{pos_id}] Accès interdit (403)")
                break

            if resp.status_code != 200:
                log.error(f"[{pos_id}] Erreur HTTP {resp.status_code}")
                log.debug(f"[{pos_id}] Réponse brute: {resp.text[:500]}")
                break

            data = resp.json()

            # Log de la structure reçue (première page seulement)
            if page == 1:
                keys = list(data.keys()) if isinstance(data, dict) else "liste"
                total = data.get("count", "?") if isinstance(data, dict) else len(data)
                total_pages = (
                    -(-int(total) // page_size) if isinstance(total, int) else "?"
                )
                log.info(
                    f"[{pos_id}] Réponse : {total} entrées totales "
                    f"(~{total_pages} pages) | clés: {keys}"
                )

            # Support DRF paginé {"count":N, "results":[...]}
            if isinstance(data, dict) and "results" in data:
                results = data["results"]
                log.debug(
                    f"[{pos_id}] Page {page}/{total_pages} : "
                    f"{len(results)} entrées"
                )
                if not results:
                    break
                all_results.extend(results)

                # Pas de page suivante
                if not data.get("next"):
                    break

            # Réponse directe (liste)
            elif isinstance(data, list):
                log.debug(f"[{pos_id}] Page {page} : {len(data)} entrées (liste directe)")
                if not data:
                    break
                all_results.extend(data)
                break  # Pas de pagination sur une liste brute

            else:
                log.warning(f"[{pos_id}] Format de réponse inattendu: {type(data)}")
                log.debug(f"[{pos_id}] Contenu: {str(data)[:500]}")
                all_results.append(data)
                break

        except requests.exceptions.ConnectTimeout:
            log.error(f"[{pos_id}] Timeout connexion ({timeout}s)")
            break
        except requests.exceptions.ReadTimeout:
            log.error(f"[{pos_id}] Timeout lecture ({timeout}s)")
            break
        except requests.exceptions.ConnectionError as e:
            log.error(f"[{pos_id}] Erreur connexion: {e}")
            break
        except requests.exceptions.SSLError as e:
            log.error(f"[{pos_id}] Erreur SSL: {e}")
            break
        except json.JSONDecodeError as e:
            log.error(f"[{pos_id}] Réponse non-JSON: {e}")
            log.debug(f"[{pos_id}] Contenu brut: {resp.text[:500]}")
            break
        except Exception as e:
            log.error(f"[{pos_id}] Erreur inattendue: {type(e).__name__}: {e}")
            break

        page += 1

    return all_results

# ─── Sauvegarde ─────────────────────────────────────────────────────────────

# Colonnes fixes du CSV (ordre stable)
CSV_FIELDNAMES = [
    "pos_id",
    "pos_name",
    "collected_at",
    "id",
    "created_at",
    "updated_at",
    "entry_type_value",
    "entry_type_label",
    "entry_type_detail_type",
    "entry_type_detail_text",
    "status_value",
    "status_label",
    "username",
    "report",
    "shop_reference",
    "shop_name",
]


def _parse_json_field(raw):
    """Parse un champ JSON string ou dict en dict Python."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip().startswith("{"):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
    return {}


def flatten_entry(pos_id, pos_name, collected_at, entry):
    """
    Aplatit une entrée API en une ligne CSV avec des colonnes lisibles.
    Les champs JSON imbriqués (entry_type, entry_type_detail, status, shop)
    sont décomposés en colonnes séparées.
    """
    et  = _parse_json_field(entry.get("entry_type", {}))
    etd = _parse_json_field(entry.get("entry_type_detail", {}))
    st  = _parse_json_field(entry.get("status", {}))
    sh  = _parse_json_field(entry.get("shop", {}))

    # report : supprimer les sauts de ligne pour avoir une cellule propre
    report_raw = entry.get("report") or ""
    report = " | ".join(line.strip() for line in str(report_raw).splitlines() if line.strip())

    return {
        "pos_id":                  pos_id,
        "pos_name":                pos_name,
        "collected_at":            collected_at.isoformat(),
        "id":                      entry.get("id", ""),
        "created_at":              entry.get("created_at", ""),
        "updated_at":              entry.get("updated_at", ""),
        "entry_type_value":        et.get("value", ""),
        "entry_type_label":        et.get("display_name", ""),
        "entry_type_detail_type":  etd.get("type", ""),
        "entry_type_detail_text":  etd.get("display_text", ""),
        "status_value":            st.get("value", ""),
        "status_label":            st.get("display_name", ""),
        "username":                entry.get("username", ""),
        "report":                  report,
        "shop_reference":          sh.get("reference", ""),
        "shop_name":               sh.get("name", ""),
    }


def save_data(pos_id, pos_name, entries, collected_at):
    """
    Sauvegarde les entrées journal en CSV avec colonnes aplaties.
    - Séparateur : point-virgule (compatible Excel/LibreOffice)
    - Encodage   : UTF-8 avec BOM (compatible Excel)
    - Colonnes   : voir CSV_FIELDNAMES
    """
    DATA_DIR.mkdir(exist_ok=True)

    filename = f"{pos_id}_{collected_at.strftime('%Y-%m-%d_%H-%M-%S')}.csv"
    filepath = DATA_DIR / filename

    if not entries:
        log.warning(f"[{pos_id}] Aucune entrée à sauvegarder")
        return filepath

    with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, delimiter=";", extrasaction="ignore")
        writer.writeheader()
        for entry in entries:
            if isinstance(entry, dict):
                row = flatten_entry(pos_id, pos_name, collected_at, entry)
            else:
                row = {k: "" for k in CSV_FIELDNAMES}
                row["pos_id"] = pos_id
                row["pos_name"] = pos_name
                row["id"] = str(entry)
            writer.writerow(row)

    log.info(f"[{pos_id}] CSV sauvegardé : {filepath} ({len(entries)} lignes, {len(CSV_FIELDNAMES)} colonnes)")
    return filepath

# ─── Collect un POS ─────────────────────────────────────────────────────────

def collect_pos(pos_id, pos_info, config, date_debut=None, date_fin=None):
    pos_name = pos_info.get("name", pos_id)
    base_url  = pos_info.get("url", "").rstrip("/")

    if not base_url:
        log.error(f"[{pos_id}] URL manquante dans magasin.json")
        return {"pos_id": pos_id, "status": "error", "error": "URL manquante", "count": 0}

    endpoint = f"{base_url}/api/journal_entry/"
    log.info(
        f"[{pos_id}] ({pos_name}) Connexion → {endpoint} "
        f"| fenêtre {date_debut.strftime('%d/%m %Hh%M') if date_debut else '?'} "
        f"→ {date_fin.strftime('%d/%m %Hh%M') if date_fin else '?'}"
    )

    session = make_session(pos_id, config)
    collected_at = datetime.now(timezone.utc)

    entries = fetch_all_pages(
        session,
        endpoint,
        config["page_size"],
        config["timeout"],
        pos_id,
        date_debut=date_debut,
        date_fin=date_fin,
        debug=config["debug"],
    )

    if entries:
        filepath = save_data(pos_id, pos_name, entries, collected_at)
        return {
            "pos_id":   pos_id,
            "pos_name": pos_name,
            "status":   "ok",
            "count":    len(entries),
            "file":     str(filepath),
            "collected_at": collected_at.isoformat(),
        }
    else:
        log.warning(f"[{pos_id}] Aucune entrée récupérée")
        return {
            "pos_id":   pos_id,
            "pos_name": pos_name,
            "status":   "empty",
            "count":    0,
            "collected_at": collected_at.isoformat(),
        }

# ─── Run complet ────────────────────────────────────────────────────────────

def run_collection(pos_filter=None, target_date=None, date_debut_explicit=None, date_fin_explicit=None):
    """
    Lance la collecte sur tous les POS (ou un sous-ensemble).
    - Si date_debut_explicit et date_fin_explicit sont fournis, utilise ces dates directement.
    - Sinon, utilise la fenêtre Asten : J-1 20h00 → J 01h40.
    """
    config  = load_config()
    magasin = load_magasin()

    if not config["verify_tls"]:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    if date_debut_explicit and date_fin_explicit:
        date_debut, date_fin = date_debut_explicit, date_fin_explicit
        log.info(f"═══ Fenêtre personnalisée : {date_debut} → {date_fin} ═══")
    else:
        date_debut, date_fin = get_window(target_date)
    log.info(
        f"═══ Collecte démarrée : fenêtre "
        f"{date_debut.strftime('%d/%m/%Y %H:%M')} → {date_fin.strftime('%d/%m/%Y %H:%M')} ═══"
    )

    targets = (
        {k: v for k, v in magasin.items() if k in pos_filter}
        if pos_filter else magasin
    )

    log.info(f"═══ {len(targets)} POS ciblés ═══")
    t_global = perf_counter()
    results = []

    for pos_id, pos_info in targets.items():
        result = collect_pos(pos_id, pos_info, config, date_debut=date_debut, date_fin=date_fin)
        results.append(result)

    elapsed = perf_counter() - t_global
    ok    = sum(1 for r in results if r["status"] == "ok")
    empty = sum(1 for r in results if r["status"] == "empty")
    err   = sum(1 for r in results if r["status"] == "error")
    total = sum(r["count"] for r in results)

    log.info(
        f"═══ Collecte terminée en {elapsed:.1f}s — "
        f"OK:{ok} | Vide:{empty} | Erreur:{err} | "
        f"Total entrées: {total} ═══"
    )

    # Rapport JSON dans logs/
    report_path = LOGS_DIR / f"report_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(
            {"started_at": datetime.now().isoformat(), "elapsed_s": round(elapsed, 2),
             "results": results},
            f, ensure_ascii=False, indent=2, default=str
        )
    log.info(f"Rapport sauvegardé : {report_path}")

    return results


# ─── Entry point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Collecteur journal POS")
    parser.add_argument("--pos", nargs="*", help="POS à collecter (ex: pos1 pos3). Défaut: tous")
    parser.add_argument("--date-debut", dest="date_debut", help="Date/heure début (YYYY-MM-DDTHH:MM:SS ou YYYY-MM-DD)")
    parser.add_argument("--date-fin",   dest="date_fin",   help="Date/heure fin   (YYYY-MM-DDTHH:MM:SS ou YYYY-MM-DD)")
    args = parser.parse_args()

    # Dates explicites fournies en argument
    date_debut_explicit = None
    date_fin_explicit   = None

    if args.date_debut:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                date_debut_explicit = datetime.strptime(args.date_debut, fmt)
                break
            except ValueError:
                pass
        if date_debut_explicit is None:
            log.error(f"Format date-debut invalide : {args.date_debut}  (attendu YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS)")
            sys.exit(1)

    if args.date_fin:
        for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                date_fin_explicit = datetime.strptime(args.date_fin, fmt)
                break
            except ValueError:
                pass
        if date_fin_explicit is None:
            log.error(f"Format date-fin invalide : {args.date_fin}  (attendu YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS)")
            sys.exit(1)

    run_collection(
        pos_filter=set(args.pos) if args.pos else None,
        date_debut_explicit=date_debut_explicit,
        date_fin_explicit=date_fin_explicit,
    )
