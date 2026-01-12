# ✅ Modification : Comparaison avec clé composite

## 🔄 Changements apportés

### Avant
- Comparaison sur une seule colonne (Référence commande)
- Recherche de colonne commune entre AUTO et MANUAL

### Maintenant
- **Comparaison sur clé composite** : Référence + Date
- **Mapping explicite** des colonnes :
  - AUTO : `Référence commande` + `Date commande`
  - MANUAL : `NCDE` + `DCDE`

## 📊 Logique de comparaison

### Clé composite
Chaque ligne est identifiée par une clé composite :
```
Clé = "Référence|Date"
```

### Exemple
- **AUTO** : `"29103385|09/01/2026 12:08:03"`
- **MANUAL** : `"29103385|09/01/2026 12:08:03"` (via NCDE + DCDE)

Si les clés correspondent, on compare les autres colonnes.

## 🎯 Types d'écarts détectés

1. **absent_b** : La clé composite (Référence + Date) existe dans AUTO mais pas dans MANUAL
2. **valeur_differente** : La clé existe dans les deux fichiers mais d'autres valeurs diffèrent

## 📝 Format des fichiers attendus

### Fichier AUTO
Doit contenir :
- `Référence commande` : Numéro de référence
- `Date commande` : Date de la commande

### Fichier MANUAL
Doit contenir :
- `NCDE` : Numéro de commande (correspond à Référence commande)
- `DCDE` : Date de commande (correspond à Date commande)

## ⚠️ Messages d'erreur

Si une colonne est introuvable, le système affichera :
- Les colonnes disponibles dans AUTO
- Les colonnes disponibles dans MANUAL
- Un message d'erreur explicite

## 🚀 Utilisation

```bash
# 1. Déposer les fichiers dans les bons dossiers
# AUTO → export1/
# MANUAL → extractions/commande_manual/

# 2. Lancer le traitement
python manage.py process_commande

# 3. Voir les résultats
# Les écarts seront enregistrés dans la base de données
# Accessible via l'admin Django
```

