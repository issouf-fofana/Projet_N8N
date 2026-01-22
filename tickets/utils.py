import json
from pathlib import Path
from django.conf import settings
from .models import Technicien


def charger_techniciens_si_vide():
    if Technicien.objects.exists():
        return
    fichier = Path(settings.BASE_DIR) / "techniciens.json"
    if not fichier.exists():
        return
    data = json.loads(fichier.read_text(encoding="utf-8"))
    noms = data.get("techniciens", [])
    for nom in noms:
        nom = (nom or "").strip()
        if nom:
            Technicien.objects.get_or_create(nom=nom)



