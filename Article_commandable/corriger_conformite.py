"""
Correction automatique des articles KO (conformité CEGID × Asten)
-------------------------------------------------------------------
Lit les ArticleConformite en KO (dernier ControleConformite par magasin) et
corrige l'article côté RPOS via l'API :

  - Motif "Non commandable Asten" (devrait être commandable, ne l'est pas)
      → PATCH orderable=True, blocked=False

  - Motifs "Statut NA", "Statut ES", "Déréférencé", "NC/LD", "Absent assortiment"
    (ne devrait PAS être commandable, l'est encore)
      → PATCH orderable=False

Par défaut : dry-run (affiche ce qui serait fait, n'écrit rien).
Avec --apply : exécute réellement les PATCH et marque les articles corrigés
en base (ArticleConformite.corrige = True).

Écrit sa progression dans output/correction_progress.json (consommé par le
dashboard en SSE), et un journal détaillé article par article dans
output/correction_<timestamp>.json (ce qui a été corrigé, action, résultat).

Usage CLI :
  python corriger_conformite.py               # dry-run, tous magasins avec KO
  python corriger_conformite.py 035            # dry-run, un seul magasin
  python corriger_conformite.py 035 --apply    # applique réellement
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

import requests
import urllib3
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_DIR = Path(__file__).parent
ENTREE_JOURNAL_DIR = BASE_DIR.parent / "Entree_journal"
MAGASIN_JSON = ENTREE_JOURNAL_DIR / "magasin.json"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
PROGRESS_FILE = OUTPUT_DIR / "correction_progress.json"

load_dotenv(ENTREE_JOURNAL_DIR / ".env")
load_dotenv(BASE_DIR / ".env", override=True)  # RAMIAN : seul compte autorisé à écrire (PATCH) sur api/product/

USER_POS     = os.getenv("DEFAULT_USERNAME", "") or os.getenv("USER_POS", "")
PASSWORD_POS = os.getenv("DEFAULT_PASSWORD", "") or os.getenv("PASSWORD_POS", "")
VERIFY_TLS   = os.getenv("VERIFY_TLS", "false").lower() in ("1", "true", "yes")
TIMEOUT      = float(os.getenv("CORRECTION_REQUEST_TIMEOUT", "8"))

# Motifs → action
MOTIFS_A_RENDRE_COMMANDABLE = {"Non commandable Asten"}
MOTIFS_A_RENDRE_NON_COMMANDABLE = {
    "Statut NA", "Statut ES", "Déréférencé", "NC/LD", "Absent assortiment",
}


def write_progress(state: dict):
    try:
        PROGRESS_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def charger_shop_to_pos():
    with open(MAGASIN_JSON, encoding="utf-8") as f:
        magasin = json.load(f)
    mapping = {}
    for pos_id, info in magasin.items():
        for shop in info["shops"]:
            ref = shop["reference"].lstrip("0") or "0"
            mapping[ref] = {
                "pos_id": pos_id,
                "url": info["url"],
                "shop_id": shop["id"],
                "shop_name": shop["name"],
            }
    return mapping


def make_session():
    if not USER_POS or not PASSWORD_POS:
        raise RuntimeError("USER_POS / PASSWORD_POS manquant (Entree_journal/.env)")
    s = requests.Session()
    s.auth = (USER_POS, PASSWORD_POS)
    s.verify = VERIFY_TLS
    s.headers.update({"Accept": "application/json", "Content-Type": "application/json"})
    return s


def determiner_action(motif):
    """Retourne 'activer', 'desactiver' ou None si motif non géré automatiquement."""
    if motif in MOTIFS_A_RENDRE_COMMANDABLE:
        return "activer"
    if motif in MOTIFS_A_RENDRE_NON_COMMANDABLE:
        return "desactiver"
    return None


def trouver_produit(session, base_url, ean, shop_id):
    url = f"{base_url}/api/product/"
    try:
        r = session.get(url, params={"ean": ean, "shop": shop_id}, timeout=TIMEOUT)
    except requests.RequestException as e:
        return None, str(e)
    if r.status_code != 200:
        return None, f"HTTP {r.status_code}"
    data = r.json()
    items = data.get("results", []) if isinstance(data, dict) else []
    if not items:
        return None, "Introuvable sur RPOS"
    return items[0], None


def patcher_produit(session, base_url, product_id, payload):
    url = f"{base_url}/api/product/{product_id}/"
    try:
        r = session.patch(url, json=payload, timeout=TIMEOUT)
    except requests.RequestException as e:
        return False, str(e)
    if r.status_code not in (200, 202):
        return False, f"HTTP {r.status_code} — {r.text[:200]}"
    return True, None


STOP_FLAG_FILE = OUTPUT_DIR / "correction_stop.flag"


def demander_arret():
    """Pose un flag lu par run() entre deux articles pour arrêter proprement.
    Retourne True si le flag a bien été écrit (vérifié par relecture)."""
    STOP_FLAG_FILE.write_text("1", encoding="utf-8")
    return STOP_FLAG_FILE.exists()


def _stop_requested():
    return STOP_FLAG_FILE.exists()


def _clear_stop_flag():
    try:
        STOP_FLAG_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def run(magasins=None, apply=False, log=print):
    """
    Exécute la correction. `log` reçoit des lignes de texte (print par défaut,
    ou un callback pour les logs UI). Retourne le rapport final (dict).

    Quoi qu'il arrive (succès, erreur imprévue, arrêt demandé), le fichier de
    progression est toujours finalisé avec running=False — sinon l'UI reste
    bloquée sur "en cours" indéfiniment.
    """
    _clear_stop_flag()
    try:
        return _run_impl(magasins=magasins, apply=apply, log=log)
    except Exception as e:
        import traceback
        log(f"⛔ Erreur inattendue : {e}")
        log(traceback.format_exc())
        write_progress({
            "running": False, "finished": True, "erreur_fatale": str(e),
        })
        raise
    finally:
        _clear_stop_flag()


def _run_impl(magasins=None, apply=False, log=print):
    import django
    import django.apps
    root = BASE_DIR.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "verification_commande.settings")
    if not django.apps.apps.ready:
        django.setup()

    from entree_journal.models import ControleConformite, ArticleConformite
    from django.utils import timezone

    shop_to_pos = charger_shop_to_pos()
    session = make_session()

    controles_qs = ControleConformite.objects.filter(ko__gt=0)
    if magasins:
        controles_qs = controles_qs.filter(magasin__in=magasins)

    vus = set()
    controles = []
    for c in controles_qs.order_by("magasin", "-date_controle"):
        if c.magasin in vus:
            continue
        vus.add(c.magasin)
        controles.append(c)

    mode = "APPLY" if apply else "DRY-RUN"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log(f"=== Correction conformité — mode {mode} ===")

    total_magasins = len(controles)
    resume = {"total_traites": 0, "total_ignores": 0, "total_erreurs": 0}
    detail_magasins = []
    journal_articles = []  # détail article par article (pour rapport téléchargeable)

    write_progress({
        "running": True, "finished": False, "mode": mode,
        "magasin_done": 0, "magasin_total": total_magasins,
        "magasin_courant": None,
        "article_done": 0, "article_total": 0,
        "traites": 0, "ignores": 0, "erreurs": 0,
        "derniers": [],
    })

    derniers = []  # 20 dernières actions, pour affichage live
    arrete = False

    for idx_mag, controle in enumerate(controles):
        if _stop_requested():
            log("⏹ Arrêt demandé — correction interrompue.")
            arrete = True
            break

        magasin = controle.magasin
        pos_info = shop_to_pos.get(magasin.lstrip("0") or "0")
        if not pos_info:
            log(f"[{magasin}] ⚠️  Pas de mapping POS (magasin.json) — ignoré")
            continue

        articles_ko = list(ArticleConformite.objects.filter(
            controle=controle, resultat="KO", corrige=False
        ))
        if not articles_ko:
            continue

        log(f"[{magasin}] {pos_info['shop_name']} ({pos_info['pos_id']}) — {len(articles_ko)} KO non corrigés")

        mag_traites = mag_ignores = mag_erreurs = 0

        write_progress({
            "running": True, "finished": False, "mode": mode,
            "magasin_done": idx_mag, "magasin_total": total_magasins,
            "magasin_courant": magasin,
            "article_done": 0, "article_total": len(articles_ko),
            "traites": resume["total_traites"], "ignores": resume["total_ignores"],
            "erreurs": resume["total_erreurs"], "derniers": derniers[-20:],
        })

        for idx_art, art in enumerate(articles_ko):
            if _stop_requested():
                log("⏹ Arrêt demandé — correction interrompue.")
                arrete = True
                break

            action = determiner_action(art.motif)
            entry = {
                "magasin": magasin, "reference": art.reference, "libelle": art.libelle,
                "motif": art.motif, "action": None, "statut": None, "detail": None,
            }

            if action is None:
                entry["statut"] = "ignore"
                entry["detail"] = "Pas de règle automatique pour ce motif"
                mag_ignores += 1
                resume["total_ignores"] += 1
                journal_articles.append(entry)
                continue

            if action == "activer":
                payload = {"orderable": True, "blocked": False}
                libelle_action = "orderable=True, blocked=False"
            else:
                payload = {"orderable": False}
                libelle_action = "orderable=False"
            entry["action"] = libelle_action

            produit, err = trouver_produit(session, pos_info["url"], art.reference, pos_info["shop_id"])
            if produit is None:
                entry["statut"] = "erreur"
                entry["detail"] = err
                mag_erreurs += 1
                resume["total_erreurs"] += 1
                log(f"  ⛔ {art.reference}  → {err}")
                if apply:
                    art.correction_erreur = err
                    art.save(update_fields=["correction_erreur"])
                journal_articles.append(entry)
                derniers.append(entry)
                continue

            etat_actuel = f"orderable={produit.get('orderable')}, blocked={produit.get('blocked')}"
            entry["etat_avant"] = etat_actuel

            if apply:
                ok, err = patcher_produit(session, pos_info["url"], produit["id"], payload)
                if ok:
                    art.corrige = True
                    art.date_correction = timezone.now()
                    art.action_correction = libelle_action
                    art.correction_erreur = ""
                    art.save(update_fields=["corrige", "date_correction", "action_correction", "correction_erreur"])
                    entry["statut"] = "corrige"
                    mag_traites += 1
                    resume["total_traites"] += 1
                    log(f"  ✓ {art.reference}  {art.libelle[:40]:<40}  motif='{art.motif}'  ({etat_actuel}) → {libelle_action}")
                else:
                    entry["statut"] = "erreur"
                    entry["detail"] = err
                    art.correction_erreur = err
                    art.save(update_fields=["correction_erreur"])
                    mag_erreurs += 1
                    resume["total_erreurs"] += 1
                    log(f"  ⛔ {art.reference}  échec PATCH : {err}")
            else:
                entry["statut"] = "a_corriger"
                mag_traites += 1
                resume["total_traites"] += 1
                log(f"  · {art.reference}  {art.libelle[:40]:<40}  motif='{art.motif}'  ({etat_actuel}) → {libelle_action}")

            journal_articles.append(entry)
            derniers.append(entry)

            write_progress({
                "running": True, "finished": False, "mode": mode,
                "magasin_done": idx_mag, "magasin_total": total_magasins,
                "magasin_courant": magasin,
                "article_done": idx_art + 1, "article_total": len(articles_ko),
                "traites": resume["total_traites"], "ignores": resume["total_ignores"],
                "erreurs": resume["total_erreurs"], "derniers": derniers[-20:],
            })

        detail_magasins.append({
            "magasin": magasin, "shop_name": pos_info["shop_name"],
            "traites": mag_traites, "ignores": mag_ignores, "erreurs": mag_erreurs,
            "total_ko": len(articles_ko),
        })
        log("")

        if arrete:
            break

    _clear_stop_flag()

    rapport = {
        "mode": mode, "date": ts,
        "arrete": arrete,
        "total_traites": resume["total_traites"],
        "total_ignores": resume["total_ignores"],
        "total_erreurs": resume["total_erreurs"],
        "magasins": detail_magasins,
        "articles": journal_articles,
    }

    rapport_path = OUTPUT_DIR / f"correction_{ts}.json"
    try:
        with open(rapport_path, "w", encoding="utf-8") as f:
            json.dump(rapport, f, ensure_ascii=False, indent=2)
        rapport["filename"] = rapport_path.name
        rapport_filename = rapport_path.name
    except OSError as e:
        log(f"⚠️  Impossible d'écrire le rapport {rapport_path} : {e}")
        rapport["filename"] = None
        rapport_filename = None

    write_progress({
        "running": False, "finished": True, "mode": mode, "arrete": arrete,
        "magasin_done": total_magasins, "magasin_total": total_magasins,
        "magasin_courant": None,
        "traites": resume["total_traites"], "ignores": resume["total_ignores"],
        "erreurs": resume["total_erreurs"], "derniers": derniers[-20:],
        "rapport_filename": rapport_filename,
    })

    suffixe = " (interrompue)" if arrete else ""
    log(f"=== Résumé ({mode}){suffixe} : {resume['total_traites']} traités, "
        f"{resume['total_ignores']} ignorés (pas de règle), {resume['total_erreurs']} erreurs ===")
    if not apply:
        log("Aucune modification écrite. Relancer avec --apply pour appliquer.")

    return rapport


def corriger_un_article(article_id, apply=True):
    """
    Corrige un seul ArticleConformite (par son id). Retourne un dict résultat.
    Utilisé pour la correction unitaire depuis l'UI (bouton par ligne).
    """
    import django
    import django.apps
    root = BASE_DIR.parent
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "verification_commande.settings")
    if not django.apps.apps.ready:
        django.setup()

    from entree_journal.models import ArticleConformite
    from django.utils import timezone

    try:
        art = ArticleConformite.objects.select_related("controle").get(id=article_id)
    except ArticleConformite.DoesNotExist:
        return {"ok": False, "error": "Article introuvable"}

    if art.resultat != "KO":
        return {"ok": False, "error": "Cet article n'est pas en KO"}

    action = determiner_action(art.motif)
    if action is None:
        return {"ok": False, "error": f"Pas de règle automatique pour le motif '{art.motif}'"}

    shop_to_pos = charger_shop_to_pos()
    magasin = art.controle.magasin
    pos_info = shop_to_pos.get(magasin.lstrip("0") or "0")
    if not pos_info:
        return {"ok": False, "error": f"Pas de mapping POS pour le magasin {magasin}"}

    if action == "activer":
        payload = {"orderable": True, "blocked": False}
        libelle_action = "orderable=True, blocked=False"
    else:
        payload = {"orderable": False}
        libelle_action = "orderable=False"

    session = make_session()
    produit, err = trouver_produit(session, pos_info["url"], art.reference, pos_info["shop_id"])
    if produit is None:
        if apply:
            art.correction_erreur = err
            art.save(update_fields=["correction_erreur"])
        return {"ok": False, "error": err}

    if not apply:
        return {
            "ok": True, "dry_run": True,
            "action": libelle_action,
            "etat_avant": f"orderable={produit.get('orderable')}, blocked={produit.get('blocked')}",
        }

    ok, err = patcher_produit(session, pos_info["url"], produit["id"], payload)
    if not ok:
        art.correction_erreur = err
        art.save(update_fields=["correction_erreur"])
        return {"ok": False, "error": err}

    art.corrige = True
    art.date_correction = timezone.now()
    art.action_correction = libelle_action
    art.correction_erreur = ""
    # Reflète immédiatement l'effet de l'action dans l'affichage, sans attendre
    # le prochain contrôle de conformité : l'action qu'on vient d'appliquer
    # résout par construction l'anomalie détectée par ce motif.
    art.present_asten = (action == "activer")
    art.resultat = "OK"
    art.motif = f"Corrigé — {libelle_action}"
    art.save(update_fields=[
        "corrige", "date_correction", "action_correction", "correction_erreur",
        "present_asten", "resultat", "motif",
    ])
    return {
        "ok": True, "action": libelle_action,
        "present_asten": art.present_asten, "resultat": art.resultat, "motif": art.motif,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("magasins", nargs="*", help="Codes magasins à traiter (vide = tous)")
    parser.add_argument("--apply", action="store_true", help="Applique réellement les corrections (sinon dry-run)")
    args = parser.parse_args()
    run(magasins=args.magasins or None, apply=args.apply, log=print)


if __name__ == "__main__":
    main()
