# 🔗 Mapping des colonnes pour la comparaison

## 📊 Colonnes utilisées pour la comparaison

La comparaison se fait sur une **clé composite** (Référence + Date) :

### Fichier AUTO
- **Référence commande** : Colonne contenant la référence de la commande
- **Date commande** : Colonne contenant la date de la commande

### Fichier MANUAL
- **NCDE** : Correspond à "Référence commande" (Numéro de Commande)
- **DCDE** : Correspond à "Date commande" (Date de Commande)

## 🔄 Logique de comparaison

1. **Création de la clé composite** :
   - AUTO : `Référence commande` + `Date commande` → Clé = `"REF|DATE"`
   - MANUAL : `NCDE` + `DCDE` → Clé = `"REF|DATE"`

2. **Recherche** :
   - Pour chaque ligne de AUTO, on cherche la clé composite dans MANUAL
   - Si la clé n'existe pas dans MANUAL → **Écart détecté** (absent_b)
   - Si la clé existe mais les autres valeurs diffèrent → **Écart détecté** (valeur_differente)

## 📝 Exemple

### Fichier AUTO
```csv
Référence commande;Date commande;Statut;...
29103385;09/01/2026 12:08:03;en attente;...
20809975;09/01/2026 10:47:44;en attente;...
```

### Fichier MANUAL
```csv
NCDE;DCDE;Statut;...
29103385;09/01/2026 12:08:03;en attente;...
20809975;09/01/2026 10:47:44;livré;...
```

### Comparaison
- Ligne 1 : Clé `"29103385|09/01/2026 12:08:03"` → Trouvée dans MANUAL → Comparer les autres colonnes
- Ligne 2 : Clé `"20809975|09/01/2026 10:47:44"` → Trouvée dans MANUAL mais Statut différent → **Écart détecté**

## ⚠️ Important

1. **Format des dates** : Les dates doivent être au même format dans les deux fichiers pour que la comparaison fonctionne
2. **Valeurs vides** : Les lignes avec référence ou date vide sont ignorées
3. **Sensibilité à la casse** : Les comparaisons sont sensibles à la casse et aux espaces

## 🔍 Vérification

Si vous obtenez une erreur indiquant qu'une colonne est introuvable, vérifiez :
- Que le fichier AUTO contient bien les colonnes `Référence commande` et `Date commande`
- Que le fichier MANUAL contient bien les colonnes `NCDE` et `DCDE`
- Que les noms de colonnes sont exactement identiques (espaces, majuscules/minuscules)

