"""
Contrôle de conformité articles : Product × Asten (POS) × Assortiment
----------------------------------------------------------------------
Sources :
  - Product   : 030_products_*.csv  (SMB backup)
  - Asten     : commandable_pos4-*.csv  (export POS)
  - Assortiment : assortiment.json  {code_magasin: [refs...]}

Résultat : conformite_035_<timestamp>.csv + .json
"""

import csv
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# ─── Chemins ────────────────────────────────────────────────────────────────

SMB_BACKUP = Path(os.getenv(
    "SMB_BACKUP_PATH",
    "/run/user/1000/gvfs/smb-share:server=10.0.70.104,share=d/"
    "Cegid/AstenProd/ReferentielPrecom/Backup"
))

OUTPUT_DIR = Path(os.getenv("EXPORT_DIR", Path(__file__).parent / "output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

ASSORTIMENTS_JSON = Path(__file__).parent.parent / "assortiments_magasins.json"

# Construit dynamiquement {code_magasin → code_backup} depuis assortiments_magasins.json
def build_magasin_to_backup():
    if not ASSORTIMENTS_JSON.exists():
        return {}
    with open(ASSORTIMENTS_JSON, encoding="utf-8") as f:
        data = json.load(f)
    mapping = {}
    for backup_code, magasins in data.items():
        for m in magasins:
            mapping[m["code_magasin"]] = backup_code
    return mapping

MAGASIN_TO_BACKUP = build_magasin_to_backup()

# ─── Moteur de conformité ────────────────────────────────────────────────────

def controler_article(status_product, code_mak, present_asten, actif_asten, present_assortiment, deref=False):
    """
    Règles de commandabilité à la centrale (source: tableau référentiel Prosuma) :
      AC/CC  → commandable si actif dans l'assortiment (Asten = canal de commande, pas critère d'éligibilité)
      AC/CD  → commandable si actif dans l'assortiment
      AC/LD  → NON commandable à la centrale (uniquement chez fournisseur ASP)
      NA     → NON commandable (ni centrale ni fournisseur)
      ES     → NON commandable (ni centrale ni fournisseur)
      NC/CC  → commandable si actif dans l'assortiment
      NC/CD  → commandable si actif dans l'assortiment
      NC/LD  → NON commandable
    """
    # Déréférencé dans CEGID : KO seulement si encore commandable dans Asten (anomalie)
    if deref:
        if present_asten:
            return "KO", "Déréférencé"
        return "OK", "Déréférencé (non cmdable Asten)"

    if status_product == "AC":
        # AC/LD : commandable si dans assortiment ET cmdable Asten (comme CC/CD)
        # AC/CC, AC/CD, AC/LD : même règle — commandable si dans assortiment ET dans Asten
        if not present_assortiment:
            if present_asten:
                return "KO", "Absent assortiment"
            return "OK", "Absent assortiment (non cmdable Asten)"
        if not present_asten:
            return "KO", "Non commandable Asten"
        return "OK", f"Commandable ({code_mak})"

    elif status_product == "ES":
        # ES = arrêt : KO seulement si encore commandable dans Asten (anomalie)
        if present_asten:
            return "KO", "Statut ES"
        return "OK", "Statut ES (non cmdable Asten)"

    elif status_product == "NA":
        # NA = non autorisé : KO seulement si encore commandable dans Asten (anomalie)
        if present_asten:
            return "KO", "Statut NA"
        return "OK", "Statut NA (non cmdable Asten)"

    elif status_product == "NC":
        if code_mak in ("CC", "CD"):
            if not present_assortiment:
                if present_asten:
                    return "KO", "Absent assortiment"
                return "OK", "Absent assortiment (non cmdable Asten)"
            if not present_asten:
                return "KO", "Non commandable Asten"
            return "OK", f"Commandable NC ({code_mak})"
        elif code_mak == "LD":
            if present_asten:
                return "KO", "NC/LD"
            return "OK", "NC/LD (non cmdable Asten)"
        else:
            return "KO", f"CODE MAK inconnu : {code_mak}"

    return "KO", f"Statut inconnu : {status_product}"

# ─── Lecture sources ─────────────────────────────────────────────────────────

def lire_products(backup_prefix, backup_dir):
    """
    Retourne {code_article: {label, status, code_mak, deref}} depuis 030_products_*.csv
    col0 = flag déréférencé ('D' = déréférencé, espace = actif)
    col4 = code article POS (9 chiffres) — clé de jointure avec le CSV commandable
    col6 = libellé long
    col10 = statut (AC/NA/ES/NC)
    col15 = CODE MAK
    """
    pattern = list(backup_dir.glob(f"{backup_prefix}_products_*.csv"))
    if not pattern:
        raise FileNotFoundError(f"Fichier {backup_prefix}_products_*.csv introuvable dans {backup_dir}")
    fichier = sorted(pattern)[-1]
    print(f"  Product : {fichier.name}")
    result = {}
    with open(fichier, encoding="utf-8", errors="replace") as f:
        for row in csv.reader(f, delimiter=";"):
            if len(row) < 16:
                continue
            deref    = row[0].strip() == "D"  # col0 : D = déréférencé
            code_art = row[4].strip()          # code article POS, 9 chiffres
            label    = row[6].strip()
            status   = row[10].strip()
            code_mak = row[15].strip()
            if code_art and status:            # ignorer les lignes sans code ou statut
                result[code_art] = {"label": label, "status": status, "code_mak": code_mak, "deref": deref}
    n_deref = sum(1 for v in result.values() if v["deref"])
    print(f"  → {len(result)} articles Product (clé=code_article col4) dont {n_deref} déréférencés")
    return result


def lire_asten(magasin_code):
    """
    Retourne set des codes articles présents pour ce magasin dans les CSV commandables.
    Cherche dans tous les fichiers commandable_pos*.csv (tous les POS).
    """
    import re as _re
    # Tous les fichiers courants (sans horodatage) en priorité, sinon les horodatés
    tous = list(OUTPUT_DIR.glob("commandable_pos*.csv"))
    if not tous:
        raise FileNotFoundError(f"Aucun fichier commandable_pos*.csv dans {OUTPUT_DIR}")

    # Regrouper par "base" (nom sans horodatage) → garder le plus récent par mtime
    bases = {}
    for p in tous:
        base = _re.sub(r'_\d{8}_\d{6}$', '', p.stem)
        if base not in bases or p.stat().st_mtime > bases[base].stat().st_mtime:
            bases[base] = p

    fichiers = sorted(bases.values(), key=lambda p: p.name)
    present = set()
    for fichier in fichiers:
        with open(fichier, encoding="utf-8-sig", errors="replace") as f:
            for row in csv.DictReader(f, delimiter=";"):
                if row.get("magasin", "").strip() != magasin_code:
                    continue
                ref = row.get("ean", "").strip()
                if ref:
                    present.add(ref)
    if present:
        print(f"  Asten   : {len(fichiers)} fichiers POS — {len(present)} articles pour magasin {magasin_code}")
    else:
        print(f"  Asten   : magasin {magasin_code} introuvable dans {len(fichiers)} fichiers POS")
    return present


def lire_assortiment(backup_prefix, backup_dir):
    """
    L'assortiment = tous les codes articles (col4) du fichier 030_products_*.csv.
    """
    pattern = list(backup_dir.glob(f"{backup_prefix}_products_*.csv"))
    if not pattern:
        print(f"  ⚠️  Fichier assortiment {backup_prefix}_products_*.csv introuvable")
        return None
    fichier = sorted(pattern)[-1]
    refs = set()
    with open(fichier, encoding="utf-8", errors="replace") as f:
        for row in csv.reader(f, delimiter=";"):
            if len(row) >= 5:
                code_art = row[4].strip()
                if code_art:
                    refs.add(code_art)
    print(f"  Assortiment : {len(refs)} codes articles ({fichier.name})")
    return refs

# ─── Contrôle principal ──────────────────────────────────────────────────────

def _trouver_backup_dir(backup_prefix):
    """
    Cherche le dossier backup contenant {backup_prefix}_products_*.csv.
    Priorité : prdP2A_* (backup quotidien) > VG_VERIF_PPROMO2 (backup alternatif).
    """
    for candidate in [
        *sorted(SMB_BACKUP.glob("prdP2A_*"), reverse=True),
        SMB_BACKUP / "VG_VERIF_PPROMO2",
    ]:
        if candidate.exists() and list(candidate.glob(f"{backup_prefix}_products_*.csv")):
            return candidate
    return None


def controler_magasin(magasin_code):
    backup_prefix = MAGASIN_TO_BACKUP.get(magasin_code)
    if not backup_prefix:
        print(f"⛔ Pas de mapping backup pour magasin {magasin_code}")
        return

    backup_dir = _trouver_backup_dir(backup_prefix)
    if not backup_dir:
        print(f"⛔ Fichier {backup_prefix}_products_*.csv introuvable dans prdP2A_* ni VG_VERIF_PPROMO2")
        return
    print(f"\n[{magasin_code}] Dossier backup : {backup_dir.name}")

    products     = lire_products(backup_prefix, backup_dir)
    asten_refs   = lire_asten(magasin_code)
    assort_refs  = lire_assortiment(backup_prefix, backup_dir)

    rows = []
    stats = {"OK": 0, "KO": 0}

    for ref, info in products.items():
        status   = info["status"]
        code_mak = info["code_mak"]
        deref    = info.get("deref", False)

        present_asten      = ref in asten_refs
        actif_asten        = present_asten
        present_assortiment = (ref in assort_refs) if assort_refs is not None else None

        if not deref and present_assortiment is None:
            resultat = "INCOMPLET"
            motif    = "Assortiment non disponible"
        else:
            resultat, motif = controler_article(
                status, code_mak,
                present_asten, actif_asten,
                present_assortiment,
                deref=deref,
            )

        stats[resultat] = stats.get(resultat, 0) + 1
        rows.append({
            "reference":           ref,
            "libelle":             info["label"],
            "status_product":      status,
            "code_mak":            code_mak,
            "num_assortiment":     backup_prefix,
            "deref":               "Oui" if deref else "Non",
            "present_asten":       "Oui" if present_asten else "Non",
            "actif_asten":         "Oui" if actif_asten else "Non",
            "present_assortiment": "Oui" if present_assortiment else ("Non" if present_assortiment is not None else "N/A"),
            "resultat":            resultat,
            "motif":               motif,
            "absent_cegid":        "Non",
        })

    # Articles présents dans Asten mais absents du fichier CEGID products
    asten_only = asten_refs - set(products.keys())
    n_asten_only = len(asten_only)
    if n_asten_only:
        print(f"  → {n_asten_only} articles Asten absents du fichier CEGID (absent_cegid)")
    for ref in sorted(asten_only):
        stats["KO"] = stats.get("KO", 0) + 1
        rows.append({
            "reference":           ref,
            "libelle":             "",
            "status_product":      "",
            "code_mak":            "",
            "num_assortiment":     backup_prefix,
            "deref":               "Non",
            "present_asten":       "Oui",
            "actif_asten":         "Oui",
            "present_assortiment": "N/A",
            "resultat":            "KO",
            "motif":               "Absent CEGID",
            "absent_cegid":        "Oui",
        })

    total = len(rows)
    ok    = stats.get("OK", 0)
    ko    = stats.get("KO", 0)
    taux  = round(ok / total * 100, 1) if total else 0

    print(f"\n  Résultats : Total={total}  OK={ok}  KO={ko}  Conformité={taux}%")

    if not rows:
        print(f"  ⚠️  Aucun article trouvé pour magasin {magasin_code} — fichier ignoré")
        return None

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path  = OUTPUT_DIR / f"conformite_{magasin_code}_{ts}.csv"
    json_path = OUTPUT_DIR / f"conformite_{magasin_code}_{ts}.json"

    # CSV
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
        writer.writeheader()
        writer.writerows(rows)
    print(f"  CSV  : {csv_path}")

    # JSON résumé
    rapport = {
        "magasin": magasin_code, "date": ts,
        "total": total, "ok": ok, "ko": ko, "taux_conformite": taux,
        "all_rows": rows,
    }
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(rapport, f, ensure_ascii=False, indent=2)
    print(f"  JSON : {json_path}")

    # Sauvegarde en base Django
    _sauvegarder_en_base(magasin_code, backup_prefix, ts, total, ok, ko, taux, json_path.name, rows)

    return rapport


def _sauvegarder_en_base(magasin_code, backup_prefix, ts, total, ok, ko, taux, source_fichier, rows):
    """Insère ou remplace le dernier rapport de conformité en base Django."""
    try:
        import django
        import os as _os
        _os.environ.setdefault("DJANGO_SETTINGS_MODULE", "verification_commande.settings")
        django.setup()
        from entree_journal.models import ControleConformite, ArticleConformite
        from datetime import datetime as _dt

        date_controle = _dt.strptime(ts, "%Y%m%d_%H%M%S")

        # Supprimer l'ancien rapport pour ce magasin (garder uniquement le plus récent)
        ControleConformite.objects.filter(magasin=magasin_code).delete()

        controle = ControleConformite.objects.create(
            magasin=magasin_code,
            num_assortiment=backup_prefix,
            date_controle=date_controle,
            total=total, ok=ok, ko=ko,
            taux_conformite=taux,
            source_fichier=source_fichier,
        )

        # Insertion en batch
        articles = []
        for r in rows:
            articles.append(ArticleConformite(
                controle=controle,
                reference=r.get("reference", ""),
                libelle=r.get("libelle", "")[:255],
                status_product=r.get("status_product", ""),
                code_mak=r.get("code_mak", ""),
                num_assortiment=r.get("num_assortiment", ""),
                deref=r.get("deref") == "Oui",
                present_asten=r.get("present_asten") == "Oui",
                present_assortiment=None if r.get("present_assortiment") == "N/A"
                                    else r.get("present_assortiment") == "Oui",
                resultat=r.get("resultat", ""),
                motif=r.get("motif", ""),
                absent_cegid=r.get("absent_cegid") == "Oui",
            ))
        ArticleConformite.objects.bulk_create(articles, batch_size=500)
        print(f"  DB   : {len(articles)} articles insérés en base (magasin {magasin_code})")
    except Exception as e:
        print(f"  ⚠️  Erreur sauvegarde base : {e}")

# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    magasins = sys.argv[1:] if len(sys.argv) > 1 else ["035"]
    for mag in magasins:
        controler_magasin(mag)
