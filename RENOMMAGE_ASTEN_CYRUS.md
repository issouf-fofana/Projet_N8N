# ✅ Renommage : Asten et Cyrus

## 🔄 Changements effectués

### Terminologie
- **AUTO / Automatique** → **Asten** (Source A)
- **MANUAL / Manuel** → **Cyrus** (Source B)

## 📝 Fichiers modifiés

### 1. Modèles (`traitement/models.py`)
- `ORIGINE_CHOICES` mis à jour :
  - `('auto', 'Automatique (Source A)')` → `('asten', 'Asten (Source A)')`
  - `('manual', 'Manuel (Source B)')` → `('cyrus', 'Cyrus (Source B)')`

### 2. Commande de traitement (`traitement/management/commands/process_commande.py`)
- Toutes les variables renommées :
  - `fichiers_auto` → `fichiers_asten`
  - `fichiers_manual` → `fichiers_cyrus`
  - `df_auto` → `df_asten`
  - `df_manual` → `df_cyrus`
  - `path_auto` → `path_asten`
  - `path_manual` → `path_cyrus`
- Messages mis à jour :
  - "Chargement de fichiers MANUAL" → "Chargement de fichiers Cyrus"
  - "lignes dans AUTO" → "lignes dans Asten"
  - "Total MANUAL" → "Total Cyrus"
- Logique de comparaison :
  - Commentaires mis à jour pour refléter Asten/Cyrus
  - Messages d'erreur mis à jour

### 3. Dashboard (`dashboard/views.py`)
- Variables renommées :
  - `data_auto` → `data_asten`
  - `data_manual` → `data_cyrus`
- Extraction des colonnes depuis `data_asten`

### 4. Template Dashboard (`dashboard/templates/dashboard/dashboard.html`)
- Labels mis à jour :
  - "Absents dans MANUAL" → "Absents dans Cyrus"
  - "Absents dans AUTO" → "Absents dans Asten"
  - "Absent MANUAL" → "Absent Cyrus"
  - "Absent AUTO" → "Absent Asten"

### 5. Migrations
- Migration créée : `0002_alter_fichiersource_origine.py`
- Migration appliquée avec succès

## 🎯 Logique de fonctionnement

### Principe
- **Asten** : Fichiers d'extraction automatique (Source A)
- **Cyrus** : Fichiers d'extraction manuelle (Source B - contient TOUTES les données)

### Processus
1. Charger tous les fichiers **Cyrus** et les combiner
2. Pour chaque fichier **Asten** :
   - Chercher chaque ligne dans le dataframe **Cyrus** consolidé
   - Si une ligne de **Asten** n'est pas trouvée dans **Cyrus** → Écart détecté
   - Si une ligne est trouvée mais avec des valeurs différentes → Écart détecté

## ✅ Vérification

Tous les fichiers ont été mis à jour et les migrations ont été appliquées avec succès.

## 🚀 Utilisation

Le système fonctionne exactement comme avant, mais avec la nouvelle terminologie :
- Les fichiers **Asten** vont dans `traitement/export1/`
- Les fichiers **Cyrus** vont dans `extractions/commande_manual/`
- La commande `python manage.py process_commande` fonctionne normalement
- Le dashboard affiche les bonnes terminologies

