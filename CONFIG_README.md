# Configuration des chemins d'import

Le fichier `config.env` permet de configurer les chemins des dossiers où vous déposez les fichiers à importer.

## Utilisation

1. **Ouvrez le fichier `config.env`** à la racine du projet

2. **Modifiez les chemins** selon vos besoins :
   - **Chemins relatifs** : Relatifs au dossier `media/` (par défaut)
     ```
     DOSSIER_COMMANDES_ASTEN=commande_asten
     ```
   
   - **Chemins absolus** : Chemins complets sur votre système
     ```
     DOSSIER_COMMANDES_ASTEN=/home/user/mes_fichiers/commande_asten
     ```

3. **Sauvegardez le fichier** - Les modifications sont prises en compte automatiquement au prochain chargement de l'application

## Variables disponibles

- `MEDIA_ROOT` : Chemin de base pour les médias (par défaut: `media/`)
- `DOSSIER_COMMANDES_ASTEN` : Dossier des commandes Asten
- `DOSSIER_COMMANDES_CYRUS` : Dossier des commandes Cyrus
- `DOSSIER_COMMANDES_GPV` : Dossier des commandes GPV
- `DOSSIER_COMMANDES_LEGEND` : Dossier des commandes Legend
- `DOSSIER_BR_ASTEN` : Dossier des BR Asten
- `DOSSIER_BR_IC` : Dossier des BR IC
- `DOSSIER_FACTURE_BACKUP` : Dossier des factures Backup (Cyrus)
- `DOSSIER_FACTURES_SAGE` : Dossier des factures Sage (CSV)
- `FACTURES_SAGE_PREFIX` : Préfixe des fichiers Sage (par défaut: `SAGE-I-SP_`)

## Exemples

### Exemple 1 : Chemins relatifs (par défaut)
```
MEDIA_ROOT=media
DOSSIER_COMMANDES_ASTEN=commande_asten
DOSSIER_COMMANDES_CYRUS=commande_cyrus
```

### Exemple 2 : Chemins absolus
```
DOSSIER_COMMANDES_ASTEN=/home/user/depot/commandes_asten
DOSSIER_COMMANDES_CYRUS=/home/user/depot/commandes_cyrus
DOSSIER_COMMANDES_GPV=/home/user/depot/commandes_gpv
DOSSIER_COMMANDES_LEGEND=/home/user/depot/commandes_legend
DOSSIER_BR_ASTEN=/home/user/depot/br_asten
DOSSIER_BR_IC=/home/user/depot/br_ic
DOSSIER_FACTURE_BACKUP=/home/user/depot/facture_backup
DOSSIER_FACTURES_SAGE=/mnt/automate-ic
FACTURES_SAGE_PREFIX=SAGE-I-SP_
```

### Exemple 3 : Chemins réseau (UNC/Samba)
```
DOSSIER_COMMANDES_ASTEN=//10.0.70.169/share/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE
DOSSIER_COMMANDES_CYRUS=//10.0.70.169/share/fofana/commande_cyrus
DOSSIER_COMMANDES_GPV=//10.0.70.169/share/fofana/commande_gpv
DOSSIER_COMMANDES_LEGEND=//10.0.70.169/share/fofana/commande_legend
DOSSIER_BR_ASTEN=//10.0.70.169/share/fofana/br_asten
DOSSIER_BR_IC=//10.0.70.169/share/fofana/br_ic
DOSSIER_FACTURE_BACKUP=//10.0.70.169/share/fofana/facture_backup
DOSSIER_FACTURES_SAGE=smb://10.0.70.150/automate-ic/ARCHIVES
FACTURES_SAGE_PREFIX=SAGE-I-SP_
```

**Note importante pour les chemins réseau SMB :**
Sur Linux, pour accéder à un partage SMB, vous devez généralement le monter d'abord. Voici comment procéder :

1. **Créer un point de montage** (optionnel, si vous voulez monter le partage) :
   ```bash
   sudo mkdir -p /mnt/smb_share
   ```

2. **Monter le partage SMB** :
   ```bash
   sudo mount -t cifs //10.0.70.169/share /mnt/smb_share -o username=votre_utilisateur,password=votre_mot_de_passe,uid=$(id -u),gid=$(id -g)
   ```

3. **Utiliser le chemin monté** dans `config.env` :
   ```
   DOSSIER_COMMANDES_ASTEN=/mnt/smb_share/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE
   ```

   **OU** utiliser directement le chemin UNC (si votre système le supporte) :
   ```
   DOSSIER_COMMANDES_ASTEN=//10.0.70.169/share/FOFANA/Etats Natacha/Commande/PRESENTATION_COMMANDE
   ```

4. **Pour monter automatiquement au démarrage**, ajoutez dans `/etc/fstab` :
   ```
   //10.0.70.169/share /mnt/smb_share cifs username=votre_utilisateur,password=votre_mot_de_passe,uid=1000,gid=1000,iocharset=utf8 0 0
   ```

### Exemple 4 : Mixte (certains relatifs, certains absolus, certains réseau)
```
DOSSIER_COMMANDES_ASTEN=commande_asten
DOSSIER_COMMANDES_CYRUS=/chemin/absolu/vers/cyrus
DOSSIER_COMMANDES_GPV=//10.0.70.169/share/fofana/commande_gpv
DOSSIER_FACTURE_BACKUP=facture_backup
```

## Important

- Les modifications dans `config.env` sont prises en compte **automatiquement** au prochain chargement de l'application
- Pas besoin de redémarrer le serveur Django (sauf si le serveur est déjà en cours d'exécution)
- Les dossiers seront créés automatiquement s'ils n'existent pas
- Le fichier `config.env` est ignoré par Git (dans `.gitignore`) pour protéger vos chemins personnels

