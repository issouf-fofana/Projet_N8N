import requests
import csv
import os
import time
from datetime import datetime
import urllib3
import json
from json_repair import repair_json
from pathlib import Path
from dotenv import load_dotenv
import logging

load_dotenv()

# =========================
# CONFIGURATION
# =========================

DEFAULT_USERNAME = os.getenv('DEFAULT_USERNAME', 'RAMIAN')
DEFAULT_PASSWORD = os.getenv('DEFAULT_PASSWORD', 'Gk3h34A2UEJ')
POS4_USERNAME = os.getenv('POS4_USERNAME', 'skabore')
POS4_PASSWORD = os.getenv('POS4_PASSWORD', 'xikwWR4z')


base_path = os.getenv('BASE_PATH', r"\\10.0.70.169\share" if os.name == 'nt' else "/mnt/share")
base = Path(base_path)
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", "/mnt/share/ASTEN/SALAMI/ABIBI_MARLILY/RealisationProsuma/VerificationPrixPromo/ASTEN_GPV/PRIX PROMO ASTEN/"))
EXPORT_DIR_LOG = Path(os.getenv("EXPORT_DIR_LOG", "/home/ifofana/logs/promo"))


# ensure directory exists and is writable at start-up
try:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
except Exception as e:
    print(f"⚠️ Impossible de créer le répertoire d'export {EXPORT_DIR} : {e}")

if not os.access(EXPORT_DIR, os.W_OK):
    print(f"⛔ Répertoire d'export non accessible en écriture : {EXPORT_DIR}")
    # the script will still run but writes will fail later

# configure logging (console + file)
log_file = EXPORT_DIR_LOG / "export.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s: %(message)s",
    handlers=[
        logging.FileHandler(log_file, encoding="utf-8"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


POS_LIST = [f"pos{i}-prod-prosuma.prosuma.pos" for i in range(1, 19)]

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# =========================
# CHAMPS API
# =========================

EXPORT_FIELDS = [
    "shop__reference",
    "ean",
    "label_1",
    "selling_price",
    "promo_price",
    "stock",
    "department__code",
    "extras",
    "suppliers",
]

CSV_HEADERS = [
    "magasin",
    "ean",
    "label 1",
    "prix de vente",
    "prix promo",
    "stock",
    "code département",
    "extras",
    "nom fournisseur",
    "code fournisseur",
]

# =========================
# SESSION FACTORY
# =========================

def create_session(pos_host):
    session = requests.Session()

    if pos_host.startswith("pos4-"):
        session.auth = (POS4_USERNAME, POS4_PASSWORD)
    else:
        session.auth = (DEFAULT_USERNAME, DEFAULT_PASSWORD)

    session.verify = False
    return session

# =========================
# EXPORT
# =========================

def export_promo_for_pos(pos_host):

    logger.info(f"🔄 Traitement {pos_host}")

    session = create_session(pos_host)

    base_url = f"https://{pos_host}/api/product/"
    params = {
        "orderable": "true",
        "page_size": 500
    }

    products = []
    page = 1
    retries = 0
    MAX_RETRIES = 10

    while True:
        params["page"] = page
        try:
            r = session.get(base_url, params=params, timeout=None)
        except requests.exceptions.RequestException as e:
            logger.warning(f"⚠️ Erreur réseau {pos_host} page {page} : {e} – re-essai {retries+1}/{MAX_RETRIES}")
            retries += 1
            if retries >= MAX_RETRIES:
                logger.error(f"⛔ Trop d'erreurs réseau sur {pos_host} page {page}, abandon")
                break
            time.sleep(60)
            continue

        if r.status_code == 401:
            logger.error("⛔ Accès refusé (401) – identifiants insuffisants")
            return

        if r.status_code == 404:
            logger.info(f"ℹ️ Fin de pagination pour {pos_host} (page {page})")
            break

        if r.status_code != 200:
            logger.warning(f"⚠️ Erreur API {r.status_code} – page {page} – re-essai {retries+1}/{MAX_RETRIES}")
            retries += 1
            if retries >= MAX_RETRIES:
                logger.error(f"⛔ Trop d'erreurs sur {pos_host} page {page}, abandon")
                break
            time.sleep(60)
            continue

        try:
            data = r.json()
        except (ValueError, requests.exceptions.JSONDecodeError) as e:
            logger.warning(f"⚠️ JSON invalide – {pos_host} page {page} : {e} – re-essai {retries+1}/{MAX_RETRIES}")
            retries += 1
            if retries >= MAX_RETRIES:
                logger.warning(f"⚠️ Tentative de réparation JSON – {pos_host} page {page}")
                try:
                    data = json.loads(repair_json(r.text))
                    logger.info(f"✔ JSON réparé – {pos_host} page {page}")
                    retries = 0
                except Exception as repair_err:
                    logger.error(f"⛔ Réparation impossible – {pos_host} page {page} : {repair_err}")
                    break
            else:
                time.sleep(60)
                continue

        retries = 0  # reset après succès
        results = data.get("results", [])

        if not results:
            break

        products.extend(results)
        total_count = data.get("count", "?")
        final_page  = data.get("final_page", "?")
        logger.info(f"  page {page}/{final_page} — {len(products)}/{total_count} articles chargés")
        page += 1

    logger.info(f"✔ {len(products)} articles commandables récupérés")

    if not products:
        print("⚠️ Aucun article – fichier non généré")
        return

    # Fichier fixe par POS — écrase l'existant pour éviter les doublons
    # On garde aussi une archive horodatée dans le sous-dossier archive/
    filepath = EXPORT_DIR / f"commandable_{pos_host}.csv"
    archive_dir = EXPORT_DIR / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath_archive = archive_dir / f"commandable_{pos_host}_{timestamp}.csv"

    try:
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(CSV_HEADERS)

            for p in products:
                row = []

                for field in EXPORT_FIELDS:
                    if field == "suppliers":
                        continue
                    value = p
                    for part in field.split("__"):
                        value = value.get(part) if isinstance(value, dict) else None
                    row.append(value)

                # Fournisseurs
                suppliers = p.get("suppliers", [])
                names, codes = [], []
                if isinstance(suppliers, list):
                    for s in suppliers:
                        names.append(str(s.get("name", "")))
                        codes.append(str(s.get("code", "")))
                row.append(", ".join(names))
                row.append(", ".join(codes))

                writer.writerow(row)

        logger.info(f"✅ Fichier principal : {filepath}")
        # Copier en archive horodatée
        import shutil
        shutil.copy2(filepath, filepath_archive)
        logger.info(f"   Archive            : {filepath_archive}")
    except PermissionError as e:
        logger.error(f"⛔ Impossible d'écrire le fichier {filepath} : {e}")
        logger.error("Vérifiez les permissions ou modifiez BASE_PATH dans le .env pour un répertoire local.")


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    import sys
    pos_filter = sys.argv[1:] if len(sys.argv) > 1 else POS_LIST
    logger.info(f"Début d'extraction pour : {pos_filter}")
    for pos in pos_filter:
        export_promo_for_pos(pos)
    logger.info("Extraction terminée")
