#!/usr/bin/env bash
set -uo pipefail

# ---- Détection environnement (dev local vs serveur) ----
if [[ "$(hostname)" == *"prosuma"* ]] || [[ -d /opt/Projet_N8N ]]; then
  PROJECT_DIR="/opt/Projet_N8N"
  VENV_ACTIVATE="$PROJECT_DIR/venv/bin/activate"
  GUNICORN_CMD="gunicorn verification_commande.wsgi:application --bind 0.0.0.0:1212 --workers 9 --daemon"
  IS_SERVER=1
else
  PROJECT_DIR="/home/youssef/Documents/traitement_n8n"
  VENV_ACTIVATE="$PROJECT_DIR/env/bin/activate"
  GUNICORN_CMD=""
  IS_SERVER=0
fi

# ---- Points de montage ----
MOUNT_AUTOMATE_IC="/mnt/automate-ic"
MOUNT_C_SHARE="/mnt/partage-c"
MOUNT_SHARE="/mnt/partage-share"
MOUNT_ASTEN="/mnt/asten"

# ---- Partages SMB ----
SHARE_AUTOMATE_IC="//10.0.70.150/automate-ic"
SHARE_C="//10.0.70.204/c"
SHARE_SHARE="//10.0.70.169/share"
SHARE_ASTEN="//10.0.70.104/d"

# Chemin dans le partage vers le Backup articlespoints
SMB_BACKUP_SUBPATH="Cegid/AstenProd/ReferentielPrecom/Backup"

# ---- Options SMB ----
SMB_DOMAIN="${SMB_DOMAIN:-PROSUMA}"
SMB_USER="${SMB_USER:-}"
SMB_PASS="${SMB_PASS:-}"
SMB_OPTS_BASE="iocharset=utf8,vers=3.0,_netdev"

# ---- Helpers ----
prompt_credentials() {
  if [[ -z "$SMB_USER" ]]; then
    read -r -p "SMB username: " SMB_USER
  fi
  if [[ -z "$SMB_PASS" ]]; then
    read -r -s -p "SMB password: " SMB_PASS
    echo
  fi
}

mount_share() {
  local share="$1"
  local mount_point="$2"

  sudo mkdir -p "$mount_point" 2>/dev/null || true
  if mountpoint -q "$mount_point" 2>/dev/null; then
    echo "  [OK] Déjà monté : $mount_point"
    return 0
  fi

  if sudo mount -t cifs "$share" "$mount_point" \
    -o "username=${SMB_USER},password=${SMB_PASS},domain=${SMB_DOMAIN},${SMB_OPTS_BASE}" 2>/dev/null; then
    echo "  [✓]  Monté : $share → $mount_point"
  else
    echo "  [✗]  Échec  : $share → $mount_point (hôte inaccessible ?)"
  fi
}

set_smb_backup_path() {
  local backup_path="${MOUNT_ASTEN}/${SMB_BACKUP_SUBPATH}"
  local env_file="${PROJECT_DIR}/config.env"

  if [[ -f "$env_file" ]]; then
    if grep -q "^SMB_BACKUP_PATH=" "$env_file"; then
      sed -i "s|^SMB_BACKUP_PATH=.*|SMB_BACKUP_PATH=${backup_path}|" "$env_file"
    else
      echo "SMB_BACKUP_PATH=${backup_path}" >> "$env_file"
    fi
    echo "  [✓]  SMB_BACKUP_PATH mis à jour dans config.env"
  else
    echo "  [!]  config.env introuvable — variable non mise à jour"
  fi
}

install_cron() {
  local cron_line="0 6 * * * SMB_BACKUP_PATH=${MOUNT_ASTEN}/${SMB_BACKUP_SUBPATH} ${PROJECT_DIR}/venv/bin/python3 ${PROJECT_DIR}/Entree_journal/verif_points.py >> ${PROJECT_DIR}/Entree_journal/logs/cron_verif.log 2>&1"
  if crontab -l 2>/dev/null | grep -q "verif_points.py"; then
    echo "  [OK] Cron verif_points déjà installé."
  else
    (crontab -l 2>/dev/null; echo "$cron_line") | crontab -
    echo "  [✓]  Cron installé : vérification points fidélité tous les jours à 06h00"
  fi
}

# ---- Montages ----
echo ""
echo "=== Montage des partages SMB ==="
prompt_credentials
mount_share "$SHARE_AUTOMATE_IC" "$MOUNT_AUTOMATE_IC"
mount_share "$SHARE_C"           "$MOUNT_C_SHARE"
mount_share "$SHARE_SHARE"       "$MOUNT_SHARE"
mount_share "$SHARE_ASTEN"       "$MOUNT_ASTEN"

# Mettre à jour SMB_BACKUP_PATH dans config.env
echo ""
echo "=== Configuration SMB_BACKUP_PATH ==="
set_smb_backup_path

# Vérifier que le fichier articlespoints est accessible
BACKUP_DIR="${MOUNT_ASTEN}/${SMB_BACKUP_SUBPATH}"
if ls "${BACKUP_DIR}/999_articlespoints_"*.csv 1>/dev/null 2>&1; then
  latest=$(ls -t "${BACKUP_DIR}/999_articlespoints_"*.csv | head -1)
  echo "  [✓]  Fichier articlespoints trouvé : $(basename "$latest")"
else
  echo "  [!]  Aucun fichier 999_articlespoints_*.csv trouvé dans $BACKUP_DIR"
fi

# ---- Cron (serveur uniquement) ----
if [[ $IS_SERVER -eq 1 ]]; then
  echo ""
  echo "=== Installation cron verif_points (06h00) ==="
  install_cron
fi

# ---- Démarrage serveur Django ----
echo ""
if [[ $IS_SERVER -eq 1 ]]; then
  read -r -p "Redémarrer Gunicorn ? (o/N): " START_SERVER
else
  read -r -p "Démarrer le serveur Django (runserver) ? (o/N): " START_SERVER
fi

START_SERVER="${START_SERVER,,}"
if [[ "$START_SERVER" == "o" || "$START_SERVER" == "oui" || "$START_SERVER" == "y" || "$START_SERVER" == "yes" ]]; then
  cd "$PROJECT_DIR"
  source "$VENV_ACTIVATE"
  if [[ $IS_SERVER -eq 1 ]]; then
    pkill -f "gunicorn.*1212" 2>/dev/null || true
    sleep 1
    exec $GUNICORN_CMD
  else
    exec python3 manage.py runserver 0.0.0.0:1212
  fi
else
  echo "Montages terminés. Serveur non démarré."
fi
