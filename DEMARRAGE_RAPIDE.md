# 🚀 Démarrage Rapide

## ✅ Configuration terminée

Le projet est maintenant configuré et fonctionnel !

### Base de données
- **SQLite** est utilisé par défaut (fichier `db.sqlite3`)
- Les migrations ont été appliquées
- Un superutilisateur a été créé :
  - Username: `admin`
  - Password: `admin123`

### Test réussi
La commande `process_commande` a été testée avec succès !

## 📝 Commandes utiles

### Lancer le serveur Django
```bash
source venv/bin/activate
python manage.py runserver
```
Accéder à l'admin : http://127.0.0.1:8000/admin/

### Traiter les commandes
```bash
source venv/bin/activate
python manage.py process_commande
python manage.py process_commande --periode 2026-01
python manage.py process_commande --force
```

### Accéder à la base de données
```bash
source venv/bin/activate
python manage.py shell
```

## 🔄 Passer à PostgreSQL (optionnel)

Si vous voulez utiliser PostgreSQL au lieu de SQLite :

1. Créer un fichier `.env` à la racine :
```bash
USE_POSTGRES=True
DB_NAME=controle_platform
DB_USER=votre_utilisateur
DB_PASSWORD=votre_mot_de_passe
DB_HOST=localhost
DB_PORT=5432
```

2. Créer la base de données PostgreSQL :
```sql
CREATE DATABASE controle_platform;
```

3. Appliquer les migrations :
```bash
python manage.py migrate
```

## 📁 Structure des fichiers

```
extractions/
├── commande_auto/      # Fichiers CSV automatiques (Source A)
│   └── (fichiers depuis export1/)
├── commande_manual/    # Fichiers CSV manuels (Source B)
├── archive/            # Fichiers traités archivés
└── ...
```

## 🎯 Prochaines étapes

1. ✅ **Phase 1 - Setup** : TERMINÉE
2. ✅ **Phase 2 - Commande** : TERMINÉE
3. 🚧 **Phase 3 - Dashboard** : À venir
4. 🚧 **Phase 4 - Automatisation** : À venir
5. 🚧 **Phase 5 - Extension** : À venir

