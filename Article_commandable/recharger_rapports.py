"""Recharge en base le dernier rapport JSON de conformité de chaque magasin.

Utile quand la base a été vidée/réinitialisée mais que les rapports JSON
existent toujours dans output/.
"""
import json
import os
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output"

sys.path.insert(0, str(BASE_DIR))
from controler_conformite import _sauvegarder_en_base  # noqa: E402

import django
root = BASE_DIR.parent
if str(root) not in sys.path:
    sys.path.insert(0, str(root))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "verification_commande.settings")
django.setup()


def main():
    fichiers = list(OUTPUT_DIR.glob("conformite_*.json"))
    par_magasin = {}
    for f in fichiers:
        m = re.match(r"conformite_(\w+)_(\d{8}_\d{6})\.json", f.name)
        if not m:
            continue
        magasin, ts = m.group(1), m.group(2)
        if magasin not in par_magasin or ts > par_magasin[magasin][1]:
            par_magasin[magasin] = (f, ts)

    print(f"{len(par_magasin)} magasins à recharger")
    for magasin, (f, ts) in sorted(par_magasin.items()):
        with open(f, encoding="utf-8") as fh:
            rapport = json.load(fh)
        rows = rapport["all_rows"]
        backup_prefix = rows[0]["num_assortiment"] if rows else ""
        _sauvegarder_en_base(
            magasin, backup_prefix, ts,
            rapport["total"], rapport["ok"], rapport["ko"], rapport["taux_conformite"],
            f.name.replace(".json", ".json"), rows, mode_merge=False,
        )


if __name__ == "__main__":
    main()
