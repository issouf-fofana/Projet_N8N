# 📖 Guide d'utilisation - Comment ça marche ?

## 📁 1. Où déposer les fichiers ?

### Emplacements exacts

**Fichiers AUTO** (extraction automatique) :
```
/home/youssef/Documents/traitement_n8n/traitement/export1/
```
👉 **Déposez ici** tous vos fichiers CSV d'extraction automatique

**Fichiers MANUAL** (extraction manuelle - contient toutes les données) :
```
/home/youssef/Documents/traitement_n8n/extractions/commande_manual/
```
👉 **Déposez ici** tous vos fichiers CSV d'extraction manuelle

### Exemple concret

```bash
# Créer les dossiers si nécessaire
mkdir -p /home/youssef/Documents/traitement_n8n/export1
mkdir -p /home/youssef/Documents/traitement_n8n/extractions/commande_manual

# Copier vos fichiers
cp mon_fichier_auto.csv /home/youssef/Documents/traitement_n8n/export1/
cp mon_fichier_manual.csv /home/youssef/Documents/traitement_n8n/extractions/commande_manual/
```

## 🚀 2. Lancer le traitement

```bash
# Activer l'environnement virtuel
cd /home/youssef/Documents/traitement_n8n
source venv/bin/activate

# Lancer le traitement
python manage.py process_commande
```

### Ce qui se passe :
1. ✅ Le système charge tous les fichiers MANUAL
2. ✅ Pour chaque fichier AUTO, il cherche chaque ligne dans MANUAL
3. ✅ Il détecte les écarts (lignes absentes ou différentes)
4. ✅ Il sauvegarde les résultats dans la base de données
5. ✅ Il archive les fichiers traités dans `extractions/archive/`

## 📊 3. Où voir les écarts ?

### Option 1 : Interface d'administration Django (Recommandé)

```bash
# Lancer le serveur
python manage.py runserver
```

Puis ouvrir dans votre navigateur :
```
http://127.0.0.1:8000/admin/
```

**Identifiants :**
- Username : `admin`
- Password : `admin123`

### Dans l'admin, vous verrez :

#### 📋 Section "Contrôles"
- Liste de tous les contrôles effectués
- Pour chaque contrôle :
  - Type (Commande)
  - Période
  - Date d'exécution
  - Nombre total de lignes
  - Nombre d'écarts
  - Taux de conformité
  - Statut (Terminé, En cours, Erreur)

#### ⚠️ Section "Écarts"
- Liste de tous les écarts détectés
- Pour chaque écart :
  - Référence (Référence commande + Date)
  - Type d'écart :
    - `absent_b` : Ligne dans AUTO mais absente dans MANUAL
    - `valeur_differente` : Ligne présente mais valeurs différentes
  - Valeur Source A (données AUTO)
  - Valeur Source B (données MANUAL)
  - Date de création

#### 📄 Section "Fichiers sources"
- Liste de tous les fichiers traités
- Statut (traité ou non)

### Option 2 : Via la ligne de commande

```bash
# Voir les contrôles récents
python manage.py shell
```

Puis dans le shell Python :
```python
from traitement.models import Controle, Ecart

# Voir le dernier contrôle
dernier_controle = Controle.objects.last()
print(f"Contrôle: {dernier_controle}")
print(f"Écarts: {dernier_controle.total_ecarts}")
print(f"Taux de conformité: {dernier_controle.taux_conformite}%")

# Voir tous les écarts du dernier contrôle
ecarts = Ecart.objects.filter(controle=dernier_controle)
for ecart in ecarts:
    print(f"- {ecart.reference}: {ecart.get_type_ecart_display()}")
```

## 📈 4. Interpréter les résultats

### Si `total_ecarts = 0`
✅ **Parfait !** Toutes les lignes de AUTO sont présentes dans MANUAL avec les mêmes valeurs.

### Si `total_ecarts > 0`
⚠️ **Des écarts ont été détectés :**

1. **Type `absent_b`** :
   - Une ligne existe dans AUTO mais pas dans MANUAL
   - Action : Vérifier pourquoi cette ligne n'est pas dans MANUAL

2. **Type `valeur_differente`** :
   - La ligne existe dans les deux fichiers mais certaines valeurs diffèrent
   - Action : Vérifier les colonnes différentes dans les détails de l'écart

### Exemple de résultat

```
✓ Contrôle terminé: 5 écarts sur 120 lignes (Taux de conformité: 95.83%)
```

Cela signifie :
- 120 lignes au total dans AUTO
- 5 lignes ont des écarts
- 115 lignes sont conformes
- Taux de conformité : 95.83%

## 🔍 5. Vérifier les fichiers déposés

```bash
# Voir les fichiers AUTO
ls -lh /home/youssef/Documents/traitement_n8n/traitement/export1/
```

# Voir les fichiers MANUAL
ls -lh /home/youssef/Documents/traitement_n8n/extractions/commande_manual/
```

## 📝 6. Exemple complet

```bash
# 1. Déposer les fichiers
cp fichier_auto.csv /home/youssef/Documents/traitement_n8n/traitement/export1/
```
cp fichier_manual.csv /home/youssef/Documents/traitement_n8n/extractions/commande_manual/

# 2. Lancer le traitement
cd /home/youssef/Documents/traitement_n8n
source venv/bin/activate
python manage.py process_commande

# 3. Voir les résultats
python manage.py runserver
# Ouvrir http://127.0.0.1:8000/admin/
```

## ❓ Questions fréquentes

### Les fichiers sont-ils supprimés après traitement ?
Non, ils sont **archivés** (déplacés) dans `extractions/archive/YYYYMMDD/`

### Puis-je retraiter les mêmes fichiers ?
Oui, utilisez `--force` :
```bash
python manage.py process_commande --force
```

### Comment exporter les écarts ?
Via l'admin Django, vous pouvez exporter les données ou utiliser :
```bash
python manage.py shell
```
Puis exporter en CSV/Excel avec pandas.

