# Plateforme de Vérification — Prosuma

Application Django + PostgreSQL pour le contrôle et le rapprochement des commandes, factures et bons de réception entre les systèmes Asten et Cyrus.

---

## Stack technique

- **Backend** : Django 4.x / Python 3.12
- **Base de données** : PostgreSQL 15+
- **IA** : Google Gemini (Text-to-SQL)
- **Serveur production** : Gunicorn (service systemd `projet_n8n`)
- **Surveillance fichiers** : `watcher.service` (watchdog, déclenche les imports) + `run_auto.service` (copie SMB → media en boucle)

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

### Services systemd

Trois services tournent en permanence, chacun avec un rôle distinct (`Restart=always` : redémarrage auto en cas de crash et au reboot du serveur) :

| Service | Rôle | Fichier unit |
|---------|------|--------------|
| `projet_n8n.service` | Serveur Django (gunicorn, 3 workers) | `projet_n8n.service` |
| `watcher.service` | Surveille les dossiers SMB (watchdog), déclenche `manage.py auto_import` à chaque nouveau fichier (debounce 30s) | `watcher.service` |
| `run_auto.service` | Copie en boucle (toutes les 2 min) les fichiers SMB → `media/` | `run_auto.service` |

> ⚠️ `projet_n8n.service` doit lancer **gunicorn**, jamais `manage.py runserver` (mono-thread, fuite mémoire en continu sous charge — a déjà provoqué un swap de 8 Go et un load average > 28 en production).

> ℹ️ `watcher.service` ne fait **que** déclencher l'import Django (`auto_import`) sur détection de fichier — il ne relance plus `run_auto.py` lui-même, pour éviter le double travail avec `run_auto.service` qui tourne déjà en boucle indépendamment.

### Première installation des services sur le serveur

```bash
cd /opt/Projet_N8N
source env/bin/activate
pip install -r requirements.txt   # inclut gunicorn et watchdog

sudo cp projet_n8n.service /etc/systemd/system/projet_n8n.service
sudo cp watcher.service    /etc/systemd/system/watcher.service
sudo cp run_auto.service   /etc/systemd/system/run_auto.service

sudo systemctl daemon-reload
sudo systemctl enable projet_n8n watcher run_auto
sudo systemctl start projet_n8n watcher run_auto

# Vérifier que tout est "active (running)"
sudo systemctl status projet_n8n watcher run_auto
```

> Les partages SMB (`/mnt/partage-share`, `/mnt/asten`, etc.) doivent être montés **avant** de démarrer `watcher`/`run_auto`, sinon ils tournent "à vide" sans trouver les dossiers source. Voir `start_smb_and_server.sh` (montage interactif, demande les identifiants SMB). Ce montage n'est **pas persistant après un reboot** du serveur — à refaire manuellement si le serveur redémarre.

### Déployer une mise à jour

```bash
cd /opt/Projet_N8N
git pull

source env/bin/activate
pip install -r requirements.txt   # si de nouvelles dépendances ont été ajoutées
python manage.py migrate          # appliquer les nouvelles migrations

# Si projet_n8n.service ou watcher.service ou run_auto.service ont changé :
sudo cp projet_n8n.service /etc/systemd/system/projet_n8n.service
sudo cp watcher.service    /etc/systemd/system/watcher.service
sudo cp run_auto.service   /etc/systemd/system/run_auto.service
sudo systemctl daemon-reload

sudo systemctl restart projet_n8n watcher run_auto
sudo systemctl status projet_n8n watcher run_auto
```

> `config.env` est dans `.gitignore` et n'est jamais affecté par `git pull` — pas besoin de stash.

### Diagnostiquer un service

```bash
# État + dernières lignes de log
sudo systemctl status projet_n8n
sudo systemctl status watcher
sudo systemctl status run_auto

# Suivre les logs en direct (Ctrl+C pour sortir sans arrêter le service)
journalctl -u projet_n8n -f
journalctl -u watcher -f
journalctl -u run_auto -f
```

Si `watcher` ou `run_auto` boucle en "activating (auto-restart)" avec des erreurs `ModuleNotFoundError`, le venv du serveur n'a probablement pas toutes les dépendances :

```bash
cd /opt/Projet_N8N
source env/bin/activate
pip install -r requirements.txt
sudo systemctl restart watcher run_auto
```

### Charge serveur — points de vigilance

- Surveiller `free -h` et `top` après un déploiement : un load average élevé (>10) avec beaucoup de swap utilisé indique souvent que `projet_n8n.service` tourne encore en mode `runserver` au lieu de gunicorn (voir plus haut).
- La page **Activité en direct** (`/activite/`) fait du polling toutes les 5s sur `GET /api/activite/` — cette vue lit `/proc` pour détecter `run_auto.py` (pas de fork de process type `pgrep`, volontairement, pour rester rapide même sous charge).

### Démarrage en local (développement, après redémarrage PC)

PostgreSQL démarre automatiquement. Pour le serveur Django :

```bash
cd ~/Documents/traitement_n8n
source env/bin/activate
python manage.py runserver
```

> En local (dev), `runserver` est approprié. C'est uniquement en production qu'il faut gunicorn.

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
