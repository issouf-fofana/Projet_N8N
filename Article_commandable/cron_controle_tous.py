"""
Contrôle de conformité automatique — tous les magasins.
Lancé par cron après export_commandable.py.

Usage:
    python cron_controle_tous.py               # 8 workers par défaut
    python cron_controle_tous.py --workers 4   # limiter à 4 workers
    python cron_controle_tous.py --workers 1   # séquentiel (ancien comportement)
"""
import json
import subprocess
import sys
import threading
import argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = Path(__file__).parent
ASSORT_JSON = BASE.parent / "assortiments_magasins.json"
CONTROLE_SCRIPT = BASE / "controler_conformite.py"
LOG_DIR = BASE / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# Verrou pour les prints concurrents
_print_lock = threading.Lock()

def log(msg):
    with _print_lock:
        print(msg, flush=True)

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

def controler_un(code, counter, total):
    result = subprocess.run(
        [sys.executable, str(CONTROLE_SCRIPT), code],
        cwd=str(BASE),
        capture_output=True, text=True
    )
    with _print_lock:
        n = counter["done"] + 1
        counter["done"] = n
        if result.returncode == 0:
            counter["ok"] += 1
            print(f"  [{n}/{total}] ✓ Magasin {code}", flush=True)
        else:
            counter["ko"] += 1
            err = result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "erreur inconnue"
            print(f"  [{n}/{total}] ✗ Magasin {code} — {err[:120]}", flush=True)
    return result.returncode == 0

def main():
    parser = argparse.ArgumentParser(description="Contrôle conformité tous magasins")
    parser.add_argument("--workers", type=int, default=8,
                        help="Nombre de magasins traités en parallèle (défaut: 8)")
    args = parser.parse_args()

    magasins = tous_les_magasins()
    total = len(magasins)
    workers = min(args.workers, total)

    log(f"[{datetime.now():%Y-%m-%d %H:%M}] Contrôle lancé pour {total} magasins ({workers} en parallèle)")

    counter = {"done": 0, "ok": 0, "ko": 0}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(controler_un, code, counter, total): code for code in magasins}
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                code = futures[future]
                log(f"  ✗ Magasin {code} — exception : {e}")
                with _print_lock:
                    counter["ko"] += 1

    log(f"[{datetime.now():%Y-%m-%d %H:%M}] Terminé — {counter['ok']} OK, {counter['ko']} erreurs")

if __name__ == "__main__":
    main()
