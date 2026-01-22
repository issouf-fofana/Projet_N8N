import json
from pathlib import Path
from django.core.management.base import BaseCommand
from tickets.models import Technicien


class Command(BaseCommand):
    help = "Crée les techniciens depuis techniciens.json"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default="techniciens.json",
            help="Chemin vers techniciens.json (par défaut à la racine du projet)",
        )

    def handle(self, *args, **options):
        file_path = Path(options["file"])
        if not file_path.is_absolute():
            file_path = Path.cwd() / file_path
        if not file_path.exists():
            self.stderr.write(self.style.ERROR(f"Fichier introuvable: {file_path}"))
            return

        data = json.loads(file_path.read_text(encoding="utf-8"))
        noms = data.get("techniciens", [])
        created = 0
        for nom in noms:
            technicien, was_created = Technicien.objects.get_or_create(nom=nom.strip())
            if was_created:
                created += 1
        self.stdout.write(self.style.SUCCESS(f"Techniciens créés: {created}"))

