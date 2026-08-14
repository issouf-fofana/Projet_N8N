FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=verification_commande.settings

WORKDIR /app

# Dépendances système (psycopg, openpyxl, samba/smb)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc g++ \
    smbclient cifs-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Collecter les fichiers statiques
RUN python manage.py collectstatic --noinput || true

EXPOSE 8000

# Gunicorn : 2 workers, timeout 300s (import lourd)
CMD ["gunicorn", "verification_commande.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "2", \
     "--timeout", "300", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
