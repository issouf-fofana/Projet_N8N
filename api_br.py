import argparse
import csv
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
import platform
from time import perf_counter

import requests
import urllib3
from dotenv import load_dotenv


# =========================
# CONFIGURATION
# =========================

DEFAULT_POS_COUNT = 18
DATE_FMT = "%Y-%m-%d"


# =========================
# UTILS
# =========================


def format_date(value):
    if not value:
        return ""
    try:
        return datetime.fromisoformat(value.replace("Z", "")).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return ""


def safe_get(obj, key):
    if isinstance(obj, dict):
        return obj.get(key, "")
    return ""


def parse_date(value):
    try:
        return datetime.strptime(value, DATE_FMT).date()
    except ValueError:
        raise ValueError(f"Date invalide: {value} (format attendu: {DATE_FMT})")


def get_os_key():
    system = platform.system().lower()
    if "windows" in system:
        return "windows"
    return "linux"


def env_bool(value, default=False):
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y"}


def get_env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and (value is None or str(value).strip() == ""):
        raise RuntimeError(f"Variable d'environnement manquante: {name}")
    return value


def get_pos_list():
    raw = os.getenv("POS_LIST", "").strip()
    if raw:
        return [item.strip() for item in raw.split(",") if item.strip()]
    return [f"pos{i}-prod-prosuma.prosuma.pos" for i in range(1, DEFAULT_POS_COUNT + 1)]


def parse_pos_choice(value, all_hosts):
    choice = value.strip().lower()
    if choice in {"tous", "all"}:
        return list(all_hosts), "tous"
    try:
        index = int(choice)
    except ValueError:
        raise ValueError("Selection POS invalide (utiliser un numero ou 'tous')")
    if not (1 <= index <= len(all_hosts)):
        raise ValueError("Numero POS hors limite")
    return [all_hosts[index - 1]], str(index)


def get_export_dir(reception_type):
    os_key = get_os_key()
    if reception_type == "central":
        env_key = "EXPORT_DIR_CENTRAL_WINDOWS" if os_key == "windows" else "EXPORT_DIR_CENTRAL_LINUX"
    else:
        env_key = "EXPORT_DIR_DIRECT_WINDOWS" if os_key == "windows" else "EXPORT_DIR_DIRECT_LINUX"

    path_value = get_env(env_key, required=True)
    export_path = Path(path_value)
    export_path.mkdir(parents=True, exist_ok=True)
    return export_path


def print_summary(summary):
    print("\nPARAMETRES SELECTIONNES")
    print("----------------------")
    for label, value in summary:
        print(f"- {label}: {value}")


def confirm_action():
    response = input("Confirmer l'extraction ? (o/n) : ").strip().lower()
    return response in {"o", "oui", "y", "yes"}


# =========================
# SESSION
# =========================


def create_session(pos_host, default_user, default_pass, pos4_user, pos4_pass, verify_tls):
    session = requests.Session()
    if pos_host.startswith("pos4-"):
        session.auth = (pos4_user, pos4_pass)
    else:
        session.auth = (default_user, default_pass)
    session.verify = verify_tls
    return session


# =========================
# API
# =========================


def request_json(session, url, params, timeout, debug_api=False):
    start = perf_counter()
    try:
        response = session.get(url, params=params, timeout=timeout)
        elapsed = perf_counter() - start
        if debug_api:
            print(f"[API] GET {url} status={response.status_code} {elapsed:.2f}s params={params}")
        if response.status_code == 404:
            return None, 404
        if response.status_code != 200:
            return None, response.status_code
        return response.json(), 200
    except requests.RequestException as exc:
        elapsed = perf_counter() - start
        if debug_api:
            print(f"[API] GET {url} error={exc.__class__.__name__} {elapsed:.2f}s params={params}")
        return None, 0


def get_delivery_valorisation(session, pos_host, delivery_id, page_size, timeout, debug_api=False):
    url = f"https://{pos_host}/api/delivery_line/"
    params = {"delivery": delivery_id, "page_size": page_size}

    total_ht = 0.0
    total_ttc = 0.0
    page = 1

    while True:
        params["page"] = page
        data, status = request_json(session, url, params, timeout, debug_api)
        if status != 200 or not data:
            break

        lines = data.get("results", [])
        if not lines:
            break

        for line in lines:
            try:
                total_ht += float(line.get("valorisation") or 0)
                total_ttc += float(line.get("valorisation_incl_tax") or 0)
            except Exception:
                continue

        page += 1

    return round(total_ht, 2), round(total_ttc, 2)


# =========================
# EXPORT
# =========================


def fetch_receptions(
    session, pos_host, date_start, date_end, filter_params, page_size, timeout, debug_api=False
):
    base_url = f"https://{pos_host}/api/delivery/"
    params = {
        "page_size": page_size,
        "date_0": date_start,
        "date_1": date_end,
    }
    params.update(filter_params)

    receptions = []
    page = 1

    while True:
        params["page"] = page
        data, status = request_json(session, base_url, params, timeout, debug_api)
        if status == 404:
            break
        if status != 200 or not data:
            print(f"Erreur API sur {pos_host} (status={status})")
            break

        results = data.get("results", [])
        if not results:
            break

        receptions.extend(results)
        page += 1

    return receptions


def export_receptions_for_pos(
    pos_host,
    date_start,
    date_end,
    filter_params,
    label_type,
    export_dir,
    page_size,
    timeout,
    default_user,
    default_pass,
    pos4_user,
    pos4_pass,
    verify_tls,
    debug_api,
):
    print(f"\nTraitement {pos_host}")
    session = create_session(pos_host, default_user, default_pass, pos4_user, pos4_pass, verify_tls)

    receptions = fetch_receptions(
        session, pos_host, date_start, date_end, filter_params, page_size, timeout, debug_api
    )
    print(f"{len(receptions)} receptions recuperees")
    if not receptions:
        return

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filepath = export_dir / f"receptions_{label_type}_{pos_host}_{timestamp}.csv"

    with filepath.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(
            [
            "Id",
            "Magasin",
            "Nom affiché",
            "N° bon livraison",
            "Type réception",
            "Fournisseur",
            "Commande fournisseur",
            "Statut commande",
            "Validée",
            "Date réception",
            "Date création",
            "Date validation",
            "Quantité totale",
            "Valorisation HT",
            "Valorisation TTC",
]
        )

        for rcp in receptions:
            valor_ht, valor_ttc = get_delivery_valorisation(
                session, pos_host, rcp.get("id"), page_size, timeout, debug_api
            )

            row = [
                rcp.get("id", ""),
                safe_get(rcp.get("shop"), "reference"),
                rcp.get("name", ""),
                rcp.get("delivery_number", ""),
                rcp.get("delivery_type", ""),
                safe_get(rcp.get("supplier"), "name"),
                safe_get(rcp.get("order"), "reference"),
                rcp.get("order_status", ""),
                "O" if rcp.get("validated") else "N",
                format_date(rcp.get("date")),
                format_date(rcp.get("created_at")),
                format_date(rcp.get("validation_date")),
                rcp.get("total_quantity", ""),
                valor_ht,
                valor_ttc,
            ]
            writer.writerow(row)

    print(f"Fichier genere : {filepath}")


# =========================
# MAIN
# =========================


def build_args():
    parser = argparse.ArgumentParser(description="Export receptions BRS")
    parser.add_argument("--type", choices=["central", "direct"], help="Type de reception")
    parser.add_argument(
        "--preset",
        choices=["yesterday_today"],
        help="Periode predefinie (hier a aujourd'hui)",
    )
    parser.add_argument("--pos", help="Selection POS: 'tous' ou un numero")
    parser.add_argument("--date-start", help="Date debut (YYYY-MM-DD)")
    parser.add_argument("--date-end", help="Date fin (YYYY-MM-DD)")
    parser.add_argument("--no-confirm", action="store_true", help="Lancer sans confirmation")
    return parser.parse_args()


def resolve_period(args):
    if args.preset == "yesterday_today":
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        return yesterday, today

    if args.date_start or args.date_end:
        if not (args.date_start and args.date_end):
            raise ValueError("Les dates debut et fin doivent etre toutes les deux renseignees")
        return parse_date(args.date_start), parse_date(args.date_end)

    return None, None


def prompt_type():
    print("\nTYPE DE RECEPTION A EXTRAIRE")
    print("1 - Receptions centrales")
    print("2 - Receptions directes")
    choice = input("Votre choix (1/2) : ").strip()
    if choice == "1":
        return "central"
    if choice == "2":
        return "direct"
    raise ValueError("Choix invalide")


def prompt_period():
    print("\nPERIODE D'EXPORT DES RECEPTIONS")
    print("1 - Hier a aujourd'hui")
    print("2 - Personnalisee")
    choice = input("Votre choix (1/2) : ").strip()

    if choice == "1":
        today = datetime.now().date()
        yesterday = today - timedelta(days=1)
        return yesterday, today
    if choice == "2":
        date_debut = parse_date(input("Date debut (YYYY-MM-DD) : ").strip())
        date_fin = parse_date(input("Date fin   (YYYY-MM-DD) : ").strip())
        return date_debut, date_fin
    raise ValueError("Choix invalide")


def prompt_pos_selection(all_hosts):
    print("\nSELECTION DES POS")
    print("Saisir un numero (1 a 18) ou 'tous'")
    choice = input("Votre choix : ").strip()
    return parse_pos_choice(choice, all_hosts)


def main():
    load_dotenv()

    default_user = get_env("DEFAULT_USERNAME", required=True)
    default_pass = get_env("DEFAULT_PASSWORD", required=True)
    pos4_user = get_env("POS4_USERNAME", required=True)
    pos4_pass = get_env("POS4_PASSWORD", required=True)

    verify_tls = env_bool(get_env("VERIFY_TLS", "false"))
    page_size = int(get_env("PAGE_SIZE", "500"))
    timeout = float(get_env("REQUEST_TIMEOUT", "30"))
    debug_api = env_bool(get_env("DEBUG_API", "false"))

    if not verify_tls:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    args = build_args()

    interactive = not (
        args.type and (args.preset or (args.date_start and args.date_end))
    )

    while True:
        if args.type:
            reception_type = args.type
        else:
            reception_type = prompt_type()

        date_start, date_end = resolve_period(args)
        if not date_start or not date_end:
            date_start, date_end = prompt_period()

        if date_end < date_start:
            raise ValueError("La date de fin ne peut pas etre avant la date de debut")

        if reception_type == "central":
            filter_params = {"is_central": "true"}
            label_type = "centrales"
        else:
            filter_params = {"is_direct": "true"}
            label_type = "directes"

        export_dir = get_export_dir(reception_type)
        all_hosts = get_pos_list()
        if args.pos:
            pos_list, pos_label = parse_pos_choice(args.pos, all_hosts)
        else:
            pos_list, pos_label = prompt_pos_selection(all_hosts)

        date_start_str = f"{date_start}T00:00:00"
        date_end_str = f"{date_end}T23:59:59"

        summary = [
            ("OS", get_os_key()),
            ("Type", reception_type),
            ("POS selection", pos_label),
            ("POS hosts", ", ".join(pos_list)),
            ("Date debut", date_start.strftime(DATE_FMT)),
            ("Date fin", date_end.strftime(DATE_FMT)),
            ("Export dir", export_dir),
            ("POS", f"{len(pos_list)} POS"),
            ("Page size", page_size),
            ("Timeout (s)", timeout),
            ("TLS verify", verify_tls),
            ("Debug API", debug_api),
        ]
        print_summary(summary)

        if args.no_confirm:
            break

        if confirm_action():
            break

        if not interactive:
            print("Operation annulee.")
            return

        retry = input("Reconfigurer les parametres ? (o/n) : ").strip().lower()
        if retry not in {"o", "oui", "y", "yes"}:
            print("Operation annulee.")
            return

    for pos in pos_list:
        export_receptions_for_pos(
            pos,
            date_start_str,
            date_end_str,
            filter_params,
            label_type,
            export_dir,
            page_size,
            timeout,
            default_user,
            default_pass,
            pos4_user,
            pos4_pass,
            verify_tls,
            debug_api,
        )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"Erreur: {exc}")
        sys.exit(1)