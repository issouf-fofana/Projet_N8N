# ⚡ Démarrage rapide - 3 étapes

## 📍 ÉTAPE 1 : Déposer vos fichiers

### Fichiers AUTO (extraction automatique)
```bash
# Copier vos fichiers CSV AUTO ici :
cp vos_fichiers_auto.csv /home/youssef/Documents/traitement_n8n/traitement/export1/
```

**Dossier :** `/home/youssef/Documents/traitement_n8n/traitement/export1/`

### Fichiers MANUAL (extraction manuelle)
```bash
# Copier vos fichiers CSV MANUAL ici :
cp vos_fichiers_manual.csv /home/youssef/Documents/traitement_n8n/extractions/commande_manual/
```

**Dossier :** `/home/youssef/Documents/traitement_n8n/extractions/commande_manual/`

✅ **Vous avez déjà 3 fichiers MANUAL dans ce dossier !**

## 🚀 ÉTAPE 2 : Lancer le traitement

```bash
cd /home/youssef/Documents/traitement_n8n
source venv/bin/activate
python manage.py process_commande
```

## 📊 ÉTAPE 3 : Voir les résultats

```bash
# Lancer le serveur
python manage.py runserver

# Ouvrir dans le navigateur
# http://127.0.0.1:8000/admin/
# Username: admin
# Password: admin123
```

## ⚠️ Important

- Les fichiers AUTO doivent avoir les colonnes : `Référence commande` et `Date commande`
- Les fichiers MANUAL doivent avoir les colonnes : `NCDE` et `DCDE`
- Les fichiers sont automatiquement archivés après traitement

