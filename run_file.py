import os
import shutil
import argparse
import re
from datetime import datetime

SOURCE_BASE = "/mnt/partage-share"
DEST_BASE = "/opt/Projet_N8N/media"

SOURCES = {
    f"{SOURCE_BASE}/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE/ASTEN": "commande_asten",
    f"{SOURCE_BASE}/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE/CMD CYRUS": "commande_cyrus",
    f"{SOURCE_BASE}/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE/GPV": "commande_gpv",
    f"{SOURCE_BASE}/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE/LEGEND": "commande_legend",

    f"{SOURCE_BASE}/ASTEN/SALAMI/Mdme_Natacha/Vérification_BRS/BRS/BRS_IC": "br_ic",
    f"{SOURCE_BASE}/ASTEN/SALAMI/Mdme_Natacha/Vérification_BRS/BRS/ASTEN/Receptions": "br_asten",

    f"{SOURCE_BASE}/ASTEN/SALAMI/Mdme_Natacha/Verification_Factures_ASTEN/Fact_ASTEN": "facture_asten",
    f"{SOURCE_BASE}/ASTEN/SALAMI/Mdme_Natacha/Verification_Factures_ASTEN/Fac_cyrus": "facture_cyrus",
}

def parse_date(date_str):
    return datetime.strptime(date_str, "%Y-%m-%d")


def format_date(timestamp):
    return datetime.fromtimestamp(timestamp).strftime("%Y%m%d_%H%M%S")


def extract_date_from_filename(filename):
    match = re.search(r'(\d{8}_\d{6})', filename)
    if match:
        return datetime.strptime(match.group(1), "%Y%m%d_%H%M%S")
    return None


def is_in_range(file_time, start, end):
    if start and file_time < start:
        return False
    if end and file_time > end:
        return False
    return True


def copy_csv_files(source_dir, dest_dir, start_date, end_date):

    if not os.path.exists(source_dir):
        print(f"[ERREUR] chemin introuvable: {source_dir}")
        return

    for root, dirs, files in os.walk(source_dir):
        for file in files:
            if file.lower().endswith(".csv"):
                source_file = os.path.join(root, file)

                try:
                    file_date = extract_date_from_filename(file)

                    if file_date:
                        mtime = file_date.timestamp()
                    else:
                        mtime = os.path.getmtime(source_file)
                        file_date = datetime.fromtimestamp(mtime)

                    if not is_in_range(file_date, start_date, end_date):
                        continue

                    date_str = format_date(mtime)
                    name, ext = os.path.splitext(file)

                    new_name = f"{name}_{date_str}{ext}"
                    dest_file = os.path.join(dest_dir, new_name)

                    if os.path.exists(dest_file):
                        print(f"[SKIP] {new_name}")
                        continue

                    shutil.copy2(source_file, dest_file)
                    print(f"[OK] {new_name}")

                except Exception as e:
                    print(f"[ERREUR] {source_file} : {e}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date-debut")
    parser.add_argument("--date-fin")

    args = parser.parse_args()

    start_date = parse_date(args.date_debut) if args.date_debut else None

    if args.date_fin:
        end_date = parse_date(args.date_fin).replace(hour=23, minute=59, second=59)
    else:
        end_date = None

    print(f"Filtre: {start_date} -> {end_date}")

    for source_path, dest_folder in SOURCES.items():
        dest_path = os.path.join(DEST_BASE, dest_folder)
        os.makedirs(dest_path, exist_ok=True)

        print(f"\n=== {source_path} ===")
        copy_csv_files(source_path, dest_path, start_date, end_date)


if __name__ == "__main__":
    main()