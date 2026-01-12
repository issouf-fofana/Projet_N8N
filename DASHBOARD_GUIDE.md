# 📊 Guide du Dashboard

## 🚀 Accès au Dashboard

### Lancer le serveur
```bash
cd /home/youssef/Documents/traitement_n8n
source venv/bin/activate
python manage.py runserver
```

### Ouvrir dans le navigateur
```
http://127.0.0.1:8000/
```

## 🎯 Fonctionnalités

### 📈 Statistiques en temps réel
Le dashboard affiche en haut :
- **Total Écarts** : Nombre total d'écarts détectés
- **Absents dans MANUAL** : Lignes présentes dans AUTO mais absentes dans MANUAL
- **Valeurs Différentes** : Lignes présentes mais avec valeurs différentes
- **Absents dans AUTO** : Lignes présentes dans MANUAL mais absentes dans AUTO

### 🔍 Filtres disponibles

1. **Magasin** : Filtrer par numéro de magasin
   - Liste déroulante avec tous les magasins (numéro + nom)
   - Exemple : "110 - SUPER U VALLON"

2. **Période** : Filtrer par période de contrôle
   - Exemple : "2026-01"

3. **Type d'écart** :
   - Absent dans MANUAL
   - Valeur Différente
   - Absent dans AUTO

4. **Date début / Date fin** : Filtrer par plage de dates

5. **Contrôle** : Filtrer par contrôle spécifique

### 📋 Tableau des écarts

Le tableau affiche toutes les colonnes importantes :
- **Type Écart** : Badge coloré indiquant le type
- **Magasin** : Numéro et nom du magasin
- **Référence Commande** : Numéro de référence
- **Date Commande** : Date de la commande
- **Date Livraison** : Date de livraison prévue
- **Date Validation** : Date de validation
- **Statut** : Statut de la commande
- **Créée par** : Utilisateur ayant créé la commande
- **Validée par** : Utilisateur ayant validé la commande
- **Fournisseur** : Nom du fournisseur
- **Type Commande** : Type de commande
- **Date Détection** : Date à laquelle l'écart a été détecté
- **Détails** : Détails supplémentaires de l'écart

## 🎨 Design Professionnel

- **Interface moderne** avec dégradé violet/bleu
- **Cartes statistiques** avec ombres et animations
- **Tableau responsive** avec défilement horizontal
- **Badges colorés** pour identifier rapidement les types d'écarts
- **Filtres intuitifs** avec sélection multiple

## 📱 Utilisation

1. **Voir tous les écarts** : Laissez les filtres vides et cliquez sur "Appliquer les filtres"

2. **Filtrer par magasin** : 
   - Sélectionnez un magasin dans la liste déroulante
   - Cliquez sur "Appliquer les filtres"

3. **Combiner plusieurs filtres** :
   - Sélectionnez plusieurs critères
   - Cliquez sur "Appliquer les filtres"

4. **Réinitialiser** : Cliquez sur "Réinitialiser" pour effacer tous les filtres

## 🔄 Mise à jour en temps réel

Le dashboard se met à jour automatiquement :
- Après chaque traitement de commande
- Les nouveaux écarts apparaissent immédiatement
- Les statistiques sont recalculées en temps réel

## 💡 Conseils d'utilisation

- **Pour le directeur** : Utilisez les filtres par magasin pour suivre les écarts par point de vente
- **Pour l'analyse** : Utilisez les filtres par période pour voir l'évolution dans le temps
- **Pour le suivi** : Utilisez le filtre par type d'écart pour identifier les problèmes récurrents

## 🎯 Exemple d'utilisation

1. Ouvrir le dashboard : `http://127.0.0.1:8000/`
2. Filtrer par magasin "110" pour voir les écarts du SUPER U VALLON
3. Filtrer par période "2026-01" pour voir les écarts de janvier
4. Analyser les résultats dans le tableau
5. Exporter les données si nécessaire (via l'admin Django)

