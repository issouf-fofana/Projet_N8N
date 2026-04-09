"""
Verifier — Intégration Asten par magasin
Pour une date donnée, vérifie que les 4 types de fichiers Asten
ont bien été importés sur chaque POS/magasin entre 20h00 et 00h40.

Fichier backup → entry_type_detail_type :
  linkedcodes       → linked_code
  pricing           → price_updater
  products          → product
  productssuppliers → product_suppliers
"""

import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from collections import defaultdict

# ─── Chemins ────────────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
MAGASIN  = BASE_DIR / "magasin.json"

# ─── Constantes ─────────────────────────────────────────────────────────────

# Les 5 types à vérifier (type_journal → libellé fichier)
TYPES_REQUIS = {
    "supplier_pre_order": "Pre-order",
    "price_updater":      "Price Updater",
    "linked_code":        "Linked Code",
    "product":            "Product",
    "product_suppliers":  "Product Suppliers",
}

STATUS_LABELS = {
    0: "erreur",
    1: "en cours",
    2: "succès",
    3: "indéfini",
}

# Fenêtre horaire : J-1 20h00 → J 01h40
# Quand on lance le 25/03, on vérifie la nuit du 24/03 20h00 → 25/03 01h40
HEURE_DEBUT_SOIR = 20 * 60   # 20h00 en minutes (côté J-1)
HEURE_FIN_MATIN  = 1 * 60 + 40  # 01h40 en minutes (côté J)

ENTRY_TYPE_IMPORT = 30
STATUS_SUCCES     = 2


# ─── Chargement ─────────────────────────────────────────────────────────────

def load_magasin():
    with open(MAGASIN, encoding="utf-8") as f:
        return json.load(f)


def get_latest_csv(pos_id):
    """Retourne le CSV le plus récent pour un POS."""
    files = sorted(DATA_DIR.glob(f"{pos_id}_*.csv"), reverse=True)
    return files[0] if files else None


def load_csv(path):
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            try:
                row["entry_type_value"] = int(row.get("entry_type_value") or -1)
                row["status_value"]     = int(row.get("status_value") or -1)
            except (ValueError, TypeError):
                pass
            rows.append(row)
    return rows


# ─── Filtrage horaire ────────────────────────────────────────────────────────

def _minutes_since_midnight(dt):
    return dt.hour * 60 + dt.minute


def in_window(created_at_str, run_date):
    """
    Vérifie si created_at est dans la fenêtre Asten :
      run_date - 1 jour à 20h00  →  run_date à 01h40
    Exemple : lance le 25/03 → vérifie 24/03 20h00 → 25/03 01h40
    """
    try:
        dt = datetime.fromisoformat(created_at_str.replace("Z", ""))
    except (ValueError, AttributeError):
        return False

    d    = dt.date()
    mins = _minutes_since_midnight(dt)
    j    = run_date          # jour de lancement (ex: 25/03)
    j1   = run_date - timedelta(days=1)  # veille       (ex: 24/03)

    # Partie soir : veille entre 20h00 et 23h59
    if d == j1 and mins >= HEURE_DEBUT_SOIR:
        return True

    # Partie nuit : jour J entre 00h00 et 01h40
    if d == j and mins <= HEURE_FIN_MATIN:
        return True

    return False


# ─── Vérification ────────────────────────────────────────────────────────────

def verify_pos(pos_id, pos_info, target_date):
    """
    Vérifie l'intégration Asten pour un POS sur une date donnée.
    Retourne par magasin le détail complet de chaque type d'entrée.
    """
    csv_path = get_latest_csv(pos_id)
    if not csv_path:
        return {"status": "no_data", "shops": {}}

    rows = load_csv(csv_path)

    # Filtrer : entry_type=30 (import) + fenêtre horaire
    imports = [
        r for r in rows
        if r["entry_type_value"] == ENTRY_TYPE_IMPORT
        and in_window(r.get("created_at", ""), target_date)
    ]

    # Grouper par shop_reference → entry_type_detail_type → liste de lignes complètes
    shop_types = defaultdict(lambda: defaultdict(list))
    for r in imports:
        ref = r.get("shop_reference", "").strip()
        typ = r.get("entry_type_detail_type", "").strip()
        if ref and typ in TYPES_REQUIS:
            shop_types[ref][typ].append(r)

    # Construire le résultat par magasin
    expected_shops = {s["reference"]: s["name"] for s in pos_info.get("shops", [])}
    result_shops = {}

    for ref, name in expected_shops.items():
        types_detail = {}

        for type_key, type_label in TYPES_REQUIS.items():
            lignes = shop_types[ref].get(type_key, [])

            if not lignes:
                types_detail[type_key] = {
                    "label":        type_label,
                    "integre":      False,
                    "statut":       "absent",
                    "nb_total":     0,
                    "nb_succes":    0,
                    "nb_erreurs":   0,
                    "entrees":      [],
                }
            else:
                entrees = []
                for l in lignes:
                    sv = l.get("status_value", -1)
                    try:
                        sv = int(sv)
                    except (ValueError, TypeError):
                        sv = -1
                    entrees.append({
                        "created_at":   l.get("created_at", ""),
                        "status_value": sv,
                        "status_label": STATUS_LABELS.get(sv, str(sv)),
                        "username":     l.get("username", ""),
                        "type_detail":  l.get("entry_type_detail_text", ""),
                        "report":       l.get("report", ""),
                    })
                # Trier par date desc
                entrees.sort(key=lambda x: x["created_at"], reverse=True)

                nb_succes  = sum(1 for e in entrees if e["status_value"] == STATUS_SUCCES)
                nb_erreurs = sum(1 for e in entrees if e["status_value"] == 0)

                if nb_succes > 0:
                    statut = "ok"
                elif nb_erreurs > 0:
                    statut = "erreur"
                else:
                    statut = "en_cours"

                types_detail[type_key] = {
                    "label":      type_label,
                    "integre":    nb_succes > 0,
                    "statut":     statut,
                    "nb_total":   len(entrees),
                    "nb_succes":  nb_succes,
                    "nb_erreurs": nb_erreurs,
                    "entrees":    entrees,
                }

        nb_ok    = sum(1 for t in types_detail.values() if t["integre"])
        nb_total = len(TYPES_REQUIS)
        statut_global = "ok" if nb_ok == nb_total else ("partial" if nb_ok > 0 else "absent")

        result_shops[ref] = {
            "reference":    ref,
            "name":         name,
            "statut":       statut_global,
            "nb_ok":        nb_ok,
            "nb_total":     nb_total,
            "types":        types_detail,
        }

    return {
        "status":     "ok",
        "csv":        csv_path.name,
        "nb_imports": len(imports),
        "shops":      result_shops,
    }


# ─── Rapport global ──────────────────────────────────────────────────────────

def verify_all(target_date=None):
    """
    Lance la vérification sur tous les POS.
    target_date = jour de lancement (ex: 25/03).
    Vérifie la fenêtre : target_date-1 20h00 → target_date 01h40.
    Par défaut : aujourd'hui.
    """
    if target_date is None:
        target_date = date.today()

    j1 = target_date - timedelta(days=1)
    magasin = load_magasin()
    rapport = {
        "date_lancement": str(target_date),
        "date_nuit":      str(j1),
        "fenetre":        f"{j1} 20h00 → {target_date} 01h40",
        "pos":            {},
        "summary":        {},
    }

    total_shops = 0
    total_ok    = 0
    total_partial = 0
    total_absent  = 0
    total_no_data = 0

    for pos_id, pos_info in magasin.items():
        result = verify_pos(pos_id, pos_info, target_date)
        rapport["pos"][pos_id] = {
            "name":       pos_info.get("name", pos_id),
            **result,
        }

        if result["status"] == "no_data":
            total_no_data += len(pos_info.get("shops", []))
        else:
            for shop in result["shops"].values():
                total_shops += 1
                if shop["statut"] == "ok":
                    total_ok += 1
                elif shop["statut"] == "partial":
                    total_partial += 1
                else:
                    total_absent += 1

    rapport["summary"] = {
        "total_shops":   total_shops,
        "ok":            total_ok,
        "partial":       total_partial,
        "absent":        total_absent,
        "no_data":       total_no_data,
        "conformite_pct": round(total_ok / total_shops * 100, 1) if total_shops else 0,
    }

    return rapport


# ─── Affichage console ───────────────────────────────────────────────────────

STATUS_ICON = {"ok": "✅", "partial": "⚠️ ", "absent": "❌"}

def print_rapport(rapport):
    s = rapport["summary"]
    print(f"\n{'═'*60}")
    print(f"  VÉRIFICATION INTÉGRATION ASTEN")
    print(f"  Lancé le : {rapport.get('date_lancement','?')}  |  Nuit du : {rapport.get('date_nuit','?')}")
    print(f"  Fenêtre  : {rapport['fenetre']}")
    print(f"{'═'*60}")
    print(f"  Conformité globale : {s['conformite_pct']}%  "
          f"({s['ok']} OK / {s['partial']} partiel / {s['absent']} absent / {s['no_data']} sans données)")

    for pos_id, pos_data in sorted(rapport["pos"].items()):
        shops = pos_data.get("shops", {})
        if not shops:
            print(f"\n  {pos_id} ({pos_data['name']}) — ⚫ Pas de données")
            continue

        print(f"\n  {pos_id} ({pos_data['name']}) — {pos_data.get('nb_imports',0)} imports dans la fenêtre")

        for ref, shop in sorted(shops.items()):
            icon = STATUS_ICON.get(shop["statut"], "?")
            print(f"    {icon} {ref} — {shop['name']}  ({shop['nb_ok']}/{shop['nb_total']} types OK)")
            for type_key, t in shop["types"].items():
                si = "✅" if t["integre"] else ("❌" if t["statut"]=="erreur" else ("🔄" if t["statut"]=="en_cours" else "⚫"))
                line = f"         {si} {t['label']:35s} | {t['nb_total']} entrée(s)"
                if t["nb_erreurs"]:
                    line += f"  | {t['nb_erreurs']} erreur(s)"
                print(line)
                # Afficher les détails de chaque entrée
                for e in t["entrees"]:
                    dt_str = e["created_at"][:16].replace("T", " ")
                    print(f"              → {dt_str}  [{e['status_label']:10s}]  {e['username'] or '-'}")
                    if e["report"]:
                        # Afficher les 2 premières lignes du rapport
                        for rline in e["report"].split(" | ")[:2]:
                            if rline.strip():
                                print(f"                 {rline.strip()[:80]}")

    print(f"\n{'═'*60}\n")


# ─── Entry point ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Vérification intégration Asten")
    parser.add_argument(
        "--date",
        help="Jour de lancement (YYYY-MM-DD). Défaut: aujourd'hui. Vérifie la nuit J-1 20h → J 01h40",
        default=None,
    )
    parser.add_argument(
        "--pos",
        help="POS à vérifier (ex: pos1). Défaut: tous",
        default=None,
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Sortie JSON brute",
    )
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else None

    if args.pos:
        magasin = load_magasin()
        if args.pos not in magasin:
            print(f"POS '{args.pos}' inconnu.")
            sys.exit(1)
        target_d = target or date.today()
        j1 = target_d - timedelta(days=1)
        result = verify_pos(args.pos, magasin[args.pos], target_d)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            shops = result.get("shops", {})
            nb_ok = sum(1 for sh in shops.values() if sh["statut"] == "ok")
            rapport = {
                "date_lancement": str(target_d),
                "date_nuit":      str(j1),
                "fenetre":        f"{j1} 20h00 → {target_d} 01h40",
                "pos": {args.pos: {"name": magasin[args.pos].get("name"), **result}},
                "summary": {
                    "total_shops": len(shops),
                    "ok": nb_ok,
                    "partial": sum(1 for sh in shops.values() if sh["statut"] == "partial"),
                    "absent": sum(1 for sh in shops.values() if sh["statut"] == "absent"),
                    "no_data": 0,
                    "conformite_pct": round(nb_ok / len(shops) * 100, 1) if shops else 0,
                },
            }
            print_rapport(rapport)
    else:
        rapport = verify_all(target)
        if args.json:
            print(json.dumps(rapport, ensure_ascii=False, indent=2, default=str))
        else:
            print_rapport(rapport)
