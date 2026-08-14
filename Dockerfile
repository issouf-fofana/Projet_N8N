FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=verification_commande.settings

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc g++ \
    smbclient cifs-utils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Cache-busting — invalide le layer COPY à chaque build
ARG CACHEBUST=1
COPY . .

RUN mkdir -p /app/media/commande_asten \
             /app/media/commande_cyrus \
             /app/media/commande_gpv \
             /app/media/commande_legend \
             /app/media/br_asten \
             /app/media/br_ic \
             /app/media/anomalie_br \
             /app/media/facture_asten \
             /app/media/facture_cyrus \
             /app/media/facture_backup \
             /app/media/tickets \
             /app/media/remontees \
             /app/media/entree_journal \
             /app/.cache

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
