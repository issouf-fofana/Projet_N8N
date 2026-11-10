import requests
import csv
import os
from datetime import datetime
import urllib3
import json
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


base_path = os.getenv('BASE_PATH', r"\\10.0.70.169\share" if os.name == 'nt' else "/mnt/partage-share")
base = Path(base_path)
EXPORT_DIR = base / "ASTEN" / "SALAMI" / "ABIBI_MARLILY" / "RealisationProsuma" / "VerificationPrixPromo" / "ASTEN_GPV" / "PRIX PROMO ASTEN"
EXPORT_DIR_LOG = base / "ASTEN" / "SALAMI" / "ABIBI_MARLILY" / "RealisationProsuma" / "log_extra_promo"

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
    "id",
    "shop__reference",
    "ean",
    "short_code",
    "label_1",
    "label_2",
    "label_3",
    "selling_price",
    "price_excl_tax",
    "global_price",
    "shop_price",
    "promo_price",
    "selling_unit",
    "input_method",
    "blocked",
    "margin_rate",
    "buying_price",
    "shipping_cost",
    "tare",
    "handle_stock",
    "stock",
    "low_stock_alert",
    "department__code",
    "department__name",
    "product_type",
    "discount_allowed",
    "manual_discount_allowed",
    "hide_linked_products",
    "vat__code",
    "vat__value",
    "is_component",
    "has_linked_products",
    "qualifies_for_ticket_resto",
    "ordering_unit",
    "packaging_unit",
    "packaged_quantity",
    "supplier_reference",
    "country_origin",
    "extras",
    "suppliers",
]

CSV_HEADERS = [
    "id",
    "magasin",
    "ean",
    "code court",
    "label 1",
    "label 2",
    "label 3",
    "prix de vente",
    "prix hors taxes",
    "prix enseigne",
    "prix magasin",
    "prix promo",
    "unité de vente",
    "procédé entrée",
    "bloqué",
    "marge",
    "prix achat H.T.",
    "frais de port",
    "tare",
    "gérer le stock",
    "stock",
    "alerte stock bas à partir de",
    "code département",
    "nom département",
    "type de produit",
    "remise automatique autorisée",
    "remise manuelle autorisée",
    "cacher les articles liés",
    "code T.V.A.",
    "taux T.V.A.",
    "est un composant",
    "a des articles liés",
    "payable en titres restaurant",
    "unité de commande",
    "unité de conditionnement",
    "quantité conditionnée",
    "référence fournisseur",
    "origine",
    "extras",
    "nom fournisseur",
    "code fournisseur",
    "code marketing prosuma",
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
        "has_promo_price": "true",
        "page_size": 500
    }

    products = []
    page = 1

    while True:
        params["page"] = page
        r = session.get(base_url, params=params)

        if r.status_code == 401:
            print("⛔ Accès refusé (401) – identifiants insuffisants")
            return

        if r.status_code != 200:
            print(f"⚠️ Erreur API {r.status_code} – page {page}")
            break

        data = r.json()
        results = data.get("results", [])

        if not results:
            break

        products.extend(results)
        page += 1

    logger.info(f"✔ {len(products)} articles promo récupérés")

    if not products:
        print("⚠️ Aucun article – fichier non généré")
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = EXPORT_DIR / f"promo_{pos_host}_{timestamp}.csv"

    try:
        with open(filepath, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f, delimiter=";")
            writer.writerow(CSV_HEADERS)

            for p in products:
                row = []

                # Champs simples
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

                # Code marketing Prosuma
                extras = p.get("extras", {})
                marketing_code = ""

                if isinstance(extras, dict):
                    marketing_code = extras.get("prosuma_marketing_code", "")
                elif isinstance(extras, str):
                    try:
                        extras_json = json.loads(extras)
                        marketing_code = extras_json.get("prosuma_marketing_code", "")
                    except Exception:
                        pass

                row.append(marketing_code)

                writer.writerow(row)

        logger.info(f"✅ Fichier généré : {filepath}")
    except PermissionError as e:
        logger.error(f"⛔ Impossible d'écrire le fichier {filepath} : {e}")
        logger.error("Vérifiez les permissions ou modifiez BASE_PATH dans le .env pour un répertoire local.")


# =========================
# MAIN
# =========================

if __name__ == "__main__":
    logger.info("Début d'extraction des promos pour tous les POS")
    for pos in POS_LIST:
        export_promo_for_pos(pos)
    logger.info("Extraction terminée")
