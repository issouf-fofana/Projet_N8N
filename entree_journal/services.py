"""
Services — Entrée Journal
Import des CSV + vérification d'intégration Asten
"""
import csv
import json
from collections import defaultdict
from datetime import datetime, timedelta, date
from pathlib import Path

from django.conf import settings
from django.utils.dateparse import parse_datetime
from django.utils import timezone

from .models import EntreeJournal, FichierImporte

# ─── Constantes ──────────────────────────────────────────────────────────────

# 4 types obligatoires — leur présence = intégration validée
TYPES_OBLIGATOIRES = {
    "price_updater":     "Price Updater",
    "linked_code":       "Linked Code",
    "product":           "Product",
    "product_suppliers": "Product Suppliers",
}

# 1 type optionnel — présence affichée mais n'influence pas la conformité
TYPES_OPTIONNELS = {
    "supplier_pre_order": "Pre-order",
}

# Tous les types à afficher (obligatoires + optionnels)
TYPES_REQUIS = {**TYPES_OPTIONNELS, **TYPES_OBLIGATOIRES}

STATUS_LABELS = {0: "erreur", 1: "en cours", 2: "succès", 3: "indéfini"}


# ─── Explication courte des erreurs ──────────────────────────────────────────

_ERROR_EXPLANATIONS = [
    ("mutex",                          ("warning",  "Import en parallèle — repris automatiquement")),
    ("fichier ignoré",                 ("warning",  "Import en parallèle — repris automatiquement")),
    ("existe déjà",                    ("info",     "Déjà intégré — doublon ignoré")),
    ("department matching query",      ("tag",      "Rayon/département introuvable dans RPOS — à créer")),
    ("ce champ ne peut être nul",      ("file",     "Données manquantes — article sans produit lié dans Asten")),
    ("non fourni par",                 ("ban",      "Article absent du catalogue fournisseur dans RPOS")),
    ("non commandable",                ("lock",     "Article bloqué — non commandable dans RPOS")),
    ("article non commandable",        ("lock",     "Article bloqué — non commandable dans RPOS")),
    ("erreur association",             ("link",     "Fournisseur associé à un autre magasin — config RPOS incorrecte")),
    ("magasin différent",              ("link",     "Fournisseur associé à un autre magasin — config RPOS incorrecte")),
    ("n'est pas fourni",               ("ban",      "Article absent du catalogue fournisseur dans RPOS")),
    ("does not exist",                 ("question", "Référence introuvable dans RPOS — données à synchroniser")),
    ("invalid",                        ("x-circle", "Valeur invalide — vérifier les données dans Asten")),
    ("null",                           ("file",     "Données manquantes — champ obligatoire vide dans Asten")),
]

def get_error_explanation(message):
    """Retourne (icon, texte) pour un message d'erreur."""
    m = message.lower()
    for pattern, explanation in _ERROR_EXPLANATIONS:
        if pattern in m:
            return explanation
    return ("question-circle", "Erreur inconnue — vérifier le rapport complet")


# Messages qui sont des avertissements (pas de vraies erreurs bloquantes)
_WARNING_PATTERNS = [
    "mutex",
    "fichier ignoré",
    "existe déjà",
    "import déjà en cours",
    "already in progress",
]

def _is_warning_message(message):
    m = message.lower()
    return any(p in m for p in _WARNING_PATTERNS)


def _parse_report(report_text):
    """
    Parse le texte du rapport (segments séparés par ' | ').
    Retourne { summary: {...}, errors: [...], warnings: [...], logs: [...] }

    - errors   : vraies erreurs article (null, non commandable, non fourni...)
    - warnings : avertissements non bloquants (mutex, doublon déjà intégré...)
    - logs     : messages de processus (fichier présent, lancement, etc.)
    """
    if not report_text:
        return {"summary": None, "errors": [], "warnings": [], "logs": []}

    import re
    parts = [p.strip() for p in report_text.split(" | ") if p.strip()]

    summary    = {}
    errors     = []
    warnings   = []
    logs       = []
    in_summary = False

    for part in parts:
        # Ligne de fin d'intégration → début du récap
        if "fin intégration" in part or "fin integration" in part.lower():
            in_summary = True
            m = re.search(r'\(([a-f0-9\-]{36})\)', part)
            if m:
                summary["uuid"] = m.group(1)
            continue

        if in_summary:
            if part.startswith("fichier:"):
                summary["fichier"] = part.replace("fichier:", "").strip()
            elif re.match(r'^\d+\s+lignes', part):
                m = re.match(r'^(\d+)\s+lignes\s+(.+)$', part)
                if m:
                    n, label = int(m.group(1)), m.group(2).strip().lower()
                    if "format" in label:
                        summary["nb_formatees"] = n
                    elif "erreur" in label:
                        summary["nb_erreurs"] = n
                    elif "cr" in label:
                        summary["nb_crees"] = n
                    elif "jour" in label:
                        summary["nb_maj"] = n
                    elif "supprim" in label:
                        summary["nb_supprimees"] = n
        else:
            m_ts = re.match(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d+)\s+(.*)', part)
            if not m_ts:
                continue
            timestamp = m_ts.group(1)[:19]
            rest      = m_ts.group(2)

            if "Extrait du rapport" in rest or "fin intégration" in rest:
                continue

            # Extraire les données article après " - [id;fournisseur;...]"
            m_data    = re.search(r'-\s*\[([^\[\]]+)\]\s*$', rest)
            raw_data  = m_data.group(1) if m_data else ""

            # Extraire message ErrorDetail Django
            m_err   = re.findall(r"string='([^']+)'", rest)
            has_err = bool(m_err)
            message = m_err[0] if m_err else rest[:200]

            # Parser les champs article séparés par ";"
            data_fields = []
            if raw_data:
                field_labels = ["Article", "Fournisseur", "Prix", "Ref", "Code"]
                for i, val in enumerate(raw_data.split(";")):
                    lbl = field_labels[i] if i < len(field_labels) else f"#{i}"
                    data_fields.append({"label": lbl, "value": val.strip()})

            entry = {
                "timestamp":   timestamp,
                "message":     message,
                "data":        raw_data,
                "data_fields": data_fields,
            }

            # Classifier : avertissement, vraie erreur, ou log de processus
            if data_fields or has_err:
                if _is_warning_message(message):
                    warnings.append(entry)
                else:
                    errors.append(entry)
            else:
                logs.append(entry)

    return {"summary": summary, "errors": errors, "warnings": warnings, "logs": logs}

HEURE_DEBUT_SOIR = 20 * 60       # 20h00 en minutes
HEURE_FIN_MATIN  = 1 * 60 + 40  # 01h40 en minutes

MEDIA_DIR = Path(settings.MEDIA_ROOT) / "entree_journal"


# ─── Import CSV → DB ─────────────────────────────────────────────────────────

def _parse_dt(val):
    if not val:
        return None
    try:
        dt = datetime.fromisoformat(str(val).replace("Z", ""))
        if timezone.is_naive(dt):
            dt = timezone.make_aware(dt)
        return dt
    except Exception:
        return None


def _parse_int(val, default=0):
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


def importer_fichiers():
    """
    Scanne MEDIA_ROOT/entree_journal/, importe les CSV nouveaux dans la DB.
    Retourne un résumé {total_fichiers, nouveaux, lignes_inserees, erreurs}.
    """
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)

    fichiers = sorted(MEDIA_DIR.glob("*.csv"))
    deja_importes = set(FichierImporte.objects.values_list("nom_fichier", flat=True))

    total = len(fichiers)
    nouveaux = 0
    lignes_inserees = 0
    erreurs = []

    for fpath in fichiers:
        nom = fpath.name
        if nom in deja_importes:
            continue

        try:
            nb = _importer_un_fichier(fpath)
            FichierImporte.objects.create(
                nom_fichier=nom,
                pos_id=nom.split("_")[0],
                nb_lignes=nb,
            )
            nouveaux += 1
            lignes_inserees += nb
            # Supprimer le fichier brut après import réussi
            try:
                fpath.unlink()
            except Exception as del_err:
                erreurs.append({"fichier": nom, "erreur": f"Import OK mais suppression échouée : {del_err}"})
        except Exception as e:
            erreurs.append({"fichier": nom, "erreur": str(e)})

    return {
        "total_fichiers":  total,
        "nouveaux":        nouveaux,
        "lignes_inserees": lignes_inserees,
        "erreurs":         erreurs,
    }


def _importer_un_fichier(fpath):
    """Lit un CSV et insère les entrées manquantes. Retourne nb insérées."""
    rows = []
    with open(fpath, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            rows.append(row)

    if not rows:
        return 0

    # Récupérer les entry_id déjà en base pour éviter les doublons
    ids_in_file = [r.get("id", "").strip() for r in rows if r.get("id", "").strip()]
    existing = set(
        EntreeJournal.objects.filter(entry_id__in=ids_in_file)
        .values_list("entry_id", flat=True)
    )

    to_create = []
    for row in rows:
        eid = row.get("id", "").strip()
        if not eid or eid in existing:
            continue

        to_create.append(EntreeJournal(
            entry_id               = eid,
            pos_id                 = row.get("pos_id", "").strip(),
            pos_name               = row.get("pos_name", "").strip(),
            collected_at           = _parse_dt(row.get("collected_at")) or timezone.now(),
            created_at             = _parse_dt(row.get("created_at")) or timezone.now(),
            updated_at             = _parse_dt(row.get("updated_at")),
            entry_type_value       = _parse_int(row.get("entry_type_value"), 30),
            entry_type_label       = row.get("entry_type_label", "").strip(),
            entry_type_detail_type = row.get("entry_type_detail_type", "").strip(),
            entry_type_detail_text = row.get("entry_type_detail_text", "").strip(),
            status_value           = _parse_int(row.get("status_value"), 3),
            status_label           = row.get("status_label", "").strip(),
            username               = row.get("username", "").strip(),
            report                 = row.get("report", "").strip(),
            shop_reference         = row.get("shop_reference", "").strip(),
            shop_name              = row.get("shop_name", "").strip(),
        ))

    if to_create:
        EntreeJournal.objects.bulk_create(to_create, ignore_conflicts=True)

    return len(to_create)


# ─── Vérification intégration ─────────────────────────────────────────────────

def _in_window(created_at, run_date):
    """J-1 20h00 → J 01h40"""
    if not created_at:
        return False
    dt = created_at
    if timezone.is_aware(dt):
        dt = timezone.localtime(dt)
    d    = dt.date()
    mins = dt.hour * 60 + dt.minute
    j    = run_date
    j1   = run_date - timedelta(days=1)

    if d == j1 and mins >= HEURE_DEBUT_SOIR:
        return True
    if d == j and mins <= HEURE_FIN_MATIN:
        return True
    return False


def get_integration_asten(run_date=None):
    """
    Retourne le rapport d'intégration Asten pour une date donnée.
    run_date = jour de lancement (défaut: aujourd'hui)
    Vérifie la fenêtre : run_date-1 20h00 → run_date 01h40
    """
    if run_date is None:
        run_date = date.today()

    j1  = run_date - timedelta(days=1)

    # Fenêtre en datetime
    debut = timezone.make_aware(datetime(j1.year, j1.month, j1.day, 20, 0, 0))
    fin   = timezone.make_aware(datetime(run_date.year, run_date.month, run_date.day, 1, 40, 0))

    # Requête DB : entry_type=30, fenêtre horaire
    qs = EntreeJournal.objects.filter(
        entry_type_value=30,
        created_at__gte=debut,
        created_at__lte=fin,
    ).order_by('created_at')

    # Charger magasin.json pour la cartographie POS → shops
    magasin_path = Path(__file__).parent.parent / "Entree_journal" / "magasin.json"
    try:
        with open(magasin_path, encoding="utf-8") as f:
            magasin = json.load(f)
    except Exception:
        magasin = {}

    # Grouper les entrées par pos_id → shop_reference → type
    grouped = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))
    for e in qs:
        if e.entry_type_detail_type in TYPES_REQUIS:
            grouped[e.pos_id][e.shop_reference][e.entry_type_detail_type].append(e)

    # Construire le rapport par POS
    rapport_pos = {}
    total_shops = 0
    total_ok    = 0
    total_partial = 0
    total_absent  = 0
    total_no_data = 0

    for pos_id, pos_info in magasin.items():
        pos_name = pos_info.get("name", pos_id)
        expected_shops = {s["reference"]: s["name"] for s in pos_info.get("shops", [])}

        if not expected_shops:
            continue

        shops_result = {}
        has_any_data = pos_id in grouped

        for ref, sname in expected_shops.items():
            types_detail = {}

            for type_key, type_label in TYPES_REQUIS.items():
                entrees_db = grouped[pos_id][ref].get(type_key, [])

                if not entrees_db:
                    types_detail[type_key] = {
                        "label":       type_label,
                        "detail_text": type_label,
                        "integre":     False,
                        "optionnel":   type_key in TYPES_OPTIONNELS,
                        "statut":      "absent",
                        "nb_total":    0,
                        "nb_succes":   0,
                        "nb_erreurs":  0,
                        "entrees":     [],
                    }
                else:
                    entrees_list = []
                    for e in sorted(entrees_db, key=lambda x: x.created_at, reverse=True):
                        parsed = _parse_report(e.report)
                        entrees_list.append({
                            "created_at":      e.created_at.strftime("%d/%m/%Y %H:%M"),
                            "status_value":    e.status_value,
                            "status_label":    e.status_label or STATUS_LABELS.get(e.status_value, "?"),
                            "username":        e.username,
                            "type_detail":     e.entry_type_detail_text,
                            "report":          e.report,
                            "report_summary":  parsed["summary"],
                            "report_errors":   parsed["errors"],
                            "report_warnings": parsed["warnings"],
                            "report_logs":     parsed["logs"],
                        })

                    nb_succes        = sum(1 for e in entrees_db if e.status_value == 2)
                    nb_total         = len(entrees_db)
                    # nb_erreurs = uniquement les vraies erreurs (pas mutex/doublon)
                    nb_erreurs       = sum(len(e["report_errors"])   for e in entrees_list)
                    nb_avertissements = sum(len(e["report_warnings"]) for e in entrees_list)

                    integre       = nb_total > 0
                    est_optionnel = type_key in TYPES_OPTIONNELS

                    if integre and nb_erreurs == 0:
                        statut = "ok"           # intégré sans vraie erreur article
                    elif integre and nb_erreurs > 0:
                        statut = "ok_erreurs"   # intégré mais articles en erreur
                    else:
                        statut = "en_cours"

                    # Nom réel du fichier (depuis CSV entry_type_detail_text)
                    detail_text = next(
                        (e.entry_type_detail_text for e in entrees_db if e.entry_type_detail_text),
                        type_label
                    )

                    types_detail[type_key] = {
                        "label":              type_label,
                        "detail_text":        detail_text,
                        "integre":            integre,
                        "optionnel":          est_optionnel,
                        "statut":             statut,
                        "nb_total":           len(entrees_list),
                        "nb_succes":          nb_succes,
                        "nb_erreurs":         nb_erreurs,
                        "nb_avertissements":  nb_avertissements,
                        "entrees":            entrees_list,
                    }

            # Conformité = uniquement sur les 4 types OBLIGATOIRES
            nb_oblig_integres = sum(
                1 for k, t in types_detail.items()
                if k in TYPES_OBLIGATOIRES and t["integre"]
            )
            nb_oblig_avec_erreurs = sum(
                1 for k, t in types_detail.items()
                if k in TYPES_OBLIGATOIRES and t["integre"] and t["nb_erreurs"] > 0
            )
            nb_obligatoires = len(TYPES_OBLIGATOIRES)  # 4

            if nb_oblig_integres == nb_obligatoires:
                # Tous les 4 obligatoires présents = INTÉGRÉ
                statut_global = "ok_erreurs" if nb_oblig_avec_erreurs > 0 else "ok"
            elif nb_oblig_integres > 0:
                statut_global = "partial"
            else:
                statut_global = "absent"

            shops_result[ref] = {
                "reference":    ref,
                "name":         sname,
                "statut":       statut_global,
                "nb_ok":        nb_oblig_integres,
                "nb_total":     nb_obligatoires,
                "nb_erreurs_articles": sum(
                    t["nb_erreurs"] for t in types_detail.values()
                ),
                "types":        types_detail,
            }

            total_shops += 1
            if statut_global in ("ok", "ok_erreurs"):
                total_ok += 1   # intégré = conforme, même avec erreurs articles
            elif statut_global == "partial":
                total_partial += 1
            else:
                if has_any_data:
                    total_absent += 1
                else:
                    total_no_data += 1

        # Statut agrégé du POS
        shops_list = list(shops_result.values())
        pos_nb_ok       = sum(1 for s in shops_list if s["statut"] == "ok")
        pos_nb_partial  = sum(1 for s in shops_list if s["statut"] == "partial")
        pos_nb_absent   = sum(1 for s in shops_list if s["statut"] == "absent")
        if pos_nb_ok == len(shops_list):
            pos_statut = "ok"
        elif pos_nb_ok > 0 or pos_nb_partial > 0:
            pos_statut = "partial"
        else:
            pos_statut = "absent" if has_any_data else "no_data"

        rapport_pos[pos_id] = {
            "pos_id":       pos_id,
            "name":         pos_name,
            "has_data":     has_any_data,
            "shops":        shops_result,
            "statut":       pos_statut,
            "nb_ok":        pos_nb_ok,
            "nb_partial":   pos_nb_partial,
            "nb_absent":    pos_nb_absent,
        }

    conformite = round(total_ok / total_shops * 100, 1) if total_shops else 0

    return {
        "disponible":      qs.exists(),
        "run_date":        str(run_date),
        "date_nuit":       str(j1),
        "fenetre":         f"{j1} 20h00 → {run_date} 01h40",
        "pos":             rapport_pos,
        "summary": {
            "total_shops":    total_shops,
            "ok":             total_ok,
            "partial":        total_partial,
            "absent":         total_absent,
            "no_data":        total_no_data,
            "conformite_pct": conformite,
        },
        "nb_entrees_total": qs.count(),
    }


def get_historique_imports():
    """Retourne la liste des fichiers importés."""
    return FichierImporte.objects.all().order_by('-importe_le')[:50]
