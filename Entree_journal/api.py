"""
API FastAPI — Entrées du journal POS
Expose les fichiers collectés + déclenche une collecte à la demande.
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException, Query
from fastapi.responses import JSONResponse, FileResponse
from dotenv import load_dotenv

# ─── Chemins ────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
LOGS_DIR = BASE_DIR / "logs"
MAGASIN  = BASE_DIR / "magasin.json"

DATA_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

load_dotenv(BASE_DIR / ".env")

# ─── App ────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Entrées Journal POS",
    description="API de collecte et consultation des entrées journal POS Prosuma",
    version="1.0.0",
)

log = logging.getLogger("uvicorn.error")

# ─── Helpers ────────────────────────────────────────────────────────────────

def load_magasin():
    if not MAGASIN.exists():
        return {}
    with open(MAGASIN, encoding="utf-8") as f:
        return json.load(f)


def list_pos_files(pos_id: str):
    files = sorted(DATA_DIR.glob(f"{pos_id}_*.csv"), reverse=True)
    return files


def read_csv_file(path: Path):
    """Lit un CSV et retourne une liste de dicts avec colonnes aplaties."""
    import csv as _csv
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = _csv.DictReader(f, delimiter=";")
        for row in reader:
            # Convertir les valeurs numériques connues
            if row.get("entry_type_value") not in ("", None):
                try:
                    row["entry_type_value"] = int(row["entry_type_value"])
                except (ValueError, TypeError):
                    pass
            if row.get("status_value") not in ("", None):
                try:
                    row["status_value"] = int(row["status_value"])
                except (ValueError, TypeError):
                    pass
            rows.append(dict(row))
    meta = {}
    if rows:
        meta = {
            "pos_id":       rows[0].get("pos_id", ""),
            "pos_name":     rows[0].get("pos_name", ""),
            "collected_at": rows[0].get("collected_at", ""),
            "count":        len(rows),
        }
    return {**meta, "entries": rows}


def get_latest_file(pos_id: str):
    files = list_pos_files(pos_id)
    if not files:
        return None
    return files[0]

# ─── État de la collecte en cours ────────────────────────────────────────────

_collect_status = {"running": False, "last_run": None, "last_results": None}


def run_background_collection(pos_filter=None):
    from collector import run_collection
    _collect_status["running"] = True
    _collect_status["last_run"] = datetime.now().isoformat()
    try:
        results = run_collection(pos_filter=pos_filter)
        _collect_status["last_results"] = results
    except Exception as e:
        log.error(f"Erreur collecte: {e}")
        _collect_status["last_results"] = [{"error": str(e)}]
    finally:
        _collect_status["running"] = False


# ─── Routes ─────────────────────────────────────────────────────────────────

@app.get("/", tags=["Info"])
def root():
    """État général de l'API."""
    magasin = load_magasin()
    pos_summary = {}
    for pos_id in magasin:
        files = list_pos_files(pos_id)
        latest = files[0] if files else None
        pos_summary[pos_id] = {
            "name":         magasin[pos_id].get("name"),
            "files_count":  len(files),
            "latest_file":  latest.name if latest else None,
            "latest_at":    (
                read_csv_file(latest).get("collected_at")
                if latest else None
            ),
        }
    return {
        "service":      "Entrées Journal POS",
        "pos_count":    len(magasin),
        "collect_status": _collect_status,
        "pos":          pos_summary,
    }


@app.post("/collect", tags=["Collecte"])
def trigger_collect(
    background_tasks: BackgroundTasks,
    pos: Optional[str] = Query(None, description="POS à collecter, ex: pos1,pos3. Vide = tous"),
):
    """
    Déclenche une collecte en arrière-plan.
    - `pos` : liste séparée par virgules (ex: `pos1,pos2`) ou vide pour tout collecter.
    """
    if _collect_status["running"]:
        raise HTTPException(409, "Une collecte est déjà en cours")

    pos_filter = (
        {p.strip() for p in pos.split(",") if p.strip()}
        if pos else None
    )

    background_tasks.add_task(run_background_collection, pos_filter)
    return {
        "status":  "started",
        "targets": list(pos_filter) if pos_filter else "tous",
        "message": "Collecte démarrée en arrière-plan. Consulter /collect/status pour l'avancement.",
    }


@app.get("/collect/status", tags=["Collecte"])
def collect_status():
    """État de la dernière collecte."""
    return _collect_status


@app.get("/pos", tags=["Données"])
def list_pos():
    """Liste tous les POS avec leur dernier fichier collecté."""
    magasin = load_magasin()
    result = []
    for pos_id, info in magasin.items():
        files = list_pos_files(pos_id)
        latest = files[0] if files else None
        latest_data = read_csv_file(latest) if latest else {}
        result.append({
            "pos_id":       pos_id,
            "name":         info.get("name"),
            "url":          info.get("url"),
            "files_count":  len(files),
            "latest_file":  latest.name if latest else None,
            "latest_count": latest_data.get("count", 0),
            "collected_at": latest_data.get("collected_at"),
        })
    return {"pos": result, "total": len(result)}


@app.get("/pos/{pos_id}", tags=["Données"])
def get_pos_latest(pos_id: str):
    """
    Retourne les données du dernier fichier collecté pour un POS.
    Contient TOUTES les entrées brutes telles que récupérées depuis l'API.
    """
    magasin = load_magasin()
    if pos_id not in magasin:
        raise HTTPException(404, f"POS '{pos_id}' inconnu")

    latest = get_latest_file(pos_id)
    if not latest:
        raise HTTPException(404, f"Aucune donnée collectée pour {pos_id}")

    return read_csv_file(latest)


@app.get("/pos/{pos_id}/files", tags=["Données"])
def list_pos_files_endpoint(pos_id: str):
    """Liste tous les fichiers collectés pour un POS (du plus récent au plus ancien)."""
    magasin = load_magasin()
    if pos_id not in magasin:
        raise HTTPException(404, f"POS '{pos_id}' inconnu")

    files = list_pos_files(pos_id)
    result = []
    for f in files:
        try:
            data = read_csv_file(f)
            result.append({
                "filename":     f.name,
                "collected_at": data.get("collected_at"),
                "count":        data.get("count", 0),
                "size_kb":      round(f.stat().st_size / 1024, 1),
            })
        except Exception:
            result.append({"filename": f.name, "error": "lecture impossible"})

    return {"pos_id": pos_id, "files": result, "total": len(result)}


@app.get("/pos/{pos_id}/files/{filename}", tags=["Données"])
def get_pos_file(pos_id: str, filename: str):
    """Récupère un fichier spécifique pour un POS."""
    if ".." in filename or "/" in filename:
        raise HTTPException(400, "Nom de fichier invalide")

    filepath = DATA_DIR / filename
    if not filepath.exists():
        raise HTTPException(404, f"Fichier '{filename}' introuvable pour {pos_id}")

    return read_csv_file(filepath)


@app.get("/all", tags=["Données"])
def get_all_latest(
    with_entries: bool = Query(False, description="Inclure les entrées brutes (peut être volumineux)")
):
    """
    Retourne le dernier fichier collecté pour TOUS les POS.
    Par défaut, n'inclut que les métadonnées (count, collected_at).
    Ajouter ?with_entries=true pour inclure toutes les entrées.
    """
    magasin = load_magasin()
    result = []
    for pos_id in magasin:
        latest = get_latest_file(pos_id)
        if not latest:
            result.append({"pos_id": pos_id, "status": "no_data"})
            continue
        data = read_csv_file(latest)
        if not with_entries:
            data.pop("entries", None)
        result.append(data)

    return {"collected": result, "total_pos": len(result)}


@app.get("/pos/{pos_id}/files/{filename}/download", tags=["Données"])
def download_csv(pos_id: str, filename: str):
    """Télécharge le fichier CSV brut directement."""
    if ".." in filename or "/" in filename:
        raise HTTPException(400, "Nom de fichier invalide")
    filepath = DATA_DIR / filename
    if not filepath.exists():
        raise HTTPException(404, f"Fichier '{filename}' introuvable pour {pos_id}")
    return FileResponse(
        path=str(filepath),
        media_type="text/csv",
        filename=filename,
    )


@app.get("/pos/{pos_id}/latest/download", tags=["Données"])
def download_latest_csv(pos_id: str):
    """Télécharge le dernier CSV collecté pour un POS."""
    magasin = load_magasin()
    if pos_id not in magasin:
        raise HTTPException(404, f"POS '{pos_id}' inconnu")
    latest = get_latest_file(pos_id)
    if not latest:
        raise HTTPException(404, f"Aucune donnée collectée pour {pos_id}")
    return FileResponse(
        path=str(latest),
        media_type="text/csv",
        filename=latest.name,
    )


@app.get("/logs", tags=["Logs"])
def get_logs(lines: int = Query(100, description="Nombre de dernières lignes")):
    """Retourne les dernières lignes du log de collecte."""
    log_file = LOGS_DIR / "collector.log"
    if not log_file.exists():
        return {"logs": [], "message": "Aucun log disponible"}

    with open(log_file, encoding="utf-8") as f:
        all_lines = f.readlines()

    last_lines = all_lines[-lines:]
    return {
        "total_lines": len(all_lines),
        "returned":    len(last_lines),
        "logs":        [l.rstrip() for l in last_lines],
    }


@app.get("/reports", tags=["Logs"])
def list_reports():
    """Liste les rapports de collecte générés."""
    reports = sorted(LOGS_DIR.glob("report_*.json"), reverse=True)
    result = []
    for r in reports:
        try:
            with open(r, encoding="utf-8") as f:
                data = json.load(f)
            ok  = sum(1 for x in data.get("results", []) if x.get("status") == "ok")
            err = sum(1 for x in data.get("results", []) if x.get("status") == "error")
            result.append({
                "filename":   r.name,
                "started_at": data.get("started_at"),
                "elapsed_s":  data.get("elapsed_s"),
                "ok":         ok,
                "error":      err,
                "total":      len(data.get("results", [])),
            })
        except Exception:
            result.append({"filename": r.name, "error": "lecture impossible"})
    return {"reports": result, "total": len(result)}


@app.get("/reports/{filename}", tags=["Logs"])
def get_report(filename: str):
    """Retourne le détail d'un rapport de collecte."""
    if ".." in filename or "/" in filename:
        raise HTTPException(400, "Nom de fichier invalide")
    path = LOGS_DIR / filename
    if not path.exists():
        raise HTTPException(404, "Rapport introuvable")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ─── Run ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("API_PORT", "8100"))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=False)
