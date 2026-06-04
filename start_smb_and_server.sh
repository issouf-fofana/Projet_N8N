#!/usr/bin/env bash
set -uo pipefail

# ---- Config ----
PROJECT_DIR="/home/youssef/Documents/traitement_n8n"

# Mount points
MOUNT_AUTOMATE_IC="/mnt/automate-ic"
MOUNT_C_SHARE="/mnt/partage-c"
MOUNT_SHARE="/mnt/partage-share"
MOUNT_ASTEN="/mnt/asten"

# SMB shares
SHARE_AUTOMATE_IC="//10.0.70.150/automate-ic"
SHARE_C="//10.0.70.204/c"
SHARE_SHARE="//10.0.70.169/share"
SHARE_ASTEN="//10.0.70.104/d"

# SMB options
SMB_DOMAIN="${SMB_DOMAIN:-PROSUMA}"
SMB_USER="${SMB_USER:-}"
SMB_PASS="${SMB_PASS:-}"
SMB_OPTS_BASE="iocharset=utf8,vers=3.0"

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
    echo "Déjà monté: $mount_point"
    return 0
  fi

  if sudo mount -t cifs "$share" "$mount_point" \
    -o "username=${SMB_USER},password=${SMB_PASS},domain=${SMB_DOMAIN},${SMB_OPTS_BASE}" 2>/dev/null; then
    echo "✓ Monté: $share -> $mount_point"
  else
    echo "✗ Echec montage (hôte inaccessible ?): $share -> $mount_point"
  fi
}

# ---- Mount SMB shares ----
prompt_credentials
mount_share "$SHARE_AUTOMATE_IC" "$MOUNT_AUTOMATE_IC"
mount_share "$SHARE_C" "$MOUNT_C_SHARE"
mount_share "$SHARE_SHARE" "$MOUNT_SHARE"
mount_share "$SHARE_ASTEN" "$MOUNT_ASTEN"

# ---- Optional Django server ----
read -r -p "Démarrer le serveur Django ? (o/N): " START_SERVER
START_SERVER="${START_SERVER,,}"
if [[ "$START_SERVER" == "o" || "$START_SERVER" == "oui" || "$START_SERVER" == "y" || "$START_SERVER" == "yes" ]]; then
  cd "$PROJECT_DIR"
  source "$PROJECT_DIR/env/bin/activate"
  exec python3 manage.py runserver 0.0.0.0:1212
else
  echo "Montages terminés. Serveur non démarré."
fi

