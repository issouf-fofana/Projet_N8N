"""
One-shot : rétrofit l'affichage des articles corrigés AVANT le fix du
28/11 (corrige=True enregistré, mais present_asten/resultat/motif jamais
mis à jour — restaient figés avec l'ancien statut KO).

Déduit present_asten depuis action_correction déjà enregistré :
  "orderable=True, blocked=False"  -> present_asten=True  (rendu commandable)
  "orderable=False"                -> present_asten=False (rendu non commandable)

Usage :
    python retrofit_corrections.py          # dry-run (affiche ce qui serait fait)
    python retrofit_corrections.py --apply  # applique réellement
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
root = BASE_DIR.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "verification_commande.settings")
import django
django.setup()

from entree_journal.models import ArticleConformite


def main():
    apply_ = "--apply" in sys.argv

    qs = ArticleConformite.objects.filter(corrige=True).exclude(resultat="OK")
    total = qs.count()
    print(f"{total} article(s) corrigé(s) avec un affichage pas encore à jour")

    maj = []
    ignores = []
    for art in qs:
        action = art.action_correction or ""
        if "orderable=True" in action:
            present_asten = True
        elif "orderable=False" in action:
            present_asten = False
        else:
            ignores.append(art)
            continue
        art.present_asten = present_asten
        art.resultat = "OK"
        art.motif = f"Corrigé — {action}"
        maj.append(art)

    print(f"  → {len(maj)} à mettre à jour, {len(ignores)} ignorés (action_correction vide/inattendue)")

    if not apply_:
        print("Dry-run — aucune écriture. Relancer avec --apply pour appliquer.")
        for art in maj[:20]:
            print(f"  [{art.controle.magasin}] {art.reference} → present_asten={art.present_asten}, motif='{art.motif}'")
        if len(maj) > 20:
            print(f"  ... et {len(maj) - 20} autres")
        return

    ArticleConformite.objects.bulk_update(maj, ["present_asten", "resultat", "motif"], batch_size=500)
    print(f"✅ {len(maj)} article(s) mis à jour en base")


if __name__ == "__main__":
    main()
