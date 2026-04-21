"""
run_auto.py — Copie automatique : hier 00:00 → aujourd'hui 23:59
Lance ce script sans argument, il calcule les dates tout seul.
"""
import os
import shutil
import re
from datetime import datetime, timedelta
from collections import defaultdict

SOURCE_BASE = "/mnt/partage-share"

# Détection automatique du chemin selon la machine
_script_dir = os.path.dirname(os.path.abspath(__file__))
DEST_BASE = os.path.join(_script_dir, "media")

SOURCES = {
    f"{SOURCE_BASE}/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE/ASTEN": "commande_asten",
    f"{SOURCE_BASE}/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE/CMD CYRUS": "commande_cyrus",
    f"{SOURCE_BASE}/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE/GPV": "commande_gpv",
    f"{SOURCE_BASE}/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE/LEGEND": "commande_legend",

    f"{SOURCE_BASE}/ASTEN/SALAMI/Mdme_Natacha/Vérification_BRS/BRS/BRS_IC": "br_ic",
    f"{SOURCE_BASE}/ASTEN/SALAMI/Mdme_Natacha/Vérification_BRS/BRS/ASTEN/Receptions": "br_asten",
    f"{SOURCE_BASE}/ASTEN/SALAMI/Mdme_Natacha/Vérification_BRS/Anomalies_BR_ASTEN": "anomalie_br",

    f"{SOURCE_BASE}/ASTEN/SALAMI/Mdme_Natacha/Verification_Factures_ASTEN/Fact_ASTEN": "facture_asten",
    f"{SOURCE_BASE}/ASTEN/SALAMI/Mdme_Natacha/Verification_Factures_ASTEN/Fac_cyrus": "facture_cyrus",
}

# Extensions acceptées par dossier (par défaut : csv uniquement)
EXTENSIONS = {
    "br_ic": (".csv", ".xlsx"),
}

# Dossiers dont le fichier doit être copié sans filtre de date (toujours à jour)
COPY_ALWAYS = {"anomalie_br"}

DEDUP_PATTERNS = {
    "commande_asten":  re.compile(r'^export_commande_reassort_(\d+)_(\d{8})_(\d{6})'),
    "commande_cyrus":  None,
    "commande_gpv":    None,
    "commande_legend": None,
    "br_ic":           re.compile(r'^(.+?)_(\d{8})_(\d{6})'),
    "br_asten":        re.compile(r'^(.+?)_(\d{8})_(\d{6})'),
    "facture_asten":   re.compile(r'^(.+?)_(\d{8})_(\d{6})'),
    "facture_cyrus":   None,
    "anomalie_br":     None,
}


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


def copy_files(source_dir, dest_dir, start_date, end_date, folder_name=""):
    if not os.path.exists(source_dir):
        print(f"  [ERREUR] chemin introuvable: {source_dir}")
        return

    extensions = EXTENSIONS.get(folder_name, (".csv",))
    always = folder_name in COPY_ALWAYS
    copied = 0
    skipped = 0

    for root, dirs, files in os.walk(source_dir):
        for file in files:
            if not file.lower().endswith(extensions):
                continue
            source_file = os.path.join(root, file)
            try:
                if always:
                    # Copier sans filtre de date, écraser l'existant
                    dest_file = os.path.join(dest_dir, file)
                    shutil.copy2(source_file, dest_file)
                    copied += 1
                    print(f"  [OK] {file}")
                    continue

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
                    skipped += 1
                    continue

                shutil.copy2(source_file, dest_file)
                copied += 1
                print(f"  [OK] {file}")

            except Exception as e:
                print(f"  [ERREUR] {source_file} : {e}")

    print(f"  → {copied} copié(s), {skipped} déjà présent(s)")


def deduplicate(dest_dir, folder_name):
    pattern = DEDUP_PATTERNS.get(folder_name)
    if pattern is None:
        return

    groups = defaultdict(list)
    for f in os.listdir(dest_dir):
        if not f.lower().endswith(".csv"):
            continue
        m = pattern.match(f)
        if m:
            key   = (m.group(1), m.group(2))
            heure = m.group(3)
            groups[key].append((heure, f))

    deleted = 0
    for key, files in groups.items():
        files.sort(reverse=True)
        for _, f in files[1:]:
            os.remove(os.path.join(dest_dir, f))
            deleted += 1

    if deleted:
        print(f"  → {deleted} doublon(s) supprimé(s)")


def main():
    aujourd_hui = datetime.now().replace(hour=23, minute=59, second=59, microsecond=0)
    hier        = (datetime.now() - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    print("=" * 55)
    print("   Copie automatique — Hier → Aujourd'hui")
    print("=" * 55)
    print(f"  Période    : {hier.strftime('%d/%m/%Y')} → {aujourd_hui.strftime('%d/%m/%Y')}")
    print(f"  Destination: {DEST_BASE}")
    print()

    for source_path, dest_folder in SOURCES.items():
        dest_path = os.path.join(DEST_BASE, dest_folder)
        os.makedirs(dest_path, exist_ok=True)
        print(f"─── {dest_folder} ───")
        copy_files(source_path, dest_path, hier, aujourd_hui, dest_folder)
        deduplicate(dest_path, dest_folder)
        print()

    print("✓ Terminé.")


if __name__ == "__main__":
    main()
