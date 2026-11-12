import json
import os
import threading
import time
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

LOGS_DIR   = Path(__file__).parent.parent / "Entree_journal" / "logs"
PROGRESS_F = LOGS_DIR / "verif_progress.json"

SMB_BACKUP = Path(
    os.environ.get(
        "SMB_BACKUP_PATH",
        "/run/user/1000/gvfs/smb-share:server=10.0.70.104,share=d/"
        "Cegid/AstenProd/ReferentielPrecom/Backup"
    )
)

# ─── état global de la vérification en cours ────────────────────────────────
_verif_lock   = threading.Lock()
_verif_thread = None


def _is_running():
    with _verif_lock:
        if _verif_thread is not None and _verif_thread.is_alive():
            return True
    # Fallback : vérifier le fichier de progression (au cas où le process a redémarré)
    if PROGRESS_F.exists():
        try:
            d = json.loads(PROGRESS_F.read_text(encoding="utf-8"))
            return bool(d.get("running"))
        except Exception:
            pass
    return False


def _run_in_thread(pos_filter, source_file):
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent / "Entree_journal"))
    import importlib
    import verif_points as vp
    importlib.reload(vp)   # au cas où le module était déjà chargé
    vp.run(pos_filter=pos_filter, source_file=source_file)


def _list_articlespoints_files():
    files = sorted(SMB_BACKUP.glob("999_articlespoints_*.csv"), reverse=True)
    if not files:
        sous = SMB_BACKUP / "articlespoints"
        if sous.exists():
            files = sorted(sous.glob("999_articlespoints_*.csv"), reverse=True)
    return [f.name for f in files[:20]]


def _list_rapports():
    if not LOGS_DIR.exists():
        return []
    rapports = sorted(LOGS_DIR.glob("verif_points_*.json"), reverse=True)
    result = []
    for p in rapports[:20]:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            result.append({
                "filename":   p.name,
                "started_at": data.get("started_at", ""),
                "elapsed_s":  data.get("elapsed_s", ""),
                "source":     data.get("source_file", ""),
                "total":      data.get("total", 0),
                "ok":         data.get("ok", 0),
                "ecart":      data.get("ecart", 0),
                "absent":     data.get("absent", 0),
            })
        except Exception:
            result.append({"filename": p.name, "error": True})
    return result


def get_dernier_rapport():
    """Retourne le dernier rapport verif_points (pour le dashboard)."""
    if not LOGS_DIR.exists():
        return None
    rapports = sorted(LOGS_DIR.glob("verif_points_*.json"), reverse=True)
    if not rapports:
        return None
    try:
        with open(rapports[0], encoding="utf-8") as f:
            data = json.load(f)
        data["filename"] = rapports[0].name
        return data
    except Exception:
        return None


# ─── vues ────────────────────────────────────────────────────────────────────

@login_required
def verif_points(request):
    rapports = _list_rapports()
    context = {
        "fichiers_dispo":  _list_articlespoints_files(),
        "rapports":        rapports,
        "dernier":         rapports[0] if rapports else None,
        "smb_accessible":  SMB_BACKUP.exists(),
        "verif_en_cours":  _is_running(),
    }
    return render(request, "entree_journal/verif_points.html", context)


@login_required
@require_POST
def lancer_verif_points(request):
    global _verif_thread

    if _is_running():
        return JsonResponse({"error": "Une vérification est déjà en cours."}, status=409)

    pos_filter_raw = request.POST.get("pos", "").strip()
    fichier        = request.POST.get("fichier", "").strip()
    pos_filter = {p.strip() for p in pos_filter_raw.split(",") if p.strip()} if pos_filter_raw else None

    source_file = None
    if fichier:
        candidates = [SMB_BACKUP / fichier, SMB_BACKUP / "articlespoints" / fichier]
        found = next((p for p in candidates if p.exists()), None)
        if not found:
            return JsonResponse({"error": f"Fichier introuvable : {fichier}"}, status=400)
        source_file = str(found)

    with _verif_lock:
        t = threading.Thread(target=_run_in_thread, args=(pos_filter, source_file), daemon=True)
        _verif_thread = t
        t.start()

    return JsonResponse({"status": "started"})


@login_required
def progression_verif_points(request):
    """
    SSE : envoie l'état de progression toutes les 2 secondes.
    Se termine quand running=False dans le fichier de progression.
    """
    def event_stream():
        last_data = None
        idle = 0
        while True:
            time.sleep(2)
            try:
                if PROGRESS_F.exists():
                    raw = PROGRESS_F.read_text(encoding="utf-8")
                    if raw != last_data:
                        last_data = raw
                        idle = 0
                        yield f"data: {raw}\n\n"
                    else:
                        idle += 1
                    # Arrêter le stream si terminé et idle depuis 10s
                    try:
                        state = json.loads(raw)
                        if not state.get("running") and state.get("finished"):
                            yield f"data: {raw}\n\n"
                            return
                    except Exception:
                        pass
                else:
                    # Pas encore de fichier → heartbeat
                    yield "data: {\"running\": false, \"waiting\": true}\n\n"
                    idle += 1

                if idle > 150:   # 5 min sans changement → fermer
                    return
            except Exception:
                yield "data: {\"error\": true}\n\n"
                return

    response = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response


@login_required
def statut_verif_points(request):
    """État rapide (polling JSON) pour vérifier si une vérif tourne."""
    state = {"running": _is_running()}
    if PROGRESS_F.exists():
        try:
            state.update(json.loads(PROGRESS_F.read_text(encoding="utf-8")))
        except Exception:
            pass
    return JsonResponse(state)


@login_required
def rapport_verif_points(request, filename):
    if ".." in filename or "/" in filename:
        return JsonResponse({"error": "Nom invalide"}, status=400)
    path = LOGS_DIR / filename
    if not path.exists():
        return JsonResponse({"error": "Rapport introuvable"}, status=404)
    with open(path, encoding="utf-8") as f:
        return JsonResponse(json.load(f))


# ─── Article commandable ─────────────────────────────────────────────────────

CONTROLE_DIR = Path(__file__).parent.parent / "Article_commandable" / "output"
CONTROLE_SCRIPT = Path(__file__).parent.parent / "Article_commandable" / "controler_conformite.py"
EXPORT_SCRIPT   = Path(__file__).parent.parent / "Article_commandable" / "export_commandable.py"
CONTROLE_PROGRESS_F = CONTROLE_DIR / "controle_progress.json"

_controle_lock   = threading.Lock()
_controle_thread = None


def _controle_running():
    with _controle_lock:
        if _controle_thread is not None and _controle_thread.is_alive():
            return True
    if CONTROLE_PROGRESS_F.exists():
        try:
            d = json.loads(CONTROLE_PROGRESS_F.read_text(encoding="utf-8"))
            return bool(d.get("running"))
        except Exception:
            pass
    return False


def _run_controle(magasin_codes):
    """Lance export puis contrôle pour chaque magasin."""
    import subprocess, sys
    codes = magasin_codes if isinstance(magasin_codes, list) else [magasin_codes]
    total = len(codes)

    CONTROLE_DIR.mkdir(parents=True, exist_ok=True)
    CONTROLE_PROGRESS_F.write_text(
        json.dumps({"running": True, "etape": "démarrage", "done": 0, "total": total}),
        encoding="utf-8"
    )

    try:
        for i, code in enumerate(codes):
            CONTROLE_PROGRESS_F.write_text(
                json.dumps({"running": True, "etape": f"contrôle magasin {code}", "done": i, "total": total, "magasin": code}),
                encoding="utf-8"
            )
            subprocess.run(
                [sys.executable, str(CONTROLE_SCRIPT), code],
                cwd=str(CONTROLE_SCRIPT.parent),
            )
    finally:
        CONTROLE_PROGRESS_F.write_text(
            json.dumps({"running": False, "finished": True, "done": total, "total": total}),
            encoding="utf-8"
        )


def _list_controle_rapports():
    if not CONTROLE_DIR.exists():
        return []
    rapports = sorted(CONTROLE_DIR.glob("conformite_*.json"), reverse=True)
    result = []
    for p in rapports[:20]:
        try:
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            result.append({
                "filename":        p.name,
                "magasin":         data.get("magasin", ""),
                "date":            data.get("date", ""),
                "total":           data.get("total", 0),
                "ok":              data.get("ok", 0),
                "ko":              data.get("ko", 0),
                "taux_conformite": data.get("taux_conformite", 0),
            })
        except Exception:
            result.append({"filename": p.name, "error": True})
    return result


def _get_magasins():
    import json as _json
    p = Path(__file__).parent.parent / "assortiments_magasins.json"
    if not p.exists():
        return []
    with open(p, encoding="utf-8") as f:
        data = _json.load(f)
    magasins = []
    for backup_code, liste in data.items():
        for m in liste:
            magasins.append({"code": m["code_magasin"], "nom": m["nom_magasin"]})
    return sorted(magasins, key=lambda x: x["code"])


def _get_magasin_to_backup():
    p = Path(__file__).parent.parent / "assortiments_magasins.json"
    if not p.exists():
        return {}
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    mapping = {}
    for backup_code, magasins in data.items():
        for m in magasins:
            mapping[m["code_magasin"]] = backup_code
    return mapping


@login_required
def article_commandable(request):
    rapports = _list_controle_rapports()
    # Permet d'ouvrir directement un rapport depuis le dashboard (?rapport=filename)
    rapport_param = request.GET.get("rapport", "")
    dernier = next((r for r in rapports if r["filename"] == rapport_param), None) if rapport_param else None
    if dernier is None:
        dernier = rapports[0] if rapports else None
    context = {
        "rapports":          rapports,
        "dernier":           dernier,
        "controle_en_cours": _controle_running(),
        "magasins":          _get_magasins(),
        "magasin_to_backup": json.dumps(_get_magasin_to_backup()),
    }
    return render(request, "entree_journal/article_commandable.html", context)


@login_required
@require_POST
def lancer_controle(request):
    global _controle_thread

    if _controle_running():
        return JsonResponse({"error": "Un contrôle est déjà en cours."}, status=409)

    magasin_raw = request.POST.get("magasin", "").strip()
    if not magasin_raw:
        return JsonResponse({"error": "Code magasin manquant."}, status=400)

    codes = [c.strip() for c in magasin_raw.split(",") if c.strip()]

    with _controle_lock:
        t = threading.Thread(target=_run_controle, args=(codes,), daemon=True)
        _controle_thread = t
        t.start()

    return JsonResponse({"status": "started", "magasins": codes})


@login_required
def statut_controle(request):
    """Polling JSON — état en cours du contrôle."""
    state = {"running": _controle_running()}
    if CONTROLE_PROGRESS_F.exists():
        try:
            state.update(json.loads(CONTROLE_PROGRESS_F.read_text(encoding="utf-8")))
        except Exception:
            pass
    return JsonResponse(state)


@login_required
def cegid_products(request, backup_prefix):
    """Retourne les articles du fichier Product CEGID (backup) en JSON paginé."""
    import csv as _csv
    if not backup_prefix.isdigit() or len(backup_prefix) != 3:
        return JsonResponse({"error": "Préfixe invalide"}, status=400)

    # Cherche d'abord dans prdP2A_* (backup quotidien), puis dans VG_VERIF_PPROMO2
    fichier = None
    backup_dir_name = None
    for candidate_dir in [
        *sorted(SMB_BACKUP.glob("prdP2A_*"), reverse=True),
        SMB_BACKUP / "VG_VERIF_PPROMO2",
    ]:
        if not candidate_dir.exists():
            continue
        pattern = list(candidate_dir.glob(f"{backup_prefix}_products_*.csv"))
        if pattern:
            fichier = sorted(pattern)[-1]
            backup_dir_name = candidate_dir.name
            break

    if not fichier:
        return JsonResponse({"error": f"Fichier {backup_prefix}_products_*.csv introuvable dans les backups"}, status=404)

    articles = []
    with open(fichier, encoding="utf-8", errors="replace") as f:
        for row in _csv.reader(f, delimiter=";"):
            if len(row) < 16:
                continue
            code_art = row[4].strip()
            if not code_art:
                continue
            articles.append({
                "deref":             row[0].strip() == "D",
                "code_interne":      row[1].strip(),
                "code_article":      code_art,
                "num_assortiment":   row[3].strip(),
                "qte_cond":          row[5].strip(),
                "libelle_long":      row[6].strip(),
                "libelle_court":     row[7].strip(),
                "statut":            row[10].strip(),
                "tva":               row[12].strip(),
                "prix_vente":        row[13].strip(),
                "prix_achat":        row[14].strip(),
                "code_mak":          row[15].strip(),
            })

    n_deref = sum(1 for a in articles if a["deref"])
    return JsonResponse({
        "backup_dir": backup_dir_name,
        "fichier": fichier.name,
        "total": len(articles),
        "n_deref": n_deref,
        "articles": articles,
    })


@login_required
def rapport_controle(request, filename):
    if ".." in filename or "/" in filename:
        return JsonResponse({"error": "Nom invalide"}, status=400)
    path = CONTROLE_DIR / filename
    if not path.exists():
        return JsonResponse({"error": "Rapport introuvable"}, status=404)
    with open(path, encoding="utf-8") as f:
        return JsonResponse(json.load(f))


# ─── Vue tâches planifiées ────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent.parent

# Définition des tâches (chemins relatifs à BASE_DIR)
TACHES_DEF = [
    {
        "id":          "export_pos",
        "nom":         "Export articles commandables (POS)",
        "description": "Télécharge les articles commandables depuis chaque POS",
        "cron":        "0 2 * * *",
        "heure":       "02h00",
        "log":         BASE_DIR / "Article_commandable" / "logs" / "cron_export.log",
        "icone":       "bi-cloud-download",
        "couleur":     "#6b4c35",
        "mots_ok":     ["Extraction terminée", "terminée"],
        "mots_ko":     ["ERREUR", "⛔", "Traceback"],
    },
    {
        "id":          "controle_conformite",
        "nom":         "Contrôle conformité articles",
        "description": "Compare les articles POS avec le référentiel Asten",
        "cron":        "0 4 * * *",
        "heure":       "04h00",
        "log":         BASE_DIR / "Article_commandable" / "logs" / "cron_controle.log",
        "icone":       "bi-clipboard-check",
        "couleur":     "#10b981",
        "mots_ok":     ["Terminé —"],
        "mots_ko":     ["ERREUR", "Traceback"],
    },
    {
        "id":          "verif_points",
        "nom":         "Vérification points fidélité",
        "description": "Compare les points entre Asten et le référentiel",
        "cron":        "0 6 * * *",
        "heure":       "06h00",
        "log":         BASE_DIR / "Entree_journal" / "logs" / "verif_points.log",
        "icone":       "bi-stars",
        "couleur":     "#f59e0b",
        "mots_ok":     ["terminée", "Terminé"],
        "mots_ko":     ["ERREUR", "Traceback", "erreur"],
    },
    {
        "id":          "purge",
        "nom":         "Purge anciens rapports",
        "description": "Supprime les rapports de plus de 30 jours",
        "cron":        "0 5 * * *",
        "heure":       "05h00",
        "log":         BASE_DIR / "Article_commandable" / "logs" / "cron_purge.log",
        "icone":       "bi-trash",
        "couleur":     "#6b7280",
        "mots_ok":     ["Purge terminée"],
        "mots_ko":     ["ERREUR", "Traceback"],
    },
    {
        "id":          "collecte_journal",
        "nom":         "Collecte entrées journal POS",
        "description": "Collecte les entrées journal depuis les POS",
        "cron":        "—",
        "heure":       "Manuel",
        "log":         BASE_DIR / "Entree_journal" / "logs" / "collector.log",
        "icone":       "bi-journal-arrow-down",
        "couleur":     "#8b5cf6",
        "mots_ok":     ["Collecte terminée", "terminée"],
        "mots_ko":     ["ERREUR", "Traceback", "Erreur"],
    },
]


def _lire_statut_log(tache):
    """Lit les dernières lignes du log et détermine le statut."""
    log_path = tache["log"]
    if not log_path.exists():
        return {"statut": "inconnu", "derniere_ligne": "Aucun log trouvé", "derniere_exec": None, "nb_lignes": 0}

    try:
        # Lire les 100 dernières lignes
        with open(log_path, encoding="utf-8", errors="replace") as f:
            lignes = f.readlines()
        dernieres = lignes[-100:] if len(lignes) > 100 else lignes
        nb_lignes = len(lignes)

        if not dernieres:
            return {"statut": "inconnu", "derniere_ligne": "Log vide", "derniere_exec": None, "nb_lignes": 0}

        derniere = dernieres[-1].strip()

        # Chercher la date de dernière exécution (format ISO dans les logs)
        import re
        derniere_exec = None
        for ligne in reversed(dernieres):
            m = re.search(r'(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2})', ligne)
            if m:
                derniere_exec = m.group(1)
                break

        # Déterminer le statut
        statut = "inconnu"
        for mot in tache["mots_ok"]:
            if any(mot in l for l in dernieres[-5:]):
                statut = "ok"
                break
        if statut != "ok":
            for mot in tache["mots_ko"]:
                if any(mot in l for l in dernieres[-20:]):
                    statut = "erreur"
                    break

        # Chercher résumé (ex: "51 OK, 1 erreurs")
        resume = None
        for ligne in reversed(dernieres[-10:]):
            if "OK" in ligne and ("erreur" in ligne.lower() or "terminé" in ligne.lower()):
                resume = ligne.strip()
                break

        return {
            "statut": statut,
            "derniere_ligne": derniere,
            "derniere_exec": derniere_exec,
            "nb_lignes": nb_lignes,
            "resume": resume,
            "log_path": str(log_path),
        }
    except Exception as e:
        return {"statut": "erreur", "derniere_ligne": str(e), "derniere_exec": None, "nb_lignes": 0}


@login_required
def taches_planifiees(request):
    """Vue tableau de bord des tâches planifiées."""
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        # Retourne le statut JSON pour le rafraîchissement auto
        taches = []
        for t in TACHES_DEF:
            statut = _lire_statut_log(t)
            taches.append({
                "id":           t["id"],
                "statut":       statut["statut"],
                "derniere_exec": statut.get("derniere_exec"),
                "resume":       statut.get("resume"),
                "derniere_ligne": statut.get("derniere_ligne", "")[:200],
            })
        return JsonResponse({"taches": taches})

    taches = []
    for t in TACHES_DEF:
        statut = _lire_statut_log(t)
        taches.append({**t, **statut, "log": str(t["log"])})

    return render(request, "entree_journal/taches_planifiees.html", {"taches": taches})
