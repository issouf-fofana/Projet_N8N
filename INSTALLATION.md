# Guide d'installation - Plateforme de Contrôle

## Prérequis

- Python 3.8+
- PostgreSQL 12+
- pip

## Installation

### 1. Cloner et préparer l'environnement

```bash
cd /home/youssef/Documents/traitement_n8n
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configuration de la base de données

Créer une base de données PostgreSQL :

```sql
CREATE DATABASE controle_platform;
CREATE USER controle_user WITH PASSWORD 'votre_mot_de_passe';
GRANT ALL PRIVILEGES ON DATABASE controle_platform TO controle_user;
```

### 3. Configuration des variables d'environnement

Créer un fichier `.env` à la racine du projet :

```bash
# Configuration de la base de données PostgreSQL
DB_NAME=controle_platform
DB_USER=controle_user
DB_PASSWORD=votre_mot_de_passe
DB_HOST=localhost
DB_PORT=5432

# Chemin de base pour les extractions SMB
# En production: /mnt/windows/extractions
# En développement: laisser vide pour utiliser les dossiers locaux
SMB_BASE_PATH=/mnt/windows/extractions

# Clé secrète Django (générer une nouvelle clé pour la production)
SECRET_KEY=django-insecure-5#l@&c&9t9aa7lkbe23wmuxxk^@70w)54c8nl^=4d@lv+22mb+

# Mode debug (False en production)
DEBUG=True
```

### 4. Initialisation de la base de données

```bash
python manage.py makemigrations
python manage.py migrate
python manage.py createsuperuser
```

### 5. Test de la commande de traitement

```bash
# Traiter les commandes
python manage.py process_commande

# Avec une période spécifique
python manage.py process_commande --periode 2026-01

# Forcer le retraitement
python manage.py process_commande --force
```

### 6. Lancer le serveur de développement

```bash
python manage.py runserver
```

Accéder à l'interface d'administration : http://127.0.0.1:8000/admin/

## Structure des dossiers

```
extractions/
   ├── commande_auto/      # Fichiers CSV automatiques (Source A)
   ├── commande_manual/    # Fichiers CSV manuels (Source B)
   ├── facture/
   ├── br/
   ├── legend/
   └── archive/            # Fichiers traités archivés
```

## Automatisation

### Avec n8n

Créer un workflow n8n qui appelle :

```bash
python /chemin/vers/projet/manage.py process_commande
```

### Avec Cron

Ajouter dans crontab :

```bash
# Traiter les commandes tous les jours à 2h du matin
0 2 * * * cd /home/youssef/Documents/traitement_n8n && /chemin/vers/venv/bin/python manage.py process_commande
```

## Commandes disponibles

- `python manage.py process_commande` : Traite les fichiers de commande
- `python manage.py process_facture` : À venir
- `python manage.py process_br` : À venir
- `python manage.py process_legend` : À venir

