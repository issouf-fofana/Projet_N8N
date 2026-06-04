# Plateforme de Vérification — Prosuma

Application Django + PostgreSQL pour le contrôle et le rapprochement des commandes, factures et bons de réception entre les systèmes Asten et Cyrus.

---

## Stack technique

- **Backend** : Django 4.x / Python 3.12
- **Base de données** : PostgreSQL 15+
- **IA** : Google Gemini (Text-to-SQL)
- **Serveur production** : Gunicorn + Nginx
- **Surveillance fichiers** : run_auto.py (daemon SMB)

---

## Installation (développement local)

### Prérequis

- Python 3.12+
- PostgreSQL 15+ démarré
- Accès au partage SMB (optionnel en local)

### 1. Cloner et configurer

```bash
cd /home/youssef/Documents/traitement_n8n
python3 -m venv env
source env/bin/activate
pip install -r requirements.txt
```

### 2. Configurer config.env

Copier et adapter :
```bash
cp config.env.example config.env  # ou éditer directement config.env
```

Variables importantes :
```env
POSTGRES_DB=traitement_n8n
POSTGRES_USER=postgres
POSTGRES_PASSWORD=xxx
POSTGRES_HOST=localhost

GEMINI_API_KEY=AIza...          # Clé API Google Gemini
GEMINI_MODEL_DEFAULT=gemini-2.5-flash-lite

DOSSIER_COMMANDES_ASTEN=...     # Chemin dossier SMB ou local
DOSSIER_FACTURES_ASTEN=...
# etc.
```

> ⚠️ `config.env` est dans `.gitignore` — ne jamais le commiter.

### 3. Migrations et démarrage

```bash
python manage.py migrate
python manage.py load_magasins   # charger les magasins depuis magasin.json
python manage.py createsuperuser
python manage.py runserver
```

Accès : http://127.0.0.1:8000/

---

## Fonctionnalités

### Commandes
- **Asten / GPV / Legend / Cyrus** : import CSV automatique, comparaison, détection d'écarts
- Écarts par source avec statut `ouvert / resolu / en_cours`
- Filtres par période, magasin, statut

### Factures (Cyrus ↔ Asten)
- Vue matérialisée `mv_factures_joined` : jointure Cyrus + Asten par clé facture + magasin
- Statuts : `integre` / `integre_vide` (qt=0) / `non_integre` / `ignore`
- **Priorité statut manuel** sur le statut calculé (une facture marquée manuellement intégrée reste intégrée même si Asten a qt=0)
- Actions bulk : cocher N factures → "Marquer Intégré" en un appel (1 seul REFRESH)
- **"Tout marquer Intégré"** : traite toutes les factures filtrées (toutes pages) en une seule requête
- Stats live sans rechargement de page (polling sur `/factures/backup/mv-status/`)
- Filtre **Full Asten** (magasins `full_asten=True`)
- Filtre **Exclure certains magasins** (`exclure_factures=True`)

### Bons de réception (BR)
- BR Asten et BR IC
- Suivi intégration `ic_integre` (bool)
- Gestion anomalies

### Assistant IA
- Questions en français → SQL généré par Gemini → exécution → réponse naturelle
- Schéma métier complet : commandes, factures, BR, tickets, écarts
- Règles métier embarquées (vraies valeurs de statut, vraie logique d'intégration)
- Clé API et modèle configurables depuis l'UI (Paramètres → Configuration IA)

### Dashboard
- Vue d'ensemble par source de données
- Comparaison semaine courante / semaine précédente
- Saisie manuelle (override) des stats affichées : intégrées, **à vide**, en écart, total
- Graphiques par magasin

### Versions Asten
- Snapshots des versions `prdP2A` depuis le SMB
- Suivi conformité (nb assortiments OK/incomplet/absent)

### Tickets
- Incidents et demandes magasins
- Statuts : `en_attente` / `resolu`
- Urgences : `tres_basse / basse / moyenne / haute`

### Paramètres
- Gestion magasins (full_asten, exclure_factures)
- Configuration chemins SMB/dossiers
- **Configuration IA** : clé Gemini + modèle (sans redémarrage)
- Gestion utilisateurs et permissions

---

## Architecture

### Apps Django

| App | Rôle |
|-----|------|
| `core/` | Modèle Magasin, permissions, context processors |
| `asten/` | Commandes Asten |
| `cyrus/` | Commandes Cyrus |
| `gpv/` | Commandes GPV |
| `legend/` | Commandes Legend |
| `ecarts/` | Écarts toutes sources |
| `br/` | Bons de réception Asten + IC |
| `imports/` | Import fichiers CSV, modèles factures, vue MV |
| `dashboard/` | UI principale, APIs, assistant IA |
| `tickets/` | Tickets magasins |
| `entree_journal/` | Journal d'intégration RPOS |

### Vue matérialisée `mv_factures_joined`

```sql
-- Jointure Cyrus ↔ Asten ↔ statuts manuels
-- Ordre CASE (priorité) :
--   1. statut_manuel = 'integre'   → 'integre'
--   2. statut_manuel = 'ignore'    → 'ignore'
--   3. Asten présent + qt > 0      → 'integre'
--   4. Asten présent + qt = 0      → 'integre_vide'
--   5. Asten absent                → 'non_integre'
```

Refresh déclenché automatiquement après chaque import ou changement de statut.

### APIs internes

| Endpoint | Rôle |
|----------|------|
| `GET /api/import-status/` | Statut import en cours |
| `GET /api/activite/` | Journal activité live |
| `GET /factures/backup/stats/` | Stats MV live (pour mise à jour sans reload) |
| `GET /factures/backup/mv-status/` | État du REFRESH en cours |
| `POST /factures/backup/set-statut/` | Changer statut 1 facture |
| `POST /factures/backup/set-statut-bulk/` | Changer statut N factures + REFRESH synchrone |

---

## Déploiement production (/opt/Projet_N8N)

### Services

| Service | Rôle | Gestion |
|---------|------|---------|
| `gunicorn` | Serveur Django | `sudo systemctl restart gunicorn` |
| `run_auto.py` | Surveillance SMB → copie media/ | Daemon (voir ci-dessous) |
| cron `auto_import` | Import fichiers + recalcul écarts | Toutes les 5 min |

### Déployer une mise à jour

```bash
# Sur le serveur
git stash          # préserver config.env local
git pull
git stash pop      # remettre config.env local

source env/bin/activate
python manage.py migrate          # appliquer les nouvelles migrations
sudo systemctl restart gunicorn
```

### run_auto.py — Surveillance SMB

```bash
# Vérifier s'il tourne
ps aux | grep run_auto

# Relancer si arrêté
mkdir -p /opt/Projet_N8N/logs
nohup /opt/Projet_N8N/env/bin/python /opt/Projet_N8N/run_auto.py --interval 2 \
  > /opt/Projet_N8N/logs/run_auto.log 2>&1 &

# Logs
tail -f /opt/Projet_N8N/logs/run_auto.log
```

### Cron auto_import

```cron
*/5 * * * * cd /opt/Projet_N8N && env/bin/python manage.py auto_import >> /opt/Projet_N8N/logs/auto_import.log 2>&1
```

```bash
# Lancer manuellement
cd /opt/Projet_N8N && env/bin/python manage.py auto_import

# Logs
tail -f /opt/Projet_N8N/logs/auto_import.log
```

### Démarrage local (après redémarrage PC)

PostgreSQL démarre automatiquement. Pour le serveur Django :

```bash
cd ~/Documents/traitement_n8n
source env/bin/activate
python manage.py runserver
```

---

## Logique métier — Intégration

### Commandes non intégrées (source de vérité = tables d'écarts)

```sql
-- Asten non intégrées
SELECT COUNT(*) FROM ecarts_ecartcommande WHERE statut='ouvert';   -- ex: 14

-- GPV non intégrées
SELECT COUNT(*) FROM ecarts_ecartgpv WHERE statut='ouvert';        -- ex: 14

-- Legend non intégrées
SELECT COUNT(*) FROM ecarts_ecartlegend WHERE statut='ouvert';     -- ex: 50

-- BR non intégrés
SELECT COUNT(*) FROM br_brasten WHERE ic_integre=FALSE;            -- ex: 5
```

> ⚠️ Les colonnes `statut` de `asten_commandeasten` et `gpv_commandegpv` ne contiennent **pas** `'Intégré'` — utiliser les tables d'écarts.

### Factures non intégrées

```sql
SELECT statut_effectif, COUNT(*)
FROM mv_factures_joined
GROUP BY statut_effectif;
-- integre: 55501 | integre_vide: 578 | non_integre: 0 | ignore: 0
```

---

## Notes importantes

- `config.env` est **ignoré par git** — chaque environnement a le sien
- Le quota Gemini free tier est limité (20 req/jour sur `gemini-2.5-flash-lite`) — activer la facturation sur [aistudio.google.com](https://aistudio.google.com) pour une utilisation normale
- Le REFRESH de `mv_factures_joined` prend ~15s — c'est normal, il est synchrone dans le bulk pour garantir des stats à jour
- Les connexions PostgreSQL orphelines : redémarrer Django libère les connexions bloquées (`pg_terminate_backend`)
