"""
Management command : python manage.py auto_import

Scanne tous les dossiers de dépôt et importe automatiquement
les nouveaux fichiers en base de données.
Conçu pour être lancé par un cron toutes les 5 minutes.

Fichiers traités :
  - Commandes Asten / Cyrus / GPV / Legend
  - BR Asten
  - BR IC  → désormais stocké ligne par ligne dans BRICLigne
  - Factures Cyrus / Asten (CSV bruts)
  - Entrée Journal (POS)
"""
import time
from django.core.management.base import BaseCommand
from imports.services import scanner_et_importer_fichiers


class Command(BaseCommand):
    help = "Importe automatiquement les nouveaux fichiers déposés dans les dossiers surveillés"

    def add_arguments(self, parser):
        parser.add_argument(
            '--verbose', action='store_true',
            help='Affiche le détail des fichiers importés'
        )

    def handle(self, *args, **options):
        verbose = options['verbose']
        debut = time.time()

        if verbose:
            self.stdout.write("[auto_import] Démarrage du scan…")

        try:
            fichiers = scanner_et_importer_fichiers()
            duree = time.time() - debut

            if fichiers:
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[auto_import] {len(fichiers)} fichier(s) importé(s) en {duree:.1f}s"
                    )
                )
                if verbose:
                    for f in fichiers:
                        nom = getattr(f, 'nom_fichier', str(f))
                        typ = getattr(f, 'type_fichier', '')
                        self.stdout.write(f"  ✓ {typ} — {nom}")
            else:
                if verbose:
                    self.stdout.write(f"[auto_import] Aucun nouveau fichier ({duree:.1f}s)")

        except Exception as e:
            self.stderr.write(self.style.ERROR(f"[auto_import] Erreur : {e}"))
            raise
