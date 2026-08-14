#!/bin/bash
set -e

echo "==> Attente PostgreSQL..."
until python -c "
import os, psycopg
try:
    psycopg.connect(
        host=os.environ.get('POSTGRES_HOST','db'),
        port=os.environ.get('POSTGRES_PORT','5432'),
        dbname=os.environ.get('POSTGRES_DB','traitement_n8n'),
        user=os.environ.get('POSTGRES_USER','postgres'),
        password=os.environ.get('POSTGRES_PASSWORD','postgres'),
    ).close()
    print('OK')
except Exception as e:
    raise SystemExit(1)
" 2>/dev/null; do
    echo "   PostgreSQL pas encore prêt, attente 2s..."
    sleep 2
done
echo "==> PostgreSQL OK"

echo "==> Migrations..."
python manage.py migrate --noinput

echo "==> Collectstatic..."
python manage.py collectstatic --noinput --clear

echo "==> Import initial des fichiers déposés dans /app/media/..."
python manage.py auto_import || echo "   (import initial ignoré ou vide)"

echo "==> Démarrage Gunicorn..."
exec gunicorn verification_commande.wsgi:application \
    --bind 0.0.0.0:8000 \
    --workers 2 \
    --timeout 300 \
    --access-logfile - \
    --error-logfile -
