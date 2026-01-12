import os
import shutil
import pandas as pd
from django.core.management.base import BaseCommand
from django.conf import settings
from django.utils import timezone
from traitement.models import Controle, Ecart, FichierSource
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Traite les fichiers de commande (Source A et Source B) et effectue la comparaison'

    def add_arguments(self, parser):
        parser.add_argument(
            '--periode',
            type=str,
            help='Période du contrôle (ex: 2026-01)',
            default=timezone.now().strftime('%Y-%m')
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Forcer le retraitement même si les fichiers ont déjà été traités',
        )

    def handle(self, *args, **options):
        periode = options['periode']
        force = options['force']
        
        self.stdout.write(self.style.SUCCESS(f'Début du traitement des commandes pour la période {periode}'))
        
        # Chemins des dossiers
        path_asten = settings.EXTRACTIONS_PATHS.get('commande_auto')
        path_cyrus = settings.EXTRACTIONS_PATHS.get('commande_manual')
        path_archive = settings.EXTRACTIONS_PATHS.get('archive')
        
        # Créer les dossiers s'ils n'existent pas
        os.makedirs(path_archive, exist_ok=True)
        
        # Chercher les fichiers CSV
        fichiers_asten = self._get_csv_files(path_asten)
        fichiers_cyrus = self._get_csv_files(path_cyrus)
        
        # NOUVELLE LOGIQUE: Traiter même si un seul type de fichier est présent
        # Si seulement Asten présent -> utiliser les fichiers Cyrus du contrôle précédent
        # Si seulement Cyrus présent -> utiliser les fichiers Asten du contrôle précédent
        
        utiliser_cyrus_ancien = False
        utiliser_asten_ancien = False
        
        if not fichiers_asten and not fichiers_cyrus:
            self.stdout.write(self.style.WARNING('Aucun fichier trouvé (ni Asten ni Cyrus)'))
            return
        
        if not fichiers_asten:
            self.stdout.write(self.style.WARNING(f'Aucun fichier Asten trouvé. Utilisation des fichiers Asten du contrôle précédent.'))
            utiliser_asten_ancien = True
        
        if not fichiers_cyrus:
            self.stdout.write(self.style.WARNING(f'Aucun fichier Cyrus trouvé dans {path_cyrus}'))
            self.stdout.write(self.style.WARNING('Tentative d\'utilisation des fichiers Cyrus du contrôle précédent...'))
            utiliser_cyrus_ancien = True
        
        # Créer le contrôle
        controle = Controle.objects.create(
            type_controle='commande',
            periode=periode,
            date_execution=timezone.now(),
            statut='en_cours'
        )
        
        try:
            # Charger les fichiers Cyrus (nouveaux ou anciens)
            dfs_cyrus = []
            controle_precedent = None
            
            if utiliser_cyrus_ancien:
                # Récupérer les fichiers Cyrus du contrôle précédent (seulement les contrôles terminés)
                controle_precedent = Controle.objects.filter(
                    type_controle='commande',
                    statut='termine'  # Seulement les contrôles terminés avec succès
                ).exclude(id=controle.id).order_by('-date_execution').first()
                
                if controle_precedent:
                    fichiers_cyrus_anciens = FichierSource.objects.filter(
                        controle=controle_precedent,
                        origine='cyrus'
                    )
                    
                    if fichiers_cyrus_anciens.exists():
                        self.stdout.write(
                            self.style.SUCCESS(
                                f'✓ Contrôle précédent trouvé: {controle_precedent.periode} '
                                f'({controle_precedent.date_execution.strftime("%d/%m/%Y %H:%M")})'
                            )
                        )
                        self.stdout.write(f'Chargement de {fichiers_cyrus_anciens.count()} fichier(s) Cyrus du contrôle précédent...')
                        
                        fichiers_charges = 0
                        for fs_cyrus in fichiers_cyrus_anciens:
                            # Chercher dans l'archive si le fichier n'existe plus
                            chemin_fichier = fs_cyrus.chemin
                            if not os.path.exists(chemin_fichier):
                                import glob
                                nom_fichier = os.path.basename(chemin_fichier)
                                archive_path = os.path.join(path_archive, '*', nom_fichier)
                                fichiers_archive = glob.glob(archive_path)
                                if fichiers_archive:
                                    chemin_fichier = fichiers_archive[0]
                                else:
                                    self.stdout.write(
                                        self.style.WARNING(
                                            f'  ⚠ Fichier non trouvé (ni original ni archive): {nom_fichier}'
                                        )
                                    )
                                    continue
                            
                            df = self._load_csv(chemin_fichier)
                            if df is not None:
                                dfs_cyrus.append(df)
                                fichiers_charges += 1
                                self.stdout.write(
                                    self.style.SUCCESS(
                                        f'  ✓ Chargé (ancien): {os.path.basename(chemin_fichier)} ({len(df)} lignes)'
                                    )
                                )
                        
                        if fichiers_charges == 0:
                            self.stdout.write(
                                self.style.ERROR(
                                    'Aucun fichier Cyrus valide trouvé dans le contrôle précédent.'
                                )
                            )
                            controle.statut = 'erreur'
                            controle.save()
                            return
                    else:
                        self.stdout.write(
                            self.style.WARNING(
                                f'Le contrôle précédent ({controle_precedent.periode}) n\'a pas de fichiers Cyrus enregistrés.'
                            )
                        )
                        controle.statut = 'erreur'
                        controle.save()
                        return
                else:
                    self.stdout.write(
                        self.style.ERROR(
                            'Aucun contrôle précédent terminé trouvé avec des fichiers Cyrus.'
                        )
                    )
                    self.stdout.write(
                        self.style.WARNING(
                            'Le traitement nécessite au moins un fichier Cyrus pour comparer.'
                        )
                    )
                    controle.statut = 'erreur'
                    controle.save()
                    return
            else:
                # Charger les nouveaux fichiers Cyrus
                self.stdout.write(f'Chargement de {len(fichiers_cyrus)} fichier(s) Cyrus...')
                for fichier_cyrus in fichiers_cyrus:
                    df = self._load_csv(fichier_cyrus)
                    if df is not None:
                        dfs_cyrus.append(df)
                        self.stdout.write(f'  ✓ Chargé: {os.path.basename(fichier_cyrus)} ({len(df)} lignes)')
            
            if not dfs_cyrus:
                self.stdout.write(self.style.ERROR('Aucun fichier Cyrus valide trouvé'))
                controle.statut = 'erreur'
                controle.save()
                return
            
            # Combiner tous les dataframes Cyrus
            df_cyrus_consolide = pd.concat(dfs_cyrus, ignore_index=True)
            self.stdout.write(f'  → Total Cyrus: {len(df_cyrus_consolide)} lignes consolidées')
            
            # Charger les fichiers Asten (nouveaux ou anciens)
            fichiers_asten_a_traiter = fichiers_asten if not utiliser_asten_ancien else []
            
            if utiliser_asten_ancien:
                # Récupérer les fichiers Asten du contrôle précédent
                if not controle_precedent:
                    controle_precedent = Controle.objects.filter(
                        type_controle='commande'
                    ).exclude(id=controle.id).order_by('-date_execution').first()
                
                if controle_precedent:
                    fichiers_asten_anciens = FichierSource.objects.filter(
                        controle=controle_precedent,
                        origine='asten'
                    )
                    self.stdout.write(f'Chargement de {fichiers_asten_anciens.count()} fichier(s) Asten du contrôle précédent...')
                    
                    for fs_asten in fichiers_asten_anciens:
                        # Chercher dans l'archive si le fichier n'existe plus
                        chemin_fichier = fs_asten.chemin
                        if not os.path.exists(chemin_fichier):
                            import glob
                            nom_fichier = os.path.basename(chemin_fichier)
                            archive_path = os.path.join(path_archive, '*', nom_fichier)
                            fichiers_archive = glob.glob(archive_path)
                            if fichiers_archive:
                                chemin_fichier = fichiers_archive[0]
                            else:
                                continue
                        
                        # Ajouter à la liste pour traitement
                        fichiers_asten_a_traiter.append(chemin_fichier)
                        self.stdout.write(f'  ✓ Fichier Asten (ancien) ajouté: {nom_fichier}')
                else:
                    self.stdout.write(self.style.ERROR('Aucun contrôle précédent trouvé avec des fichiers Asten'))
                    controle.statut = 'erreur'
                    controle.save()
                    return
            
            # Récupérer les écarts précédents pour détecter les corrections
            if not controle_precedent:
                controle_precedent = Controle.objects.filter(
                    type_controle='commande'
                ).exclude(id=controle.id).order_by('-date_execution').first()
            
            ecarts_precedents = {}
            if controle_precedent:
                for ecart_prec in Ecart.objects.filter(controle=controle_precedent):
                    # Créer une clé composite pour identifier l'écart
                    ref_prec = ecart_prec.reference
                    ecarts_precedents[ref_prec] = ecart_prec
            
            # Traiter chaque fichier Asten
            total_lignes_asten = 0
            total_ecarts = 0
            
            for fichier_asten in fichiers_asten_a_traiter:
                # Vérifier si déjà traité
                if not force:
                    if FichierSource.objects.filter(
                        nom_fichier=os.path.basename(fichier_asten),
                        traite=True
                    ).exists():
                        self.stdout.write(self.style.WARNING(f'Fichier {os.path.basename(fichier_asten)} déjà traité. Utilisez --force pour forcer.'))
                        continue
                
                # Enregistrer le fichier source Asten (seulement si nouveau)
                if not utiliser_asten_ancien or fichier_asten in fichiers_asten:
                    fs_asten = FichierSource.objects.create(
                        type_controle='commande',
                        origine='asten',
                        nom_fichier=os.path.basename(fichier_asten),
                        chemin=fichier_asten,
                        controle=controle
                    )
                else:
                    # Pour les fichiers anciens, créer une référence sans dupliquer
                    fs_asten = FichierSource.objects.create(
                        type_controle='commande',
                        origine='asten',
                        nom_fichier=os.path.basename(fichier_asten),
                        chemin=fichier_asten,
                        controle=controle,
                        traite=True  # Marquer comme déjà traité pour éviter l'archivage
                    )
                
                # Enregistrer les fichiers Cyrus (une seule fois, seulement si nouveaux)
                if fichier_asten == fichiers_asten_a_traiter[0] and not utiliser_cyrus_ancien:
                    for fichier_cyrus in fichiers_cyrus:
                        FichierSource.objects.create(
                            type_controle='commande',
                            origine='cyrus',
                            nom_fichier=os.path.basename(fichier_cyrus),
                            chemin=fichier_cyrus,
                            controle=controle,
                            traite=True
                        )
                elif fichier_asten == fichiers_asten_a_traiter[0] and utiliser_cyrus_ancien:
                    # Référencer les fichiers Cyrus anciens
                    if controle_precedent:
                        fichiers_cyrus_anciens = FichierSource.objects.filter(
                            controle=controle_precedent,
                            origine='cyrus'
                        )
                        for fs_cyrus_ancien in fichiers_cyrus_anciens:
                            FichierSource.objects.create(
                                type_controle='commande',
                                origine='cyrus',
                                nom_fichier=fs_cyrus_ancien.nom_fichier,
                                chemin=fs_cyrus_ancien.chemin,
                                controle=controle,
                                traite=True
                            )
                
                # Charger le fichier Asten
                self.stdout.write(f'\nTraitement de {os.path.basename(fichier_asten)}...')
                df_asten = self._load_csv(fichier_asten)
                
                if df_asten is None:
                    self.stdout.write(self.style.ERROR(f'Erreur lors du chargement de {fichier_asten}'))
                    continue
                
                self.stdout.write(f'  → {len(df_asten)} lignes dans Asten')
                
                # Effectuer la comparaison avec le dataframe Cyrus consolidé
                ecarts = self._compare_dataframes(df_asten, df_cyrus_consolide, controle, ecarts_precedents)
                
                total_lignes_asten += len(df_asten)
                total_ecarts += len(ecarts)
                
                # Marquer le fichier Asten comme traité (seulement si nouveau)
                if not utiliser_asten_ancien or fichier_asten in fichiers_asten:
                    fs_asten.traite = True
                    fs_asten.save()
                    
                    # Archiver le fichier Asten (seulement si nouveau)
                    try:
                        date_str = timezone.now().strftime('%Y%m%d')
                        archive_subdir = os.path.join(path_archive, date_str)
                        os.makedirs(archive_subdir, exist_ok=True)
                        shutil.move(fichier_asten, os.path.join(archive_subdir, os.path.basename(fichier_asten)))
                    except Exception as e:
                        logger.warning(f'Erreur lors de l\'archivage de {fichier_asten}: {str(e)}')
                
                self.stdout.write(
                    self.style.SUCCESS(
                        f'  ✓ {len(ecarts)} écarts détectés'
                    )
                )
            
            # Archiver les fichiers Cyrus (une seule fois à la fin, seulement si nouveaux)
            if not utiliser_cyrus_ancien:
                try:
                    date_str = timezone.now().strftime('%Y%m%d')
                    archive_subdir = os.path.join(path_archive, date_str)
                    os.makedirs(archive_subdir, exist_ok=True)
                    for fichier_cyrus in fichiers_cyrus:
                        if os.path.exists(fichier_cyrus):
                            shutil.move(fichier_cyrus, os.path.join(archive_subdir, os.path.basename(fichier_cyrus)))
                except Exception as e:
                    logger.warning(f'Erreur lors de l\'archivage des fichiers Cyrus: {str(e)}')
            
            # Mettre à jour les statistiques du contrôle
            controle.total_lignes = total_lignes_asten + len(df_cyrus_consolide)
            controle.total_ecarts = total_ecarts
            
            # Finaliser le contrôle
            controle.statut = 'termine'
            controle.save()
            
            self.stdout.write(
                self.style.SUCCESS(
                    f'\n✓ Contrôle terminé: {controle.total_ecarts} écarts sur {controle.total_lignes} lignes '
                    f'(Taux de conformité: {controle.taux_conformite}%)'
                )
            )
            
        except Exception as e:
            controle.statut = 'erreur'
            controle.save()
            logger.error(f'Erreur lors du traitement: {str(e)}', exc_info=True)
            self.stdout.write(self.style.ERROR(f'Erreur: {str(e)}'))
            raise

    def _get_csv_files(self, path):
        """Récupère la liste des fichiers CSV dans un dossier"""
        if not os.path.exists(path):
            return []
        
        fichiers = []
        for file in os.listdir(path):
            if file.endswith('.csv'):
                fichiers.append(os.path.join(path, file))
        
        return sorted(fichiers)

    def _load_csv(self, filepath):
        """Charge un fichier CSV avec pandas"""
        try:
            # Essayer avec point-virgule d'abord (format français)
            df = pd.read_csv(filepath, sep=';', encoding='utf-8', low_memory=False)
            if len(df.columns) == 1:
                # Si une seule colonne, essayer avec virgule
                df = pd.read_csv(filepath, sep=',', encoding='utf-8', low_memory=False)
            return df
        except Exception as e:
            logger.error(f'Erreur lors du chargement de {filepath}: {str(e)}')
            return None

    def _compare_dataframes(self, df_asten, df_cyrus, controle, ecarts_precedents=None):
        """
        Compare deux dataframes et identifie les écarts.
        LOGIQUE MÉTIER:
        - Toutes les commandes Asten DOIVENT être présentes dans Cyrus (sinon écart)
        - Les commandes Cyrus peuvent exister sans être dans Asten (pas d'écart)
        - On cherche donc uniquement les commandes Asten absentes de Cyrus ou avec valeurs différentes
        
        Comparaison basée sur une clé composite :
        - Asten : 'Référence commande' + 'Date commande'
        - Cyrus : 'NCDE' + 'DCDE'
        
        Args:
            df_asten: DataFrame contenant les données Asten
            df_cyrus: DataFrame contenant les données Cyrus
            controle: Objet Controle associé
            ecarts_precedents: Dict des écarts précédents pour détecter les corrections (optionnel)
        """
        ecarts = []
        
        # Mapping des colonnes entre Asten et Cyrus
        # Asten -> Cyrus
        ref_col_asten = 'Référence commande'
        date_col_asten = 'Date commande'
        ref_col_cyrus = 'NCDE'
        date_col_cyrus = 'DCDE'
        
        # Vérifier que les colonnes existent dans Asten
        if ref_col_asten not in df_asten.columns:
            cols_asten = ', '.join(df_asten.columns.tolist())
            raise ValueError(
                f'Colonne "{ref_col_asten}" introuvable dans le fichier Asten.\n'
                f'Colonnes disponibles: {cols_asten}'
            )
        
        if date_col_asten not in df_asten.columns:
            cols_asten = ', '.join(df_asten.columns.tolist())
            raise ValueError(
                f'Colonne "{date_col_asten}" introuvable dans le fichier Asten.\n'
                f'Colonnes disponibles: {cols_asten}'
            )
        
        # Vérifier que les colonnes existent dans Cyrus
        if ref_col_cyrus not in df_cyrus.columns:
            cols_cyrus = ', '.join(df_cyrus.columns.tolist())
            raise ValueError(
                f'Colonne "{ref_col_cyrus}" introuvable dans le fichier Cyrus.\n'
                f'Colonnes disponibles: {cols_cyrus}'
            )
        
        if date_col_cyrus not in df_cyrus.columns:
            cols_cyrus = ', '.join(df_cyrus.columns.tolist())
            raise ValueError(
                f'Colonne "{date_col_cyrus}" introuvable dans le fichier Cyrus.\n'
                f'Colonnes disponibles: {cols_cyrus}'
            )
        
        self.stdout.write(
            f'  → Mapping des colonnes:\n'
            f'     Asten: "{ref_col_asten}" + "{date_col_asten}"\n'
            f'     Cyrus: "{ref_col_cyrus}" + "{date_col_cyrus}"'
        )
        
        # Nettoyer et normaliser les valeurs (supprimer les NaN et convertir en string)
        df_asten[ref_col_asten] = df_asten[ref_col_asten].astype(str).str.strip()
        df_asten[date_col_asten] = df_asten[date_col_asten].astype(str).str.strip()
        df_cyrus[ref_col_cyrus] = df_cyrus[ref_col_cyrus].astype(str).str.strip()
        df_cyrus[date_col_cyrus] = df_cyrus[date_col_cyrus].astype(str).str.strip()
        
        # Supprimer les lignes avec valeurs vides ou 'nan'
        df_asten = df_asten[
            ~df_asten[ref_col_asten].isin(['', 'nan', 'None', 'NaN']) &
            ~df_asten[date_col_asten].isin(['', 'nan', 'None', 'NaN'])
        ]
        df_cyrus = df_cyrus[
            ~df_cyrus[ref_col_cyrus].isin(['', 'nan', 'None', 'NaN']) &
            ~df_cyrus[date_col_cyrus].isin(['', 'nan', 'None', 'NaN'])
        ]
        
        # Fonction pour normaliser les dates (gérer différents formats)
        def normalize_date(date_str, is_cyrus=False):
            """
            Normalise une date en string pour la comparaison.
            Pour Cyrus : format YYMMDD (ex: 260107 = 07/01/2026)
            Pour Asten : format DD/MM/YYYY ou DDMMYY
            """
            if pd.isna(date_str) or str(date_str).strip() in ['', 'nan', 'None', 'NaN']:
                return ''
            date_str = str(date_str).strip()
            
            # Format 1: YYMMDD (6 chiffres) - Format Cyrus -> DDMMYYYY
            # Exemple : 260107 = YY=26, MM=01, DD=07 -> 07012026
            if is_cyrus and len(date_str) == 6 and date_str.isdigit():
                year_short = date_str[:2]  # YY
                month = date_str[2:4]      # MM
                day = date_str[4:6]        # DD
                try:
                    year = f"20{year_short}"
                    return f"{day}{month}{year}"  # DDMMYYYY pour comparaison
                except:
                    return date_str
            
            # Format 2: DDMMYY (6 chiffres) - Format Asten -> DDMMYYYY
            if not is_cyrus and len(date_str) == 6 and date_str.isdigit():
                day = date_str[:2]
                month = date_str[2:4]
                year_short = date_str[4:6]
                try:
                    year_int = int(year_short)
                    if year_int < 50:
                        year = f"20{year_short}"
                    else:
                        year = f"19{year_short}"
                    return f"{day}{month}{year}"
                except:
                    return date_str
            
            # Format 2: DDMMYYYY (8 chiffres) -> garder tel quel
            if len(date_str) == 8 and date_str.isdigit():
                return date_str
            
            # Format 3: DD/MM/YYYY ou DD/MM/YYYY HH:MM:SS -> DDMMYYYY
            import re
            # Pattern pour DD/MM/YYYY ou DD/MM/YYYY HH:MM:SS
            match = re.match(r'(\d{2})/(\d{2})/(\d{4})', date_str)
            if match:
                day, month, year = match.groups()
                return f"{day}{month}{year}"
            
            # Format 4: YYYY-MM-DD -> DDMMYYYY
            match = re.match(r'(\d{4})-(\d{2})-(\d{2})', date_str)
            if match:
                year, month, day = match.groups()
                return f"{day}{month}{year}"
            
            return date_str
        
        # Créer une fonction pour générer la clé composite
        def create_key(ref, date, is_cyrus=False):
            """Crée une clé composite à partir de la référence et de la date normalisée"""
            ref_clean = str(ref).strip() if not pd.isna(ref) else ''
            date_normalized = normalize_date(date, is_cyrus=is_cyrus)
            return f"{ref_clean}|{date_normalized}"
        
        # Normaliser les dates avant la comparaison
        self.stdout.write('  → Normalisation des dates...')
        
        # Créer un index sur Cyrus avec clé composite
        # Pour Cyrus, utiliser is_cyrus=True pour le format YYMMDD
        cyrus_dict = {}
        for idx, row in df_cyrus.iterrows():
            # Normaliser la date Cyrus avec le bon format
            date_cyrus_normalized = normalize_date(row[date_col_cyrus], is_cyrus=True)
            ref_cyrus_clean = str(row[ref_col_cyrus]).strip() if not pd.isna(row[ref_col_cyrus]) else ''
            key = f"{ref_cyrus_clean}|{date_cyrus_normalized}" if ref_cyrus_clean and date_cyrus_normalized else ''
            if key and key != '|':  # Ignorer les clés vides
                if key not in cyrus_dict:
                    cyrus_dict[key] = []
                cyrus_dict[key].append(row)
        
        self.stdout.write(f'  → {len(cyrus_dict)} clés uniques créées dans Cyrus')
        
        # Parcourir toutes les lignes de Asten et chercher dans Cyrus
        keys_asten = set()
        keys_trouves = set()
        
        for idx, row_asten in df_asten.iterrows():
            # Créer la clé composite pour la ligne Asten (format DD/MM/YYYY ou DDMMYY)
            key_asten = create_key(row_asten[ref_col_asten], row_asten[date_col_asten], is_cyrus=False)
            if not key_asten or key_asten == '|':  # Ignorer les clés vides
                continue
            keys_asten.add(key_asten)
            
            if key_asten in cyrus_dict:
                # La clé existe dans Cyrus - vérifier les valeurs
                keys_trouves.add(key_asten)
                rows_cyrus = cyrus_dict[key_asten]
                
                # Comparer avec la première occurrence dans Cyrus
                row_cyrus = rows_cyrus[0]
                
                # Comparer les valeurs (exclure les colonnes de clé)
                diff_cols = []
                for col in df_asten.columns:
                    if col in [ref_col_asten, date_col_asten]:
                        continue
                    
                    # Essayer de trouver la colonne correspondante dans Cyrus
                    # Si le nom est identique, comparer directement
                    if col in df_cyrus.columns:
                        val_asten = str(row_asten.get(col, '')).strip()
                        val_cyrus = str(row_cyrus.get(col, '')).strip()
                        
                        if val_asten != val_cyrus:
                            diff_cols.append({
                                'colonne': col,
                                'valeur_asten': val_asten,
                                'valeur_cyrus': val_cyrus
                            })
                
                if diff_cols:
                    # Écart: Valeurs différentes
                    reference_display = f"{row_asten[ref_col_asten]} ({row_asten[date_col_asten]})"
                    ecart = Ecart.objects.create(
                        controle=controle,
                        reference=reference_display,
                        valeur_source_a=str(row_asten.to_dict()),
                        valeur_source_b=str(row_cyrus.to_dict()),
                        type_ecart='valeur_differente',
                        details={
                            'colonnes_differentes': diff_cols,
                            'cle_composite': key_asten
                        }
                    )
                    ecarts.append(ecart)
            else:
                # Écart: Présent dans Asten mais absent dans Cyrus
                reference_display = f"{row_asten[ref_col_asten]} ({row_asten[date_col_asten]})"
                ecart = Ecart.objects.create(
                    controle=controle,
                    reference=reference_display,
                    valeur_source_a=str(row_asten.to_dict()),
                    valeur_source_b=None,
                    type_ecart='absent_b',  # Absent dans Cyrus (Source B)
                    details={
                        'message': 'Ligne présente dans Asten mais absente dans Cyrus',
                        'cle_composite': key_asten,
                        'reference_commande': row_asten[ref_col_asten],
                        'date_commande': row_asten[date_col_asten]
                    }
                )
                ecarts.append(ecart)
        
        # Détecter les écarts corrigés (présents dans les écarts précédents mais plus maintenant)
        ecarts_corriges = []
        if ecarts_precedents:
            # Créer un set des références avec écarts actuels
            references_avec_ecarts = set()
            for ecart in ecarts:
                references_avec_ecarts.add(ecart.reference)
            
            # Parcourir les écarts précédents et vérifier s'ils sont corrigés
            for ref_prec, ecart_prec in ecarts_precedents.items():
                if ref_prec not in references_avec_ecarts:
                    # L'écart précédent n'existe plus -> il a été corrigé
                    # Créer un écart de type "corrige" pour indiquer la correction
                    try:
                        # Extraire les données de l'écart précédent
                        valeur_source_a_prec = ecart_prec.valeur_source_a
                        valeur_source_b_prec = ecart_prec.valeur_source_b
                        
                        ecart_corrige = Ecart.objects.create(
                            controle=controle,
                            reference=ref_prec,
                            valeur_source_a=valeur_source_a_prec,
                            valeur_source_b=valeur_source_b_prec,
                            type_ecart='corrige',
                            details={
                                'message': 'Écart corrigé - Cette commande était absente de Cyrus mais est maintenant présente',
                                'ecart_precedent_id': str(ecart_prec.id),
                                'date_correction': timezone.now().isoformat()
                            }
                        )
                        ecarts_corriges.append(ecart_corrige)
                        ecarts.append(ecart_corrige)
                    except Exception as e:
                        logger.warning(f'Erreur lors de la création de l\'écart corrigé pour {ref_prec}: {str(e)}')
        
        self.stdout.write(
            f'  → {len(keys_asten)} clés composites dans Asten, '
            f'{len(keys_trouves)} trouvées dans Cyrus, '
            f'{len(ecarts)} écarts détectés'
        )
        
        if ecarts_corriges:
            self.stdout.write(
                self.style.SUCCESS(
                    f'  → {len(ecarts_corriges)} écart(s) corrigé(s) détecté(s)'
                )
            )
        
        # Log de débogage : afficher quelques exemples de clés
        if len(keys_asten) > 0:
            sample_keys = list(keys_asten)[:3]
            self.stdout.write(f'  → Exemples de clés Asten: {sample_keys}')
        if len(cyrus_dict) > 0:
            sample_cyrus_keys = list(cyrus_dict.keys())[:3]
            self.stdout.write(f'  → Exemples de clés Cyrus: {sample_cyrus_keys}')
        
        return ecarts


