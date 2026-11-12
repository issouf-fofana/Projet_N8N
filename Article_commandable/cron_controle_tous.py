"""
Contrôle de conformité automatique — tous les magasins.
Lancé par cron après export_commandable.py.

Usage:
    python cron_controle_tous.py               # 8 workers par défaut
    python cron_controle_tous.py --workers 4   # limiter à 4 workers
    python cron_controle_tous.py --workers 1   # séquentiel (ancien comportement)
    python cron_controle_tous.py --wait-export # attendre la fin de l'export avant de démarrer
"""
import json
import subprocess
import sys
import threading
import argparse
import time
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

def controler_un(code, counter, total, vg_cache="", backup_dir=""):
    cmd = [sys.executable, str(CONTROLE_SCRIPT), code, "--vg-cache", vg_cache]
    if backup_dir:
        cmd += ["--backup-dir", backup_dir]
    result = subprocess.run(
        cmd,
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

def attendre_fin_export(timeout_h=6):
    """
    Attend que tous les fichiers commandable_pos*.csv soient stables
    (mtime inchangé pendant 2 minutes) avant de démarrer le contrôle.
    Timeout après timeout_h heures.
    """
    output_dir = BASE / "output"
    debut = time.time()
    timeout_s = timeout_h * 3600
    intervalle = 120  # vérifier toutes les 2 minutes

    log(f"  Attente fin export (timeout {timeout_h}h)…")
    while True:
        fichiers = list(output_dir.glob("commandable_pos*.csv"))
        # Exclure les fichiers horodatés (archives)
        fichiers = [f for f in fichiers if not __import__('re').search(r'_\d{8}_\d{6}\.csv$', f.name)]

        if not fichiers:
            log("  ⚠️  Aucun fichier export trouvé — démarrage sans attente")
            return

        maintenant = time.time()
        en_cours = [f for f in fichiers if maintenant - f.stat().st_mtime < intervalle]

        if not en_cours:
            ages = [(maintenant - f.stat().st_mtime) / 60 for f in fichiers]
            log(f"  Export terminé — {len(fichiers)} fichiers POS stables (dernier modifié il y a {min(ages):.0f} min)")
            return

        if maintenant - debut > timeout_s:
            log(f"  ⚠️  Timeout {timeout_h}h dépassé — démarrage forcé ({len(en_cours)} fichiers encore en cours)")
            return

        noms = ", ".join(f.stem.replace("commandable_", "").replace("-prod-prosuma.prosuma.pos", "") for f in en_cours[:5])
        log(f"  Export en cours ({len(en_cours)} POS actifs: {noms}…) — nouvelle vérification dans {intervalle//60} min")
        time.sleep(intervalle)


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
    parser.add_argument("--backup-dir", default="",
                        help="Forcer un dossier backup spécifique (ex: prdP2A_2026-11-11_23h00m01s)")
    parser.add_argument("--purge-db", action="store_true",
                        help="Supprimer tous les contrôles en base avant de recommencer")
    parser.add_argument("--wait-export", action="store_true",
                        help="Attendre la fin de l'export (fichiers stables) avant de démarrer")
    parser.add_argument("--wait-timeout", type=int, default=6,
                        help="Timeout en heures pour --wait-export (défaut: 6)")
    args = parser.parse_args()

    magasins = tous_les_magasins()
    total = len(magasins)
    workers = min(args.workers, total)

    log(f"[{datetime.now():%Y-%m-%d %H:%M}] Contrôle lancé pour {total} magasins ({workers} en parallèle)")
    if args.backup_dir:
        log(f"  Backup forcé : {args.backup_dir}")

    # Attendre la fin de l'export si demandé
    if args.wait_export:
        attendre_fin_export(timeout_h=args.wait_timeout)

    # Purge DB si demandé
    if args.purge_db:
        log("  Purge de tous les contrôles en base…")
        import os as _os, sys as _sys
        _root = Path(__file__).resolve().parent.parent
        if str(_root) not in _sys.path:
            _sys.path.insert(0, str(_root))
        _os.environ.setdefault("DJANGO_SETTINGS_MODULE", "verification_commande.settings")
        import django
        django.setup()
        from entree_journal.models import ControleConformite
        n, _ = ControleConformite.objects.all().delete()
        log(f"  → {n} enregistrements supprimés")

    # Précalculer VG une seule fois avant de lancer les subprocesses
    log("  Chargement VG_VERIF_PPROMO2…")
    vg_cache = precalculer_vg()

    counter = {"done": 0, "ok": 0, "ko": 0}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(controler_un, code, counter, total, vg_cache, args.backup_dir): code for code in magasins}
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
