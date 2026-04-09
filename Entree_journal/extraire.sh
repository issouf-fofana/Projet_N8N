#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  extraire.sh — Extraction journal POS avec fenêtre personnalisée
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${SCRIPT_DIR}/.venv/bin/python"

# Fallback si pas de venv local
if [ ! -f "$PYTHON" ]; then
    PYTHON="$(which python3 || which python)"
fi

# ── Couleurs ─────────────────────────────────────────────────────────────────
BOLD="\033[1m"
CYAN="\033[1;36m"
GREEN="\033[1;32m"
YELLOW="\033[1;33m"
RED="\033[1;31m"
RESET="\033[0m"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}║       Extraction Journal POS — Entrée Journal        ║${RESET}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════╝${RESET}"
echo ""

# ── Validation format date ────────────────────────────────────────────────────
validate_datetime() {
    local input="$1"
    # Accepte YYYY-MM-DD ou YYYY-MM-DDTHH:MM:SS ou YYYY-MM-DD HH:MM:SS
    if [[ "$input" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}([ T][0-9]{2}:[0-9]{2}(:[0-9]{2})?)?$ ]]; then
        return 0
    fi
    return 1
}

# ── Saisie date début ─────────────────────────────────────────────────────────
while true; do
    echo -e "${BOLD}Date de début${RESET}"
    echo -e "  Format : ${YELLOW}YYYY-MM-DD${RESET} ou ${YELLOW}YYYY-MM-DDTHH:MM:SS${RESET}"
    echo -e "  Exemple: ${YELLOW}2026-03-01${RESET}  ou  ${YELLOW}2026-03-01T20:00:00${RESET}"
    read -rp "  → " DATE_DEBUT
    DATE_DEBUT="${DATE_DEBUT// /T}"   # remplace espace par T si besoin

    if validate_datetime "$DATE_DEBUT"; then
        break
    else
        echo -e "  ${RED}Format invalide. Réessayez.${RESET}\n"
    fi
done

echo ""

# ── Saisie date fin ───────────────────────────────────────────────────────────
while true; do
    echo -e "${BOLD}Date de fin${RESET}"
    echo -e "  Format : ${YELLOW}YYYY-MM-DD${RESET} ou ${YELLOW}YYYY-MM-DDTHH:MM:SS${RESET}"
    echo -e "  Exemple: ${YELLOW}2026-03-25${RESET}  ou  ${YELLOW}2026-03-25T01:40:00${RESET}"
    read -rp "  → " DATE_FIN
    DATE_FIN="${DATE_FIN// /T}"

    if validate_datetime "$DATE_FIN"; then
        break
    else
        echo -e "  ${RED}Format invalide. Réessayez.${RESET}\n"
    fi
done

echo ""

# ── Saisie POS (optionnel) ────────────────────────────────────────────────────
echo -e "${BOLD}POS à extraire${RESET} (laisser vide = tous)"
echo -e "  Disponibles : pos1 pos2 pos3 pos4 pos5 pos6 pos7 pos8 pos9 pos10 pos11 pos12 pos13 pos14 pos15 pos16 pos17 pos18"
read -rp "  → " POS_INPUT

echo ""
echo -e "${CYAN}────────────────────────────────────────────────────────${RESET}"
echo -e "  Début  : ${GREEN}${DATE_DEBUT}${RESET}"
echo -e "  Fin    : ${GREEN}${DATE_FIN}${RESET}"
if [ -n "$POS_INPUT" ]; then
    echo -e "  POS    : ${GREEN}${POS_INPUT}${RESET}"
else
    echo -e "  POS    : ${GREEN}tous${RESET}"
fi
echo -e "${CYAN}────────────────────────────────────────────────────────${RESET}"
echo ""
read -rp "Confirmer ? [O/n] " CONFIRM
CONFIRM="${CONFIRM:-O}"

if [[ ! "$CONFIRM" =~ ^[OoYy]$ ]]; then
    echo -e "\n${YELLOW}Annulé.${RESET}\n"
    exit 0
fi

echo ""

# ── Construction des arguments ────────────────────────────────────────────────
CMD=("$PYTHON" "$SCRIPT_DIR/collector.py"
     "--date-debut" "$DATE_DEBUT"
     "--date-fin"   "$DATE_FIN")

if [ -n "$POS_INPUT" ]; then
    # Découper les POS séparés par espaces
    read -ra POS_ARRAY <<< "$POS_INPUT"
    CMD+=("--pos" "${POS_ARRAY[@]}")
fi

# ── Lancement ─────────────────────────────────────────────────────────────────
echo -e "${CYAN}═══ Démarrage de l'extraction ═══${RESET}\n"
cd "$SCRIPT_DIR"
"${CMD[@]}"
EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✓ Extraction terminée. Fichiers dans : ${SCRIPT_DIR}/data/${RESET}"
else
    echo -e "${RED}✗ Erreur lors de l'extraction (code $EXIT_CODE).${RESET}"
fi
echo ""
