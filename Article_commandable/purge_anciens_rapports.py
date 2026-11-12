"""
Purge des anciens fichiers dans output/ — gardé les 30 derniers jours.
Lancé par cron après le contrôle de conformité.
"""
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta

BASE      = Path(__file__).parent
OUTPUT    = Path(os.getenv("EXPORT_DIR", BASE / "output"))
ARCHIVE   = OUTPUT / "archive"
RETENTION = int(os.getenv("PURGE_RETENTION_DAYS", "30"))

def purger(dossier, pattern, label):
    if not dossier.exists():
        return
    limite = datetime.now() - timedelta(days=RETENTION)
    supprimes = 0
    for f in dossier.glob(pattern):
        if datetime.fromtimestamp(f.stat().st_mtime) < limite:
            f.unlink()
            supprimes += 1
    print(f"  {label} : {supprimes} fichiers supprimés (>{RETENTION}j)")

def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Purge — rétention {RETENTION} jours")

    # Rapports de conformité horodatés (conformite_035_20261111_111709.json/.csv)
    purger(OUTPUT,  "conformite_*.json", "rapports JSON")
    purger(OUTPUT,  "conformite_*.csv",  "rapports CSV")

    # Archives des exports POS
    purger(ARCHIVE, "commandable_*.csv", "archives export")

    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Purge terminée")

if __name__ == "__main__":
    main()
