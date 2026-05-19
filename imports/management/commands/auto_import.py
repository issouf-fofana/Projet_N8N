"""
Management command : python manage.py auto_import

Scanne tous les dossiers de dépôt, importe les nouveaux fichiers,
puis recalcule automatiquement les écarts si des fichiers ont été importés.
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
from ecarts.services import recalculer_ecarts


class Command(BaseCommand):
    help = "Importe automatiquement les nouveaux fichiers et recalcule les écarts si nécessaire"

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

        # ── Étape 1 : import des fichiers ────────────────────────────────────
        try:
            fichiers = scanner_et_importer_fichiers()
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"[auto_import] Erreur import : {e}"))
            raise

        duree_import = time.time() - debut

        if fichiers:
            self.stdout.write(
                self.style.SUCCESS(
                    f"[auto_import] {len(fichiers)} fichier(s) importé(s) en {duree_import:.1f}s"
                )
            )
            if verbose:
                for f in fichiers:
                    nom = getattr(f, 'nom_fichier', str(f))
                    typ = getattr(f, 'type_fichier', '')
                    self.stdout.write(f"  ✓ {typ} — {nom}")

            # ── Étape 2 : recalcul des écarts (uniquement si import effectif) ──
            try:
                if verbose:
                    self.stdout.write("[auto_import] Recalcul des écarts…")
                resultat = recalculer_ecarts()
                crees   = resultat.get('ecarts_crees', 0)
                resolus = resultat.get('ecarts_resolus', 0)
                duree_total = time.time() - debut
                self.stdout.write(
                    self.style.SUCCESS(
                        f"[auto_import] Écarts : +{crees} créés, {resolus} résolus "
                        f"(total {duree_total:.1f}s)"
                    )
                )
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"[auto_import] Erreur recalcul écarts : {e}"))
        else:
            if verbose:
                self.stdout.write(f"[auto_import] Aucun nouveau fichier ({duree_import:.1f}s)")
