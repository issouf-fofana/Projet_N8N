"""
watcher.py — Surveille les dossiers SMB en permanence.
Dès qu'un fichier est déposé, lance la commande Django auto_import
pour importer + recalculer les écarts.
La copie SMB → media/ est gérée séparément par run_auto.service (boucle continue).
"""
import os
import sys
import time
import subprocess
import logging
from pathlib import Path
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

# ── Configuration ──────────────────────────────────────────────
SOURCE_BASE = "/mnt/partage-share"

DOSSIERS_SURVEILLES = [
    f"{SOURCE_BASE}/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE/ASTEN",
    f"{SOURCE_BASE}/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE/CMD CYRUS",
    f"{SOURCE_BASE}/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE/GPV",
    f"{SOURCE_BASE}/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE/LEGEND",
    f"{SOURCE_BASE}/ASTEN/SALAMI/Mdme_Natacha/Vérification_BRS/BRS/BRS_IC",
    f"{SOURCE_BASE}/ASTEN/SALAMI/Mdme_Natacha/Vérification_BRS/BRS/ASTEN/Receptions",
    f"{SOURCE_BASE}/ASTEN/SALAMI/Mdme_Natacha/Vérification_BRS/Anomalies_BR_ASTEN",
    f"{SOURCE_BASE}/ASTEN/SALAMI/Mdme_Natacha/Verification_Factures_ASTEN/Fact_ASTEN",
    f"{SOURCE_BASE}/ASTEN/SALAMI/Mme_Jeannette/Vérification_Factures_GPV/Fact-CYRUS",
]

# Délai d'attente après le dernier événement avant de lancer l'import (secondes)
# Evite de lancer plusieurs fois si plusieurs fichiers arrivent en même temps
DEBOUNCE_SECONDS = 30

SCRIPT_DIR  = Path(__file__).parent
PYTHON      = sys.executable
MANAGE_PY   = SCRIPT_DIR / "manage.py"

# ── Logging ────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHER] %(message)s",
    datefmt="%d/%m/%Y %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(SCRIPT_DIR / "watcher.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)


# ── Handler ────────────────────────────────────────────────────
class NouveauFichierHandler(FileSystemEventHandler):
    def __init__(self):
        self._dernier_evenement = 0
        self._en_cours = False

    def on_created(self, event):
        if event.is_directory:
            return
        # Ignorer les fichiers temporaires
        nom = Path(event.src_path).name
        if nom.startswith('~$') or nom.startswith('.'):
            return
        log.info(f"Nouveau fichier détecté : {event.src_path}")
        self._dernier_evenement = time.time()

    def on_modified(self, event):
        if event.is_directory:
            return
        nom = Path(event.src_path).name
        if nom.startswith('~$') or nom.startswith('.'):
            return
        self._dernier_evenement = time.time()

    def doit_lancer(self):
        """Retourne True si un événement récent attend d'être traité."""
        if self._dernier_evenement == 0:
            return False
        return (time.time() - self._dernier_evenement) >= DEBOUNCE_SECONDS

    def marquer_traite(self):
        self._dernier_evenement = 0


def lancer_import():
    """Lance auto_import (la copie SMB → media est gérée par run_auto.service)."""
    log.info("=" * 50)
    log.info("Lancement de auto_import (Django)...")
    try:
        r = subprocess.run(
            [PYTHON, str(MANAGE_PY), "auto_import"],
            capture_output=True, text=True, timeout=300,
            cwd=str(SCRIPT_DIR)
        )
        log.info(r.stdout.strip() or "(pas de sortie)")
        if r.returncode != 0:
            log.warning(f"auto_import a retourné code {r.returncode} : {r.stderr.strip()}")
    except Exception as e:
        log.error(f"Erreur auto_import : {e}")

    log.info("Import terminé.")
    log.info("=" * 50)


def main():
    handler  = NouveauFichierHandler()
    observer = Observer()

    dossiers_actifs = []
    for dossier in DOSSIERS_SURVEILLES:
        if os.path.exists(dossier):
            observer.schedule(handler, dossier, recursive=False)
            dossiers_actifs.append(dossier)
            log.info(f"Surveillance : {dossier}")
        else:
            log.warning(f"Dossier introuvable (ignoré) : {dossier}")

    if not dossiers_actifs:
        log.error("Aucun dossier accessible. Vérifiez le montage SMB.")
        sys.exit(1)

    observer.start()
    log.info(f"Watcher démarré — {len(dossiers_actifs)} dossier(s) surveillé(s). Délai debounce : {DEBOUNCE_SECONDS}s")

    try:
        while True:
            time.sleep(5)
            if handler.doit_lancer():
                handler.marquer_traite()
                lancer_import()
    except KeyboardInterrupt:
        log.info("Arrêt du watcher.")
        observer.stop()

    observer.join()


if __name__ == "__main__":
    main()
