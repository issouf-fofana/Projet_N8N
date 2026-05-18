"""
run_auto.py — Surveillance continue des dossiers SMB → copie dans media/

Lance ce script une fois, il tourne en boucle et copie dès qu'un nouveau
fichier apparaît dans les dossiers source. Intervalle par défaut : 2 minutes.

Usage :
    python run_auto.py           # intervalle 2 min
    python run_auto.py --once    # une seule passe puis quitte
    python run_auto.py --interval 5  # toutes les 5 minutes
"""

import os
import sys
import shutil
import re
import argparse
import time
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

# Ne copier que les fichiers modifiés dans les N derniers jours (première passe incluse)
JOURS_MAX = 7

SOURCE_BASE = "/mnt/partage-share"

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
    f"{SOURCE_BASE}/ASTEN/SALAMI/Mme_Jeannette/Vérification_Factures_GPV/Fact-CYRUS": "facture_cyrus",
}

EXTENSIONS = {
    "br_ic": (".csv", ".xlsx"),
}

# Ces dossiers sont toujours recopiés (pas de filtre par nouveauté)
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


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)


def get_known_files(dest_dir, extensions):
    """Retourne l'ensemble des noms de fichiers déjà présents dans dest."""
    if not os.path.isdir(dest_dir):
        return set()
    return {
        f for f in os.listdir(dest_dir)
        if f.lower().endswith(extensions)
    }


DATE_IN_NAME = re.compile(r'(\d{8})_\d{6}')


def get_file_date(filename, mtime):
    """
    Extrait la date depuis le nom du fichier (ex: _20260518_141111).
    Si absente, utilise le mtime. Le SMB peut fausser le mtime — le nom est fiable.
    """
    m = DATE_IN_NAME.search(filename)
    if m:
        try:
            return datetime.strptime(m.group(1), "%Y%m%d")
        except ValueError:
            pass
    return datetime.fromtimestamp(mtime)


def scan_source(source_dir, dest_dir, folder_name, known_files):
    """
    Parcourt source_dir et copie dans dest_dir tout fichier
    - dont la date (dans le nom ou mtime) est dans les JOURS_MAX derniers jours
    - pas encore présent dans known_files (ou COPY_ALWAYS)
    Retourne le nombre de fichiers copiés.
    """
    if not os.path.isdir(source_dir):
        return 0

    extensions = EXTENSIONS.get(folder_name, (".csv",))
    always = folder_name in COPY_ALWAYS
    cutoff = datetime.now() - timedelta(days=JOURS_MAX)
    copied = 0

    for root, _, files in os.walk(source_dir):
        for file in files:
            if not file.lower().endswith(extensions):
                continue

            source_file = os.path.join(root, file)

            try:
                mtime = os.path.getmtime(source_file)

                if always:
                    dest_file = os.path.join(dest_dir, file)
                    if not os.path.exists(dest_file) or mtime > os.path.getmtime(dest_file):
                        shutil.copy2(source_file, dest_file)
                        log(f"  [COPY] {folder_name}/{file}")
                        copied += 1
                    continue

                # Date fiable = depuis le nom du fichier
                file_date = get_file_date(file, mtime)

                # Ignorer les fichiers trop anciens
                if file_date < cutoff:
                    continue

                # Nom destination = nom original (la date est déjà dedans)
                # Si pas de date dans le nom, ajouter le mtime
                if DATE_IN_NAME.search(file):
                    dest_name = file
                else:
                    date_str = datetime.fromtimestamp(mtime).strftime("%Y%m%d_%H%M%S")
                    name, ext = os.path.splitext(file)
                    dest_name = f"{name}_{date_str}{ext}"

                if dest_name in known_files:
                    continue  # déjà copié

                dest_file = os.path.join(dest_dir, dest_name)
                shutil.copy2(source_file, dest_file)
                known_files.add(dest_name)
                log(f"  [NOUVEAU] {folder_name}/{dest_name}")
                copied += 1

            except Exception as e:
                log(f"  [ERREUR] {source_file} : {e}")

    return copied


def deduplicate(dest_dir, folder_name):
    pattern = DEDUP_PATTERNS.get(folder_name)
    if pattern is None:
        return

    extensions = EXTENSIONS.get(folder_name, (".csv",))
    groups = defaultdict(list)

    for f in os.listdir(dest_dir):
        if not f.lower().endswith(extensions):
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
            try:
                os.remove(os.path.join(dest_dir, f))
                deleted += 1
            except Exception:
                pass

    if deleted:
        log(f"  [{folder_name}] {deleted} doublon(s) supprimé(s)")


def run_once(known_files_cache):
    """Une passe de scan sur tous les dossiers source."""
    total_new = 0

    for source_path, folder_name in SOURCES.items():
        dest_path = os.path.join(DEST_BASE, folder_name)
        os.makedirs(dest_path, exist_ok=True)

        extensions = EXTENSIONS.get(folder_name, (".csv",))

        # Initialiser le cache pour ce dossier si première passe
        if folder_name not in known_files_cache:
            known_files_cache[folder_name] = get_known_files(dest_path, extensions)

        copied = scan_source(
            source_path, dest_path, folder_name,
            known_files_cache[folder_name]
        )
        if copied:
            deduplicate(dest_path, folder_name)
            total_new += copied

    return total_new


def main():
    parser = argparse.ArgumentParser(description="Surveillance continue SMB → media/")
    parser.add_argument('--once', action='store_true', help='Une seule passe puis quitter')
    parser.add_argument('--interval', type=int, default=2, help='Intervalle en minutes (défaut: 2)')
    args = parser.parse_args()

    interval_sec = args.interval * 60

    log("=" * 55)
    log("  Surveillance démarrée")
    log(f"  Source     : {SOURCE_BASE}")
    log(f"  Destination: {DEST_BASE}")
    if not args.once:
        log(f"  Intervalle : toutes les {args.interval} minute(s)")
    log("=" * 55)

    # Cache des fichiers connus par dossier (évite de relire le disque à chaque passe)
    known_files_cache = {}

    if args.once:
        total = run_once(known_files_cache)
        log(f"Terminé — {total} nouveau(x) fichier(s) copié(s).")
        return

    # Boucle infinie
    try:
        while True:
            total = run_once(known_files_cache)
            if total:
                log(f"✓ {total} nouveau(x) fichier(s) copié(s)")
            else:
                log("Aucun nouveau fichier.")
            time.sleep(interval_sec)
    except KeyboardInterrupt:
        log("Arrêt demandé (Ctrl+C).")


if __name__ == "__main__":
    main()
