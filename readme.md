 CAHIER DES CHARGES COMPLET
Projet : Plateforme de centralisation, comparaison et dashboard des contrôles
🏢 1. Contexte
Aujourd’hui :

Les équipes réalisent des contrôles sur plusieurs plateformes.

Les données sont extraites manuellement ou par API.

Les comparaisons se font dans Excel / CSV.

Les écarts sont envoyés par mail.

Il n’existe pas de vision globale, ni d’historique centralisé.

Problèmes :

⏱️ Perte de temps

❌ Risque d’erreurs humaines

📂 Données dispersées

📉 Pas de reporting consolidé

🔍 Difficulté de suivi dans le temps

🎯 2. Objectifs du projet
Mettre en place une application web interne permettant de :

✅ Centraliser toutes les vérifications
✅ Automatiser les comparaisons entre sources
✅ Identifier automatiquement les écarts
✅ Stocker les résultats dans une base de données
✅ Visualiser les résultats dans un dashboard
✅ Filtrer par période, type, statut
✅ Supprimer la dépendance aux mails et fichiers

🧩 3. Périmètre fonctionnel
📌 Types de contrôles à gérer
Type	Source A	Source B
Commande	API (automatique)	Fichier manuel
Facture	Manuel	Manuel
BR	Manuel	Manuel
Legend	Manuel	Manuel
📌 Formats
Tous les fichiers sont en CSV.

📌 Localisation des fichiers
Les fichiers sont déposés sur un serveur Windows.

Le dossier est monté sur le serveur Linux via SMB :

/mnt/windows/extractions/
Organisation des dossiers :

extractions/
   ├── commande_auto/
   ├── commande_manual/
   ├── facture/
   ├── br/
   ├── legend/
   └── archive/
👥 4. Utilisateurs et rôles
👤 Rôles
Admin
Gestion des utilisateurs

Paramétrage

Accès complet

Utilisateur
Consultation du dashboard

Export des données

⚙️ 5. Fonctionnalités attendues
✅ 5.1 Import des données
Lecture automatique des CSV depuis les dossiers

Détection des nouveaux fichiers

Validation du format

Vérification des colonnes obligatoires

Historisation des fichiers traités

✅ 5.2 Traitement & Comparaison
Pour chaque type :

Charger Source A

Charger Source B

Comparer selon une clé commune (ex : reference)

Identifier :

❌ Présent dans A mais absent dans B

❌ Présent dans B mais absent dans A

⚠️ Valeurs différentes

✅ Lignes conformes

Sauvegarder les écarts en base

Marquer le contrôle comme traité

Archiver les fichiers

✅ 5.3 Automatisation
Les traitements sont déclenchés par :

Cron

ou n8n

ou commandes Django

Exemples :

python manage.py process_commande
python manage.py process_facture
python manage.py process_br
python manage.py process_legend
✅ 5.4 Dashboard Web
🎛️ Filtres
Période

Type de contrôle

Statut

Date

📊 Indicateurs (KPI)
Nombre total de contrôles

Nombre total d’écarts

Taux de conformité

Répartition par type

Évolution dans le temps

📋 Table des écarts
Type de contrôle

Référence

Valeur Source A

Valeur Source B

Type d’écart

Date

📤 Export
Export CSV / Excel

✅ 5.5 Sécurité
Authentification

Gestion des rôles

Accès sécurisé

Logs d’exécution

Traçabilité

🗄️ 6. Modèle de données (proposition)
📁 Table : Controle
Champ	Type
id	UUID
type_controle	string
periode	string
date_execution	datetime
total_lignes	int
total_ecarts	int
statut	string
⚠️ Table : Ecart
Champ	Type
id	UUID
controle_id	FK
reference	string
valeur_source_a	string
valeur_source_b	string
type_ecart	string
date_creation	datetime
📄 Table : FichierSource
Champ	Type
id	UUID
type_controle	string
origine	string
nom_fichier	string
chemin	string
date_import	datetime
traite	bool
🏗️ 7. Architecture technique
Serveur Windows (CSV)
        ↓ (SMB)
Serveur Linux
        ↓
Application Django
   - Traitement
   - API interne
   - Dashboard
        ↓
PostgreSQL
        ↓
Navigateur
Orchestration :

n8n / Cron → Django commands
🧪 8. Contraintes techniques
Python 3.x

Django

PostgreSQL

Pandas

Linux

SMB

Docker (optionnel)

🚀 9. Roadmap
Phase 1 — Setup
Création projet Django

Connexion PostgreSQL

Modèles

Phase 2 — Commande (pilote)
Lecture CSV

Comparaison

Sauvegarde DB

Phase 3 — Dashboard
Liste contrôles

Table écarts

Filtres

Phase 4 — Automatisation
n8n / cron

Logs

Phase 5 — Extension
Facture

BR

Legend

📦 10. Livrables
Application Django

Base de données

Dashboard

Documentation

Scripts d’automatisation

✅ Prochaine étape pour toi
👉 1. Créer le projet Django
👉 2. Créer les modèles
👉 3. Implémenter la comparaison "Commande"

Si tu veux, je peux maintenant te donner :

✅ La structure exacte du projet Django
✅ Les modèles Django prêts à copier
✅ Une première commande de traitement
✅ Un premier écran de dashboard