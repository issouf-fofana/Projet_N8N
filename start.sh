#!/bin/bash

cd "$(dirname "$0")"

echo "Démarrage de l'application..."

# Vérifier que PostgreSQL tourne
if ! systemctl is-active --quiet postgresql; then
    echo "PostgreSQL arrêté, démarrage..."
    sudo systemctl start postgresql
fi

# Activer l'environnement virtuel et lancer le serveur
source env/bin/activate
python3 manage.py runserver 8000
#echo "Application démarrée avec succès!"