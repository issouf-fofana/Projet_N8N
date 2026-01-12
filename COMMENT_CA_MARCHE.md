# 🎯 Comment ça marche ? Guide simple

## 📍 ÉTAPE 1 : Où mettre les fichiers ?

### ✅ Fichiers AUTO (extraction automatique)
**Dossier :** `/home/youssef/Documents/traitement_n8n/traitement/export1/`

👉 **Déposez ici vos fichiers CSV d'extraction automatique**

```bash
# Exemple : copier un fichier AUTO
cp mon_fichier_auto.csv /home/youssef/Documents/traitement_n8n/traitement/export1/
```

### ✅ Fichiers MANUAL (extraction manuelle - contient TOUTES les données)
**Dossier :** `/home/youssef/Documents/traitement_n8n/extractions/commande_manual/`

👉 **Déposez ici vos fichiers CSV d'extraction manuelle**

```bash
# Exemple : copier un fichier MANUAL
cp mon_fichier_manual.csv /home/youssef/Documents/traitement_n8n/extractions/commande_manual/
```

**Note :** Vous avez déjà 3 fichiers MANUAL dans ce dossier ! ✅

## 🚀 ÉTAPE 2 : Lancer le traitement

```bash
# 1. Aller dans le dossier du projet
cd /home/youssef/Documents/traitement_n8n

# 2. Activer l'environnement virtuel
source venv/bin/activate

# 3. Lancer le traitement
python manage.py process_commande
```

**Ce qui se passe :**
- ✅ Le système charge tous les fichiers MANUAL
- ✅ Pour chaque fichier AUTO, il cherche chaque ligne dans MANUAL
- ✅ Il détecte les écarts et les sauvegarde
- ✅ Il archive les fichiers traités

## 📊 ÉTAPE 3 : Où voir les écarts ?

### 🎯 Méthode 1 : Interface Web (RECOMMANDÉ)

**1. Lancer le serveur :**
```bash
cd /home/youssef/Documents/traitement_n8n
source venv/bin/activate
python manage.py runserver
```

**2. Ouvrir dans votre navigateur :**
```
http://127.0.0.1:8000/admin/
```

**3. Se connecter :**
- **Username :** `admin`
- **Password :** `admin123`

**4. Dans l'interface, vous verrez :**

#### 📋 Section "Contrôles"
- Liste de tous les contrôles effectués
- Pour chaque contrôle :
  - Date d'exécution
  - Nombre total de lignes
  - **Nombre d'écarts** ← ICI vous voyez s'il y a des écarts !
  - **Taux de conformité** (ex: 95.83%)
  - Statut

#### ⚠️ Section "Écarts"
- **Liste complète de tous les écarts détectés**
- Pour chaque écart :
  - Référence (Référence commande + Date)
  - Type d'écart :
    - `absent_b` : Ligne dans AUTO mais absente dans MANUAL
    - `valeur_differente` : Ligne présente mais valeurs différentes
  - Valeur Source A (données AUTO)
  - Valeur Source B (données MANUAL)
  - Date de création

**👉 C'est ici que vous voyez TOUS les écarts en détail !**

### 🎯 Méthode 2 : Ligne de commande

```bash
# Ouvrir le shell Django
python manage.py shell
```

Puis taper :
```python
from traitement.models import Controle, Ecart

# Voir le dernier contrôle
controle = Controle.objects.last()
print(f"Écarts détectés: {controle.total_ecarts}")
print(f"Taux de conformité: {controle.taux_conformite}%")

# Voir tous les écarts
ecarts = Ecart.objects.filter(controle=controle)
for e in ecarts:
    print(f"- {e.reference}: {e.get_type_ecart_display()}")
```

## 📈 Comment interpréter les résultats ?

### ✅ Si `total_ecarts = 0`
**Parfait !** Toutes les lignes de AUTO sont présentes dans MANUAL avec les mêmes valeurs.

### ⚠️ Si `total_ecarts > 0`
**Des écarts ont été détectés :**

1. **Type `absent_b`** :
   - Une ligne existe dans AUTO mais **pas dans MANUAL**
   - 👉 Action : Vérifier pourquoi cette ligne n'est pas dans MANUAL

2. **Type `valeur_differente`** :
   - La ligne existe dans les deux fichiers mais **certaines valeurs diffèrent**
   - 👉 Action : Vérifier les colonnes différentes dans les détails

### Exemple de résultat

```
✓ Contrôle terminé: 5 écarts sur 120 lignes (Taux de conformité: 95.83%)
```

**Signification :**
- 120 lignes au total dans AUTO
- 5 lignes ont des écarts
- 115 lignes sont conformes
- Taux de conformité : 95.83%

## 🔍 Vérifier vos fichiers

```bash
# Voir les fichiers AUTO
ls -lh /home/youssef/Documents/traitement_n8n/traitement/export1/
```

# Voir les fichiers MANUAL
ls -lh /home/youssef/Documents/traitement_n8n/extractions/commande_manual/
```

## 📝 Exemple complet pas à pas

```bash
# 1. Déposer un fichier AUTO
cp mon_fichier_auto.csv /home/youssef/Documents/traitement_n8n/traitement/export1/

# 2. Vérifier qu'il est bien là
ls -lh /home/youssef/Documents/traitement_n8n/traitement/export1/
```

# 3. Lancer le traitement
cd /home/youssef/Documents/traitement_n8n
source venv/bin/activate
python manage.py process_commande

# 4. Voir les résultats dans l'admin
python manage.py runserver
# Puis ouvrir http://127.0.0.1:8000/admin/
```

## ❓ Questions fréquentes

**Q : Les fichiers sont-ils supprimés après traitement ?**  
R : Non, ils sont **archivés** dans `extractions/archive/YYYYMMDD/`

**Q : Puis-je retraiter les mêmes fichiers ?**  
R : Oui, utilisez `python manage.py process_commande --force`

**Q : Comment exporter les écarts ?**  
R : Via l'admin Django ou en utilisant le shell Python avec pandas

