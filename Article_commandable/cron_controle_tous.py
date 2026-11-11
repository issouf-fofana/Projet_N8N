"""
Contrôle de conformité automatique — tous les magasins.
Lancé par cron après export_commandable.py.
"""
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent
ASSORT_JSON = BASE.parent / "assortiments_magasins.json"
CONTROLE_SCRIPT = BASE / "controler_conformite.py"
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

def tous_les_magasins():
    if not ASSORT_JSON.exists():
        return []
    with open(ASSORT_JSON, encoding="utf-8") as f:
        data = json.load(f)
    codes = []
    for magasins in data.values():
        for m in magasins:
            codes.append(m["code_magasin"])
    return sorted(set(codes))

def main():
    magasins = tous_les_magasins()
    total = len(magasins)
    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Contrôle lancé pour {total} magasins")

    ok, ko = 0, 0
    for i, code in enumerate(magasins, 1):
        print(f"  [{i}/{total}] Magasin {code}…", flush=True)
        result = subprocess.run(
            [sys.executable, str(CONTROLE_SCRIPT), code],
            cwd=str(BASE),
            capture_output=True, text=True
        )
        if result.returncode == 0:
            ok += 1
        else:
            ko += 1
            print(f"    ERREUR : {result.stderr[:200]}")

    print(f"[{datetime.now():%Y-%m-%d %H:%M}] Terminé — {ok} OK, {ko} erreurs")

if __name__ == "__main__":
    main()
