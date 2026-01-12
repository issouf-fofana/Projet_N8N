# 🎨 Nouveau Dashboard - Guide d'utilisation

## ✨ Nouvelles fonctionnalités

### 🔄 Bouton "Actualiser & Traiter"
- **Fonction** : Traite automatiquement les fichiers déposés dans les dossiers
- **Utilisation** :
  1. Déposez vos fichiers dans les dossiers
  2. Sélectionnez la période (optionnel)
  3. Cliquez sur "🔄 Actualiser & Traiter"
  4. Le système traite les fichiers et actualise automatiquement la page

### 📊 Statistiques améliorées
- **Total Commandes Asten** : Nombre de lignes uniques dans Asten
- **Total Commandes Cyrus** : Nombre de lignes uniques dans Cyrus
- **Écarts Asten → Cyrus** : Éléments dans Asten absents dans Cyrus
- **Valeurs Différentes** : Lignes présentes mais avec valeurs différentes

## 🎨 Design amélioré

### Caractéristiques
- ✅ Design moderne et professionnel
- ✅ Cartes statistiques avec dégradés colorés
- ✅ Animations au survol
- ✅ Interface responsive
- ✅ Messages d'alerte pour le feedback
- ✅ Bouton d'actualisation avec indicateur de chargement

## 📍 Où déposer les fichiers

### Fichiers Asten
```
/home/youssef/Documents/traitement_n8n/traitement/export1/
```

### Fichiers Cyrus
```
/home/youssef/Documents/traitement_n8n/extractions/commande_manual/
```

## 🚀 Utilisation

### 1. Déposer les fichiers
```bash
# Fichiers Asten
cp vos_fichiers.csv /home/youssef/Documents/traitement_n8n/traitement/export1/

# Fichiers Cyrus
cp vos_fichiers.csv /home/youssef/Documents/traitement_n8n/extractions/commande_manual/
```

### 2. Ouvrir le dashboard
```bash
python manage.py runserver
# Ouvrir http://127.0.0.1:8000/
```

### 3. Actualiser et traiter
- Cliquez sur "🔄 Actualiser & Traiter"
- Le système traite les fichiers automatiquement
- La page se recharge avec les nouveaux résultats

### 4. Filtrer les résultats
- Utilisez les filtres (magasin, période, type d'écart)
- Cliquez sur "🔍 Appliquer les filtres"

## 💡 Astuces

- **Période** : Sélectionnez une période avant de cliquer sur "Actualiser" pour traiter uniquement cette période
- **Force** : Pour retraiter des fichiers déjà traités, utilisez l'option force (à ajouter dans l'interface)
- **Auto-refresh** : La page peut se rafraîchir automatiquement après le traitement

## 🎯 Workflow recommandé

1. Déposer les fichiers dans les dossiers
2. Ouvrir le dashboard
3. Sélectionner la période (si nécessaire)
4. Cliquer sur "Actualiser & Traiter"
5. Attendre le message de succès
6. Voir les résultats dans le tableau
7. Filtrer par magasin pour analyser les écarts

