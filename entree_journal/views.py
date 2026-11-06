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
