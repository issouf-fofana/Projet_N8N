# 📁 Où déposer les fichiers CSV

## 📍 Emplacements des dossiers

### En développement (local)
Les fichiers doivent être déposés dans les dossiers suivants :

```
/home/youssef/Documents/traitement_n8n/
├── traitement/
│   └── export1/                ← DÉPOSER ICI les fichiers AUTO
│       └── *.csv               (fichiers d'extraction automatique)
│
└── extractions/
    ├── commande_manual/        ← DÉPOSER ICI les fichiers MANUAL
    │   └── *.csv               (fichiers d'extraction manuelle - contient TOUTES les données)
    │
    └── archive/                (fichiers traités - créé automatiquement)
```

### En production (serveur)
Les fichiers doivent être déposés dans :

```
/mnt/windows/extractions/
├── commande_auto/              ← DÉPOSER ICI les fichiers AUTO
│   └── *.csv
│
└── commande_manual/            ← DÉPOSER ICI les fichiers MANUAL
    └── *.csv
```

## 🔄 Logique de traitement

### Principe
- **AUTO** : Fichiers d'extraction automatique (peu de données)
- **MANUAL** : Fichiers d'extraction manuelle (contient TOUTES les données)

### Processus de comparaison
1. Le système charge **TOUS** les fichiers MANUAL et les combine
2. Pour chaque fichier AUTO :
   - Le système cherche chaque ligne dans le fichier MANUAL consolidé
   - Si une ligne de AUTO n'est **pas trouvée** dans MANUAL → **ÉCART détecté**
   - Si une ligne est trouvée mais avec des valeurs différentes → **ÉCART détecté**

### Résultat
- ✅ **Conforme** : Toutes les lignes de AUTO sont présentes dans MANUAL avec les mêmes valeurs
- ❌ **Écart** : Une ligne de AUTO est absente ou différente dans MANUAL

## 📝 Exemple d'utilisation

### 1. Déposer les fichiers

**Fichiers AUTO** → `traitement/export1/` (ou `commande_auto/` en production)
```
traitement/export1/
├── export_commande_reassort_110_20260109_134120.csv
└── export_commande_reassort_230_20260109_134142.csv
```

**Fichiers MANUAL** → `extractions/commande_manual/` (ou `commande_manual/` en production)
```
extractions/commande_manual/
├── RUN51257.csv
└── RUN53516.csv
```

### 2. Lancer le traitement

```bash
source venv/bin/activate
python manage.py process_commande
```

### 3. Résultat

Le système va :
- ✅ Charger tous les fichiers MANUAL
- ✅ Comparer chaque fichier AUTO avec MANUAL
- ✅ Détecter les écarts (lignes absentes ou différentes)
- ✅ Archiver les fichiers traités dans `extractions/archive/YYYYMMDD/`

## ⚠️ Important

1. **Format CSV** : Les fichiers doivent être en CSV avec séparateur `;` (point-virgule) ou `,` (virgule)
2. **Colonne de référence** : Les fichiers doivent avoir une colonne commune pour la comparaison :
   - `Référence commande` (priorité)
   - `Référence`
   - `id`
   - Ou toute autre colonne commune
3. **Archivage** : Les fichiers sont automatiquement déplacés dans `archive/` après traitement
4. **Retraitement** : Utilisez `--force` pour retraiter des fichiers déjà traités

## 🔍 Vérifier les chemins configurés

Pour voir les chemins actuellement configurés :

```bash
source venv/bin/activate
python manage.py shell
```

Puis dans le shell Python :
```python
from django.conf import settings
print("AUTO:", settings.EXTRACTIONS_PATHS['commande_auto'])
print("MANUAL:", settings.EXTRACTIONS_PATHS['commande_manual'])
print("ARCHIVE:", settings.EXTRACTIONS_PATHS['archive'])
```

