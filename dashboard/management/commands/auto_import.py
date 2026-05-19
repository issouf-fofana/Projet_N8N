from django.core.management.base import BaseCommand
from imports.services import scanner_et_importer_fichiers
from ecarts.services import recalculer_ecarts


class Command(BaseCommand):
    help = "Importe les nouveaux fichiers et recalcule les écarts si nécessaire"

    def handle(self, *args, **options):
        self.stdout.write(">>> Import des fichiers...")
        try:
            fichiers = scanner_et_importer_fichiers()
            self.stdout.write(f"    {len(fichiers)} fichier(s) traité(s)")
        except Exception as e:
            self.stderr.write(f"    Erreur import : {e}")
            return

        if fichiers:
            self.stdout.write(">>> Recalcul des écarts...")
            try:
                result = recalculer_ecarts()
                if isinstance(result, dict):
                    self.stdout.write(f"    Écarts créés : {result.get('ecarts_crees', 0)} | résolus : {result.get('ecarts_resolus', 0)}")
            except Exception as e:
                self.stderr.write(f"    Erreur recalcul : {e}")

        self.stdout.write(self.style.SUCCESS(">>> Terminé."))
