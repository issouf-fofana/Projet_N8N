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

def purger_db():
    """Supprime en base les anciens ControleConformite (garde le dernier par magasin + rétention).

    Chaque contrôle crée un nouveau ControleConformite avec ses ArticleConformite (CASCADE).
    Sans purge, ils s'accumulent indéfiniment et gonflent la base/l'UI.
    """
    sys.path.insert(0, str(BASE.parent))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "verification_commande.settings")
    import django
    django.setup()

    from django.db.models import Max
    from entree_journal.models import ControleConformite

    # Le dernier contrôle de chaque magasin est toujours conservé, quelle que soit sa date.
    derniers_ids = set(
        ControleConformite.objects
            .values('magasin')
            .annotate(last_id=Max('id'))
            .values_list('last_id', flat=True)
    )

    limite = datetime.now() - timedelta(days=RETENTION)
    a_supprimer = ControleConformite.objects.filter(date_controle__lt=limite).exclude(id__in=derniers_ids)
    nb = a_supprimer.count()
    a_supprimer.delete()
    print(f"  Base de données : {nb} contrôles (+ articles liés) supprimés (>{RETENTION}j, hors dernier par magasin)")


def main():
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Purge — rétention {RETENTION} jours")

    # Rapports de conformité horodatés (conformite_035_20261111_111709.json/.csv)
    purger(OUTPUT,  "conformite_*.json", "rapports JSON")
    purger(OUTPUT,  "conformite_*.csv",  "rapports CSV")

    # Archives des exports POS
    purger(ARCHIVE, "commandable_*.csv", "archives export")

    # Anciens contrôles en base (ControleConformite + ArticleConformite liés)
    try:
        purger_db()
    except Exception as e:
        print(f"  Erreur purge DB : {e}")

    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Purge terminée")

if __name__ == "__main__":
    main()
