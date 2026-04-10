# Plateforme de Vérification d'Intégration des Commandes Asten → Cyrus

Plateforme Django + PostgreSQL pour le contrôle et le rapprochement automatique entre les systèmes Asten et Cyrus.

## 🚀 Installation

### Prérequis

- Python 3.12+
- SQLite3 (inclus avec Python)
- Virtualenv (recommandé)

### Étapes d'installation

1. **Cloner ou naviguer vers le projet**
```bash
cd /home/youssef/Documents/traitement_n8n
```

2. **Activer l'environnement virtuel**
```bash
source venv/bin/activate
```

3. **Installer les dépendances**
```bash
pip install -r requirements.txt
```

4. **Créer les migrations et appliquer**

La base de données SQLite sera créée automatiquement lors de la première migration.
```bash
python manage.py makemigrations
python manage.py migrate
```

5. **Charger les magasins depuis magasin.json**
```bash
python manage.py load_magasins
```

6. **Créer un superutilisateur (optionnel)**
```bash
python manage.py createsuperuser
```

7. **Lancer le serveur de développement**
```bash
python manage.py runserver
```

Accédez à l'application : http://127.0.0.1:8000/

## 📁 Structure des dossiers

```
/media/
 ├── commande_asten/
 │     ├── asten_2024_01.csv
 │     ├── asten_2024_02.csv
 ├── commande_cyrus/
 │     ├── cyrus_2024_01.csv
```

## 📊 Format des fichiers CSV

### Format Asten (commande_asten/*.csv)
```csv
date_commande,numero_commande,code_magasin,montant,statut
2024-01-15,CMD001,030,1500.00,valide
2024-01-16,CMD002,050,2000.00,valide
```

### Format Cyrus (commande_cyrus/*.csv)
```csv
date_commande,numero_commande,code_magasin,montant,statut
2024-01-15,CMD001,030,1500.00,valide
2024-01-16,CMD002,050,2000.00,valide
```

## 🔑 Clé de rapprochement

Une commande est considérée identique entre Asten et Cyrus si :
- `date_commande`
- `numero_commande`
- `code_magasin`

**Règle importante** : Un magasin ne peut pas avoir deux fois le même numéro de commande. Plusieurs magasins peuvent avoir le même numéro de commande.

## 🎯 Utilisation

### 1. Déposer les fichiers

Placez vos fichiers CSV dans :
- `/media/commande_asten/` pour les fichiers Asten
- `/media/commande_cyrus/` pour les fichiers Cyrus

### 2. Actualiser les données

Depuis le dashboard, cliquez sur **"Actualiser / Recalculer"**. Le système :
- Lit les nouveaux fichiers
- Insère les commandes dans la base
- Recalcule automatiquement les écarts

### 3. Consulter les résultats

Le dashboard affiche :
- Nombre total de commandes Asten
- Nombre total de commandes Cyrus
- Nombre de commandes intégrées
- Nombre de commandes non intégrées (écarts)
- Tableau comparatif avec filtres

## 📋 Fonctionnalités

### Dashboard
- Vue d'ensemble des statistiques
- Tableau comparatif Asten vs Cyrus
- Filtres par période et magasin
- Bouton d'actualisation/recalcul

### Liste des écarts
- Affichage de tous les écarts détectés
- Filtres par date, magasin, statut
- Détail de chaque écart

### Sidebar modulaire
- Dashboard
- Écarts
- Commandes (à venir)
- Factures (à venir)
- BR (à venir)
- Paramètres (à venir)
- Rapports (à venir)

## 🏗️ Architecture

### Apps Django
- `core/` : Modèles de base (Magasin)
- `imports/` : Logique d'import des fichiers
- `asten/` : Modèles et gestion des commandes Asten
- `cyrus/` : Modèles et gestion des commandes Cyrus
- `ecarts/` : Détection et gestion des écarts
- `dashboard/` : Interface utilisateur

### Modèles principaux
- `Magasin` : Liste des magasins
- `CommandeAsten` : Commandes du système Asten
- `CommandeCyrus` : Commandes du système Cyrus
- `EcartCommande` : Écarts détectés
- `ImportFichier` : Historique des imports

**Note** : La base de données utilise SQLite par défaut. Voir la section ci-dessous pour migrer vers PostgreSQL avec Docker.

## 🔧 Commandes de gestion

```bash
# Charger les magasins
python manage.py load_magasins

# Accéder à l'admin Django
python manage.py createsuperuser
# Puis http://127.0.0.1:8000/admin/
```

## 📝 Notes importantes

- Les fichiers déjà importés ne seront pas réimportés (vérification par nom de fichier)
- Les doublons sont automatiquement évités grâce à la clé unique composite
- Le recalcul des écarts supprime et recrée tous les écarts à chaque fois
- Les magasins doivent exister dans la base avant l'import des commandes

## 🚧 Évolutivité

L'architecture est conçue pour être extensible :
- Ajout futur de modules Factures, BR, etc.
- Même logique : Import → table → comparaison → écart
- Sidebar modulaire prête pour de nouveaux modules

## 🐘 Migration SQLite → PostgreSQL (Docker)

### Prérequis
- Docker et Docker Compose installés

### Étape 1 — Sauvegarder les données existantes

```bash
source env/bin/activate
python manage.py dumpdata --natural-foreign --natural-primary -e contenttypes -e auth.Permission > backup.json
```

### Étape 2 — Créer le fichier `docker-compose.yml` à la racine du projet

```yaml
services:
  db:
    image: postgres:16
    restart: always
    environment:
      POSTGRES_DB: verification_db
      POSTGRES_USER: django_user
      POSTGRES_PASSWORD: motdepasse
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

volumes:
  postgres_data:
```

### Étape 3 — Lancer le conteneur PostgreSQL

```bash
docker-compose up -d
```

Vérifier que le conteneur tourne :
```bash
docker ps
```

### Étape 4 — Installer le driver Python

```bash
pip install psycopg2-binary
```

### Étape 5 — Modifier `verification_commande/settings.py`

Remplacer le bloc `DATABASES` :

```python
DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'verification_db',
        'USER': 'django_user',
        'PASSWORD': 'motdepasse',
        'HOST': 'localhost',
        'PORT': '5432',
    }
}
```

### Étape 6 — Appliquer les migrations

```bash
python manage.py migrate
```

### Étape 7 — Recharger les données

```bash
python manage.py loaddata backup.json
```

### Étape 8 — Recharger les magasins (si nécessaire)

```bash
python manage.py load_magasins
```

### Étape 9 — Lancer le serveur et vérifier

```bash
python manage.py runserver
```

Accédez à http://127.0.0.1:8000/ et vérifiez que tout fonctionne.

---

> **Tip** : Pour arrêter/relancer PostgreSQL Docker :
> ```bash
> docker-compose stop    # arrêter
> docker-compose start   # relancer
> docker-compose down    # supprimer le conteneur (les données sont conservées dans le volume)
> ```

## 📞 Support

Pour toute question ou problème, consultez la documentation Django ou les logs de l'application.




## Pour démarrer après avoir allumé le PC
1. PostgreSQL démarre automatiquement
PostgreSQL se lance tout seul au démarrage du PC — tu n'as rien à faire.

## que c'est bien configuré :


sudo systemctl is-enabled postgresql
Doit afficher enabled. Si ce n'est pas le cas :

sudo systemctl enable postgresql

## 2. Pour lancer le site

cd ~/Documents/traitement_n8n
source env/bin/activate
python3 manage.py runserver
Pour la production (serveur dédié)

## Si tu mets ça sur un vrai serveur, tu n'utilises plus runserver — tu utilises Gunicorn + Nginx qui démarrent automatiquement comme services. Mais pour l'instant en local, les 3 commandes ci-dessus suffisent.

