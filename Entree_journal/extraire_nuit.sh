#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
#  extraire_nuit.sh — Extraction automatique : hier 20h00 → aujourd'hui 01h40
#  Usage : ./extraire_nuit.sh [pos1 pos2 ...]   (sans argument = tous les POS)
# ─────────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${SCRIPT_DIR}/.venv/bin/python"

if [ ! -f "$PYTHON" ]; then
    PYTHON="$(which python3 || which python)"
fi

# ── Calcul automatique des dates ──────────────────────────────────────────────
# Hier à 20:00:00
DATE_DEBUT="$(date -d 'yesterday 20:00:00' '+%Y-%m-%dT%H:%M:%S')"
# Aujourd'hui à 01:40:00
DATE_FIN="$(date -d 'today 01:40:00' '+%Y-%m-%dT%H:%M:%S')"

# ── Couleurs ──────────────────────────────────────────────────────────────────
CYAN="\033[1;36m"
GREEN="\033[1;32m"
RED="\033[1;31m"
RESET="\033[0m"

echo ""
echo -e "${CYAN}╔══════════════════════════════════════════════════════╗${RESET}"
echo -e "${CYAN}║     Extraction Nuit — Journal POS automatique        ║${RESET}"
echo -e "${CYAN}╚══════════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  Début  : ${GREEN}${DATE_DEBUT}${RESET}"
echo -e "  Fin    : ${GREEN}${DATE_FIN}${RESET}"

# ── Construction de la commande ───────────────────────────────────────────────
CMD=("$PYTHON" "$SCRIPT_DIR/collector.py"
     "--date-debut" "$DATE_DEBUT"
     "--date-fin"   "$DATE_FIN")

# Si des POS sont passés en argument, on les utilise ; sinon tous
if [ $# -gt 0 ]; then
    echo -e "  POS    : ${GREEN}$*${RESET}"
    CMD+=("--pos" "$@")
else
    echo -e "  POS    : ${GREEN}tous${RESET}"
fi

echo ""
echo -e "${CYAN}═══ Démarrage de l'extraction ═══${RESET}"
echo ""

cd "$SCRIPT_DIR"
"${CMD[@]}"
EXIT_CODE=$?

echo ""
if [ $EXIT_CODE -eq 0 ]; then
    echo -e "${GREEN}✓ Extraction terminée. Fichiers dans : $(dirname ${SCRIPT_DIR})/media/entree_journal/${RESET}"
else
    echo -e "${RED}✗ Erreur lors de l'extraction (code $EXIT_CODE).${RESET}"
fi
echo ""

exit $EXIT_CODE
