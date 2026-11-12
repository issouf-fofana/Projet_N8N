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

def controler_un(code, counter, total, vg_cache=""):
    result = subprocess.run(
        [sys.executable, str(CONTROLE_SCRIPT), code, "--vg-cache", vg_cache],
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

def precalculer_vg():
    """Lit VG_VERIF_PPROMO2 une seule fois et écrit les refs dans un fichier cache."""
    import csv, os
    from pathlib import Path
    from dotenv import load_dotenv
    load_dotenv(BASE / ".env")
    smb = Path(os.getenv(
        "SMB_BACKUP_PATH",
        "/run/user/1000/gvfs/smb-share:server=10.0.70.104,share=d/"
        "Cegid/AstenProd/ReferentielPrecom/Backup"
    ))
    vg_dir = smb / "VG_VERIF_PPROMO2"
    cache_path = BASE / "output" / "_vg_refs_cache.txt"
    if not vg_dir.exists():
        log(f"  ⚠️  VG_VERIF_PPROMO2 introuvable — Absent CEGID non filtré")
        cache_path.write_text("")
        return str(cache_path)
    refs = set()
    for fichier in sorted(vg_dir.glob("*_products_*.csv")):
        with open(fichier, encoding="utf-8", errors="replace") as f:
            for row in csv.reader(f, delimiter=";"):
                if len(row) >= 5:
                    code_art = row[4].strip()
                    if code_art:
                        refs.add(code_art)
    cache_path.write_text("\n".join(sorted(refs)), encoding="utf-8")
    log(f"  VG cache : {len(refs)} références écrites dans {cache_path.name}")
    return str(cache_path)


def main():
    parser = argparse.ArgumentParser(description="Contrôle conformité tous magasins")
    parser.add_argument("--workers", type=int, default=8,
                        help="Nombre de magasins traités en parallèle (défaut: 8)")
    args = parser.parse_args()

    magasins = tous_les_magasins()
    total = len(magasins)
    workers = min(args.workers, total)

    log(f"[{datetime.now():%Y-%m-%d %H:%M}] Contrôle lancé pour {total} magasins ({workers} en parallèle)")

    # Précalculer VG une seule fois avant de lancer les subprocesses
    log("  Chargement VG_VERIF_PPROMO2…")
    vg_cache = precalculer_vg()

    counter = {"done": 0, "ok": 0, "ko": 0}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(controler_un, code, counter, total, vg_cache): code for code in magasins}
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
