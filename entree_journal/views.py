import json
import subprocess
import sys
from pathlib import Path

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

LOGS_DIR     = Path(__file__).parent.parent / "Entree_journal" / "logs"
VERIF_SCRIPT = Path(__file__).parent.parent / "Entree_journal" / "verif_points.py"

SMB_BACKUP = Path(
    "/run/user/1000/gvfs/smb-share:server=10.0.70.104,share=d/"
    "Cegid/AstenProd/ReferentielPrecom/Backup"
)


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
    """Retourne le dernier rapport verif_points sous forme de dict (pour le dashboard)."""
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


@login_required
def verif_points(request):
    rapports = _list_rapports()
    dernier  = rapports[0] if rapports else None
    context = {
        "fichiers_dispo": _list_articlespoints_files(),
        "rapports":       rapports,
        "dernier":        dernier,
        "smb_accessible": SMB_BACKUP.exists(),
    }
    return render(request, "entree_journal/verif_points.html", context)


@login_required
@require_POST
def lancer_verif_points(request):
    pos_filter = request.POST.get("pos", "").strip()
    fichier    = request.POST.get("fichier", "").strip()

    cmd = [sys.executable, str(VERIF_SCRIPT)]
    if pos_filter:
        cmd += ["--pos", pos_filter]
    if fichier:
        candidates = [SMB_BACKUP / fichier, SMB_BACKUP / "articlespoints" / fichier]
        found = next((str(p) for p in candidates if p.exists()), None)
        if not found:
            return JsonResponse({"error": f"Fichier introuvable : {fichier}"}, status=400)
        cmd += ["--fichier", found]

    try:
        subprocess.Popen(cmd, cwd=str(VERIF_SCRIPT.parent))
        return JsonResponse({"status": "started"})
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


@login_required
def rapport_verif_points(request, filename):
    if ".." in filename or "/" in filename:
        return JsonResponse({"error": "Nom invalide"}, status=400)
    path = LOGS_DIR / filename
    if not path.exists():
        return JsonResponse({"error": "Rapport introuvable"}, status=404)
    with open(path, encoding="utf-8") as f:
        return JsonResponse(json.load(f))
