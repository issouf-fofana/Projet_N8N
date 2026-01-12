import json
import os
import subprocess
import pandas as pd
import logging
import glob
import re
from django.shortcuts import render, redirect
from django.http import JsonResponse
from django.db.models import Q, Count, Sum
from django.conf import settings
from django.views.decorators.http import require_http_methods
from django.core.serializers.json import DjangoJSONEncoder
from django.core.paginator import Paginator, EmptyPage, PageNotAnInteger
from traitement.models import Controle, Ecart, FichierSource
from traitement.management.commands.process_commande import Command as ProcessCommandeCommand
from django.utils import timezone
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


def load_magasins():
    """Charge les magasins depuis le fichier JSON"""
    from pathlib import Path
    BASE_DIR = Path(__file__).resolve().parent.parent
    json_path = BASE_DIR / 'magasins.json'
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def parse_data(value):
    """Parse les données depuis une string"""
    if not value:
        return {}
    
    # Remplacer 'nan' par None pour éviter les erreurs de parsing
    if isinstance(value, str):
        # Remplacer nan, NaN, None par des valeurs null JSON
        value_cleaned = value.replace('nan', 'None').replace('NaN', 'None')
    else:
        value_cleaned = value
    
    # Essayer d'abord avec ast.literal_eval
    try:
        import ast
        result = ast.literal_eval(value_cleaned)
        # Convertir None en dict vide si nécessaire
        if result is None:
            return {}
        return result if isinstance(result, dict) else {}
    except:
        pass
    
    # Essayer avec json (remplacer None par null)
    try:
        value_json = value_cleaned.replace('None', 'null').replace("'", '"')
        return json.loads(value_json)
    except:
        pass
    
    # Si c'est une string de dict Python, utiliser eval avec précaution
    try:
        # Remplacer None par null pour JSON
        value_eval = value_cleaned.replace('None', 'null')
        result = eval(value_eval, {"__builtins__": {}}, {})
        return result if isinstance(result, dict) else {}
    except:
        pass
    
    # Si c'est une string simple, essayer de parser comme CSV-like
    return {}


def get_value(data, *keys):
    """Récupère une valeur depuis un dict en essayant plusieurs clés"""
    if not isinstance(data, dict):
        return None
    for key in keys:
        if key in data:
            return data[key]
    return None


def format_cyrus_date(date_str):
    """
    Formate une date Cyrus du format YYMMDD (ex: 260107) vers DD/MM/YYYY (ex: 07/01/2026)
    Format Cyrus : YYMMDD où YY = année (2 chiffres), MM = mois, DD = jour
    Exemple : 260107 = 07/01/2026 (7 janvier 2026)
    """
    if not date_str or str(date_str).strip() in ['', 'nan', 'None', 'NaN']:
        return '-'
    
    date_str = str(date_str).strip()
    
    # Format YYMMDD (6 chiffres) - Format Cyrus
    if len(date_str) == 6 and date_str.isdigit():
        year_short = date_str[:2]  # YY
        month = date_str[2:4]      # MM
        day = date_str[4:6]         # DD
        try:
            year_int = int(year_short)
            # Si année < 50, assumer 20xx, sinon 20xx aussi (car on est en 2026+)
            if year_int < 50:
                year = f"20{year_short}"
            else:
                year = f"20{year_short}"  # Pour les années 50-99, on assume 2050-2099
            return f"{day}/{month}/{year}"
        except:
            return date_str
    
    # Format DDMMYY (6 chiffres) - Ancien format (pour compatibilité)
    if len(date_str) == 6 and date_str.isdigit():
        day = date_str[:2]
        month = date_str[2:4]
        year_short = date_str[4:6]
        try:
            year_int = int(year_short)
            if year_int < 50:
                year = f"20{year_short}"
            else:
                year = f"19{year_short}"
            return f"{day}/{month}/{year}"
        except:
            return date_str
    
    # Format DDMMYYYY (8 chiffres)
    if len(date_str) == 8 and date_str.isdigit():
        day = date_str[:2]
        month = date_str[2:4]
        year = date_str[4:8]
        return f"{day}/{month}/{year}"
    
    # Si déjà formaté, retourner tel quel
    return date_str


def dashboard(request):
    """Vue principale du dashboard"""
    # Charger les magasins
    magasins = load_magasins()
    
    # Récupérer les paramètres de filtrage
    magasin_filter = request.GET.get('magasin', '')
    periode_filter = request.GET.get('periode', '')
    type_ecart_filter = request.GET.get('type_ecart', '')
    date_debut = request.GET.get('date_debut', '')
    date_fin = request.GET.get('date_fin', '')
    controle_id = request.GET.get('controle', '')
    
    # Base queryset pour les écarts
    ecarts = Ecart.objects.select_related('controle').all()
    
    # Appliquer les filtres
    # Si un contrôle spécifique est sélectionné, filtrer par ce contrôle
    if controle_id:
        ecarts = ecarts.filter(controle_id=controle_id)
    
    # Appliquer le filtre de période (même si un contrôle est sélectionné, on peut encore filtrer)
    if periode_filter:
        ecarts = ecarts.filter(controle__periode=periode_filter)
    
    # Les filtres de date seront appliqués après sur les données extraites (date de commande, pas date de traitement)
    # On ne filtre plus par date_execution (date de traitement)
    
    # Filtrer par type d'écart
    if type_ecart_filter:
        ecarts = ecarts.filter(type_ecart=type_ecart_filter)
    
    # Les filtres par magasin et date de commande seront appliqués après extraction des données
    # On ne filtre plus ici pour permettre l'extraction complète des données
    
    # Trier par date de création (plus récent en premier)
    ecarts = ecarts.order_by('-date_creation')
    
    # Récupérer TOUS les écarts (sans pagination pour pouvoir filtrer sur les données)
    all_ecarts = list(ecarts)
    
    # Préparer les données pour l'affichage et appliquer les filtres sur les données de commande
    ecarts_data = []
    for ecart in all_ecarts:
        # Parser les données JSON de valeur_source_a
        data_asten = parse_data(ecart.valeur_source_a)
        data_cyrus = parse_data(ecart.valeur_source_b)
        
        # Extraire le numéro de magasin
        magasin_num = get_value(data_asten, 'Magasin', 'magasin', 'MAGASIN') or ''
        # Si c'est un nombre, le convertir en string
        if magasin_num:
            magasin_num = str(magasin_num).strip()
        if not magasin_num and isinstance(ecart.valeur_source_a, str):
            # Essayer d'extraire depuis une string avec plusieurs patterns
            import re
            patterns = [
                r"'Magasin':\s*(\d+)",
                r'"Magasin":\s*(\d+)',
                r'"Magasin":\s*"(\d+)"',
                r"'Magasin':\s*'(\d+)'",
                r'Magasin["\']?\s*[:;]\s*["\']?(\d+)',
            ]
            for pattern in patterns:
                match = re.search(pattern, ecart.valeur_source_a)
                if match:
                    magasin_num = str(match.group(1)).strip()
                    break
        
        # Obtenir le nom du magasin
        magasin_nom = magasins.get(str(magasin_num), {}).get('name', f'Magasin {magasin_num}' if magasin_num else 'N/A')
        
        # Appliquer le filtre par magasin sur les données extraites
        if magasin_filter and magasin_num != magasin_filter:
            continue
        
        # Extraire toutes les colonnes importantes
        # Essayer plusieurs variantes de noms de colonnes
        colonnes = {
            'reference': get_value(data_asten, 'Référence commande', 'Référence commande', 'Référence', 'reference', 'REFERENCE', 'NCDE'),
            'date_commande': get_value(data_asten, 'Date commande', 'Date commande', 'Date', 'date_commande', 'DCDE', 'Date de commande'),
            'date_livraison': get_value(data_asten, 'Date livraison', 'Date livraison', 'date_livraison', 'Date de livraison'),
            'date_validation': get_value(data_asten, 'Date validation', 'Date validation', 'date_validation', 'Date de validation'),
            'statut': get_value(data_asten, 'Statut', 'statut', 'STATUT', 'Status'),
            'cree_par': get_value(data_asten, 'Créée par', 'Créée par', 'cree_par', 'Crée par', 'Créé par', 'Créé par'),
            'validee_par': get_value(data_asten, 'Validée par', 'Validée par', 'validee_par', 'Validée par', 'Validé par'),
            'fournisseur': get_value(data_asten, 'Fournisseur', 'fournisseur', 'FOURNISSEUR', 'Fournisseur'),
            'type_commande': get_value(data_asten, 'Type commande', 'Type commande', 'type_commande', 'Type', 'Type de commande'),
        }
        
        # Appliquer le filtre par date de commande (pas date de traitement)
        if date_debut or date_fin:
            date_commande_str = colonnes.get('date_commande', '')
            if date_commande_str:
                # Essayer de parser la date de commande
                date_commande_obj = None
                try:
                    # Essayer différents formats
                    for fmt in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                        try:
                            date_commande_obj = datetime.strptime(date_commande_str, fmt).date()
                            break
                        except ValueError:
                            continue
                    
                    # Si format DDMMYY (6 chiffres)
                    if not date_commande_obj and len(date_commande_str) == 6 and date_commande_str.isdigit():
                        day = date_commande_str[:2]
                        month = date_commande_str[2:4]
                        year_short = date_commande_str[4:6]
                        year_int = int(year_short)
                        if year_int < 50:
                            year = f"20{year_short}"
                        else:
                            year = f"19{year_short}"
                        date_commande_str = f"{day}/{month}/{year}"
                        try:
                            date_commande_obj = datetime.strptime(date_commande_str, '%d/%m/%Y').date()
                        except:
                            pass
                    
                    if date_commande_obj:
                        # Appliquer les filtres de date
                        if date_debut:
                            try:
                                date_debut_obj = datetime.strptime(date_debut, '%Y-%m-%d').date()
                                if date_commande_obj < date_debut_obj:
                                    continue
                            except:
                                pass
                        
                        if date_fin:
                            try:
                                date_fin_obj = datetime.strptime(date_fin, '%Y-%m-%d').date()
                                if date_commande_obj > date_fin_obj:
                                    continue
                            except:
                                pass
                except:
                    pass
        
        # Préparer les détails pour le JSON (convertir en string JSON sécurisée)
        import json as json_lib
        details_json = {}
        if ecart.details:
            try:
                if isinstance(ecart.details, dict):
                    details_json = ecart.details
                else:
                    details_json = parse_data(ecart.details)
            except:
                details_json = {}
        
        # Préparer les valeurs sources pour le JSON
        valeur_source_a_json = None
        valeur_source_b_json = None
        try:
            if ecart.valeur_source_a:
                valeur_source_a_json = parse_data(ecart.valeur_source_a)
            if ecart.valeur_source_b:
                valeur_source_b_json = parse_data(ecart.valeur_source_b)
        except:
            pass
        
        # Vérifier si la commande existe dans Cyrus (même si avec différences)
        trouve_dans_cyrus = ecart.type_ecart != 'absent_b' and ecart.valeur_source_b is not None
        
        # Extraire les colonnes de Cyrus aussi
        colonnes_cyrus = {}
        if data_cyrus:
            colonnes_cyrus = {
                'reference': get_value(data_cyrus, 'NCDE', 'Référence', 'reference', 'REFERENCE'),
                'date_commande': get_value(data_cyrus, 'DCDE', 'Date', 'date_commande', 'DCDE'),
                'date_livraison': get_value(data_cyrus, 'Date livraison', 'date_livraison'),
                'date_validation': get_value(data_cyrus, 'Date validation', 'date_validation'),
                'statut': get_value(data_cyrus, 'Statut', 'statut', 'STATUT'),
                'fournisseur': get_value(data_cyrus, 'Fournisseur', 'fournisseur', 'FOURNISSEUR'),
                'type_commande': get_value(data_cyrus, 'Type commande', 'type_commande', 'Type'),
            }
        
        ecarts_data.append({
            'ecart': ecart,
            'data_asten': data_asten,
            'data_cyrus': data_cyrus,
            'magasin_num': magasin_num,
            'magasin_nom': magasin_nom,
            'colonnes': colonnes,
            'colonnes_cyrus': colonnes_cyrus,
            'details_json': details_json,
            'valeur_source_a_json': valeur_source_a_json,
            'valeur_source_b_json': valeur_source_b_json,
            'trouve_dans_cyrus': trouve_dans_cyrus,  # Indique si trouvé dans Cyrus
        })
    
    # Pagination après filtrage
    paginator = Paginator(ecarts_data, 25)
    page = request.GET.get('page', 1)
    try:
        ecarts_page = paginator.page(page)
    except PageNotAnInteger:
        ecarts_page = paginator.page(1)
    except EmptyPage:
        ecarts_page = paginator.page(paginator.num_pages)
    
    # Mettre à jour ecarts_data avec la page paginée
    ecarts_data = list(ecarts_page.object_list)
    
    # Statistiques globales (sur les données filtrées)
    total_ecarts = paginator.count
    ecarts_absent_b = sum(1 for e in ecarts_data if e['ecart'].type_ecart == 'absent_b')
    ecarts_valeur_differente = sum(1 for e in ecarts_data if e['ecart'].type_ecart == 'valeur_differente')
    ecarts_corriges = sum(1 for e in ecarts_data if e['ecart'].type_ecart == 'corrige')
    # Note: Pour les commandes, on ne cherche pas les écarts de type 'absent_a' 
    # car toutes les commandes Asten doivent être dans Cyrus, mais pas l'inverse
    ecarts_absent_a = 0
    
    # Calculer le nombre total de commandes dans Asten et Cyrus
    # On compte les lignes uniques à partir des écarts et des contrôles
    
    # Récupérer les contrôles concernés
    # Si on a des écarts filtrés, utiliser leurs contrôles
    # Sinon, utiliser tous les contrôles de type 'commande' pour les statistiques
    if ecarts_data:
        controles_ids = list(set([e['ecart'].controle_id for e in ecarts_data]))
        controles_concernes = Controle.objects.filter(id__in=controles_ids) if controles_ids else Controle.objects.none()
    else:
        # Si pas d'écarts, charger tous les contrôles récents pour les statistiques
        controles_concernes = Controle.objects.filter(type_controle='commande').order_by('-date_execution')
    
    # Compter les lignes uniques dans Asten depuis les fichiers
    lignes_asten_uniques = set()
    
    # Charger tous les fichiers Asten des contrôles pour compter toutes les lignes
    for controle_obj in controles_concernes:
        fichiers_asten = FichierSource.objects.filter(controle=controle_obj, origine='asten')
        for fichier_asten in fichiers_asten:
            # Vérifier si le fichier existe, sinon chercher dans l'archive
            chemin_fichier = fichier_asten.chemin
            if not os.path.exists(chemin_fichier):
                # Chercher dans l'archive
                nom_fichier = os.path.basename(chemin_fichier)
                archive_path = os.path.join(settings.EXTRACTIONS_PATHS.get('archive', ''), '*', nom_fichier)
                fichiers_archive = glob.glob(archive_path)
                if fichiers_archive:
                    chemin_fichier = fichiers_archive[0]
                else:
                    continue
            try:
                df_asten = pd.read_csv(chemin_fichier, sep=';', encoding='utf-8', low_memory=False)
                if len(df_asten.columns) == 1:
                    df_asten = pd.read_csv(chemin_fichier, sep=',', encoding='utf-8', low_memory=False)
                
                # Compter les lignes uniques avec Référence commande et Date commande
                if 'Référence commande' in df_asten.columns and 'Date commande' in df_asten.columns:
                    df_asten['Référence commande'] = df_asten['Référence commande'].astype(str).str.strip()
                    df_asten['Date commande'] = df_asten['Date commande'].astype(str).str.strip()
                    df_asten = df_asten[
                        ~df_asten['Référence commande'].isin(['', 'nan', 'None', 'NaN']) &
                        ~df_asten['Date commande'].isin(['', 'nan', 'None', 'NaN'])
                    ]
                    for idx, row in df_asten.iterrows():
                        ref = str(row['Référence commande']).strip()
                        date = str(row['Date commande']).strip()
                        if ref and date:
                            lignes_asten_uniques.add(f"{ref}|{date}")
            except Exception as e:
                logger.warning(f'Erreur lors du chargement du fichier Asten {fichier_asten.chemin}: {str(e)}')
                continue
    
    # Si aucun fichier trouvé, compter depuis les écarts
    if len(lignes_asten_uniques) == 0:
        for ecart in ecarts.filter(valeur_source_a__isnull=False):
            data_asten = parse_data(ecart.valeur_source_a)
            ref = get_value(data_asten, 'Référence commande', 'Référence', 'reference', 'REFERENCE')
            date = get_value(data_asten, 'Date commande', 'Date', 'date_commande', 'DCDE')
            if ref and date:
                lignes_asten_uniques.add(f"{ref}|{date}")
    
    total_asten = len(lignes_asten_uniques)
    
    # Pour Cyrus, compter toutes les lignes depuis les FichierSource
    # Récupérer tous les fichiers Cyrus des contrôles concernés
    lignes_cyrus_uniques = set()
    
    # Charger tous les fichiers Cyrus des contrôles pour compter toutes les lignes
    for controle_obj in controles_concernes:
        fichiers_cyrus = FichierSource.objects.filter(controle=controle_obj, origine='cyrus')
        for fichier_cyrus in fichiers_cyrus:
            # Vérifier si le fichier existe, sinon chercher dans l'archive
            chemin_fichier = fichier_cyrus.chemin
            if not os.path.exists(chemin_fichier):
                # Chercher dans l'archive
                nom_fichier = os.path.basename(chemin_fichier)
                archive_path = os.path.join(settings.EXTRACTIONS_PATHS.get('archive', ''), '*', nom_fichier)
                fichiers_archive = glob.glob(archive_path)
                if fichiers_archive:
                    chemin_fichier = fichiers_archive[0]
                else:
                    continue
            try:
                df_cyrus = pd.read_csv(chemin_fichier, sep=';', encoding='utf-8', low_memory=False)
                if len(df_cyrus.columns) == 1:
                    df_cyrus = pd.read_csv(chemin_fichier, sep=',', encoding='utf-8', low_memory=False)
                
                # Compter les lignes uniques avec NCDE et DCDE
                if 'NCDE' in df_cyrus.columns and 'DCDE' in df_cyrus.columns:
                    df_cyrus['NCDE'] = df_cyrus['NCDE'].astype(str).str.strip()
                    df_cyrus['DCDE'] = df_cyrus['DCDE'].astype(str).str.strip()
                    df_cyrus = df_cyrus[
                        ~df_cyrus['NCDE'].isin(['', 'nan', 'None', 'NaN']) &
                        ~df_cyrus['DCDE'].isin(['', 'nan', 'None', 'NaN'])
                    ]
                    for idx, row in df_cyrus.iterrows():
                        ref = str(row['NCDE']).strip()
                        date = str(row['DCDE']).strip()
                        if ref and date:
                            # Normaliser la date Cyrus (format YYMMDD -> DDMMYYYY)
                            if len(date) == 6 and date.isdigit():
                                # Format Cyrus : YYMMDD
                                year_short = date[:2]  # YY
                                month = date[2:4]      # MM
                                day = date[4:6]        # DD
                                try:
                                    year = f"20{year_short}"
                                    date_normalized = f"{day}{month}{year}"
                                    lignes_cyrus_uniques.add(f"{ref}|{date_normalized}")
                                except:
                                    lignes_cyrus_uniques.add(f"{ref}|{date}")
                            else:
                                lignes_cyrus_uniques.add(f"{ref}|{date}")
            except Exception as e:
                logger.warning(f'Erreur lors du chargement du fichier Cyrus {chemin_fichier}: {str(e)}')
                continue
    
    total_cyrus = len(lignes_cyrus_uniques)
    
    # Si aucun fichier trouvé dans controles_concernes, essayer tous les contrôles récents
    if total_cyrus == 0 and not controles_concernes.exists():
        # Charger depuis tous les contrôles récents
        all_controles = Controle.objects.filter(type_controle='commande').order_by('-date_execution')[:5]
        for controle_obj in all_controles:
            fichiers_cyrus = FichierSource.objects.filter(controle=controle_obj, origine='cyrus')
            for fichier_cyrus in fichiers_cyrus:
                # Vérifier si le fichier existe, sinon chercher dans l'archive
                chemin_fichier = fichier_cyrus.chemin
                if not os.path.exists(chemin_fichier):
                    # Chercher dans l'archive
                    nom_fichier = os.path.basename(chemin_fichier)
                    archive_path = os.path.join(settings.EXTRACTIONS_PATHS.get('archive', ''), '*', nom_fichier)
                    fichiers_archive = glob.glob(archive_path)
                    if fichiers_archive:
                        chemin_fichier = fichiers_archive[0]
                    else:
                        continue
                try:
                    df_cyrus = pd.read_csv(chemin_fichier, sep=';', encoding='utf-8', low_memory=False)
                    if len(df_cyrus.columns) == 1:
                        df_cyrus = pd.read_csv(chemin_fichier, sep=',', encoding='utf-8', low_memory=False)
                    
                    if 'NCDE' in df_cyrus.columns and 'DCDE' in df_cyrus.columns:
                        df_cyrus['NCDE'] = df_cyrus['NCDE'].astype(str).str.strip()
                        df_cyrus['DCDE'] = df_cyrus['DCDE'].astype(str).str.strip()
                        df_cyrus = df_cyrus[
                            ~df_cyrus['NCDE'].isin(['', 'nan', 'None', 'NaN']) &
                            ~df_cyrus['DCDE'].isin(['', 'nan', 'None', 'NaN'])
                        ]
                        for idx, row in df_cyrus.iterrows():
                            ref = str(row['NCDE']).strip()
                            date = str(row['DCDE']).strip()
                            if ref and date:
                                # Normaliser la date Cyrus (format YYMMDD -> DDMMYYYY)
                                if len(date) == 6 and date.isdigit():
                                    year_short = date[:2]
                                    month = date[2:4]
                                    day = date[4:6]
                                    try:
                                        year = f"20{year_short}"
                                        date_normalized = f"{day}{month}{year}"
                                        lignes_cyrus_uniques.add(f"{ref}|{date_normalized}")
                                    except:
                                        lignes_cyrus_uniques.add(f"{ref}|{date}")
                                else:
                                    lignes_cyrus_uniques.add(f"{ref}|{date}")
                except Exception as e:
                    logger.warning(f'Erreur lors du chargement du fichier Cyrus {fichier_cyrus.chemin}: {str(e)}')
                    continue
        total_cyrus = len(lignes_cyrus_uniques)
    
    # Si aucun fichier trouvé, essayer de compter depuis les écarts
    if total_cyrus == 0:
        for ecart in ecarts.filter(valeur_source_b__isnull=False):
            data_cyrus = parse_data(ecart.valeur_source_b)
            ref = get_value(data_cyrus, 'NCDE', 'Référence', 'reference', 'REFERENCE')
            date = get_value(data_cyrus, 'DCDE', 'Date', 'date_commande', 'DCDE')
            if ref and date:
                lignes_cyrus_uniques.add(f"{ref}|{date}")
        total_cyrus = len(lignes_cyrus_uniques)
    
    # Si on a un contrôle spécifique, utiliser ses données pour améliorer l'estimation
    if controle_id:
        controle_selected = Controle.objects.filter(id=controle_id).first()
        if controle_selected:
            # Le contrôle a déjà total_lignes qui est la somme
            # On peut estimer en utilisant les proportions des écarts
            if controle_selected.total_lignes > 0:
                # Estimation basée sur les proportions
                ecarts_du_controle = ecarts.filter(controle=controle_selected)
                if ecarts_du_controle.exists():
                    # Asten = lignes avec valeur_source_a dans ce contrôle
                    asten_controle = set()
                    for ecart in ecarts_du_controle.filter(valeur_source_a__isnull=False):
                        data_asten = parse_data(ecart.valeur_source_a)
                        ref = get_value(data_asten, 'Référence commande', 'Référence', 'reference', 'REFERENCE')
                        date = get_value(data_asten, 'Date commande', 'Date', 'date_commande', 'DCDE')
                        if ref and date:
                            asten_controle.add(f"{ref}|{date}")
                    
                    # Cyrus = lignes avec valeur_source_b + lignes trouvées (valeur_differente)
                    cyrus_controle = set()
                    for ecart in ecarts_du_controle.filter(valeur_source_b__isnull=False):
                        data_cyrus = parse_data(ecart.valeur_source_b)
                        ref = get_value(data_cyrus, 'NCDE', 'Référence', 'reference', 'REFERENCE')
                        date = get_value(data_cyrus, 'DCDE', 'Date', 'date_commande', 'DCDE')
                        if ref and date:
                            cyrus_controle.add(f"{ref}|{date}")
                    
                    for ecart in ecarts_du_controle.filter(type_ecart='valeur_differente'):
                        if ecart.valeur_source_b:
                            data_cyrus = parse_data(ecart.valeur_source_b)
                            ref = get_value(data_cyrus, 'NCDE', 'Référence', 'reference', 'REFERENCE')
                            date = get_value(data_cyrus, 'DCDE', 'Date', 'date_commande', 'DCDE')
                            if ref and date:
                                cyrus_controle.add(f"{ref}|{date}")
                    
                    if len(asten_controle) > 0:
                        total_asten = len(asten_controle)
                    if len(cyrus_controle) > 0:
                        total_cyrus = len(cyrus_controle)
    
    # Écarts = éléments dans Asten qui ne sont pas dans Cyrus
    # C'est exactement les écarts de type 'absent_b' (présent dans Asten mais absent dans Cyrus)
    ecarts_asten_cyrus = ecarts_absent_b
    
    # Liste des contrôles récents pour le filtre - grouper par période pour éviter les doublons
    # Prendre le contrôle le plus récent pour chaque période
    controles_par_periode = {}
    all_controles = Controle.objects.filter(type_controle='commande').order_by('-date_execution')
    for controle in all_controles:
        if controle.periode not in controles_par_periode:
            controles_par_periode[controle.periode] = controle
    
    # Convertir en liste triée par date décroissante
    controles = sorted(controles_par_periode.values(), key=lambda x: x.date_execution, reverse=True)[:20]
    
    # Liste des périodes disponibles
    periodes = Controle.objects.filter(type_controle='commande').values_list('periode', flat=True).distinct().order_by('-periode')
    
    # Statistiques par magasin
    stats_magasins = {}
    for ecart_data in ecarts_data:
        mag_num = ecart_data['magasin_num']
        if mag_num:
            if mag_num not in stats_magasins:
                stats_magasins[mag_num] = {
                    'nom': ecart_data['magasin_nom'],
                    'total': 0,
                    'absent_b': 0,
                    'valeur_differente': 0,
                }
            stats_magasins[mag_num]['total'] += 1
            if ecart_data['ecart'].type_ecart == 'absent_b':
                stats_magasins[mag_num]['absent_b'] += 1
            elif ecart_data['ecart'].type_ecart == 'valeur_differente':
                stats_magasins[mag_num]['valeur_differente'] += 1
    
    # Convertir les données JSON pour le template (sérialiser en JSON string)
    for item in ecarts_data:
               try:
                   item['details_json_str'] = json.dumps(item.get('details_json', {}), ensure_ascii=False, cls=DjangoJSONEncoder)
                   item['valeur_source_a_json_str'] = json.dumps(item.get('valeur_source_a_json'), ensure_ascii=False, cls=DjangoJSONEncoder) if item.get('valeur_source_a_json') else 'null'
                   item['valeur_source_b_json_str'] = json.dumps(item.get('valeur_source_b_json'), ensure_ascii=False, cls=DjangoJSONEncoder) if item.get('valeur_source_b_json') else 'null'
                   # Sérialiser row_data pour Asten et Cyrus
                   if 'row_data' in item.get('data_asten', {}):
                       item['data_asten']['row_data_str'] = json.dumps(item['data_asten']['row_data'], ensure_ascii=False, cls=DjangoJSONEncoder, default=str)
               except Exception as e:
                   item['details_json_str'] = '{}'
                   item['valeur_source_a_json_str'] = 'null'
                   item['valeur_source_b_json_str'] = 'null'
    
    # Préparer les données pour les onglets Asten et Cyrus
    # Charger toutes les commandes Asten et Cyrus depuis les fichiers
    commandes_asten_data = []
    commandes_cyrus_data = []
    
    # Récupérer le contrôle sélectionné ou le plus récent avec des fichiers
    # Priorité : contrôle_id > période > contrôle le plus récent avec fichiers
    controle_selected = None
    if controle_id:
        controle_selected = Controle.objects.filter(id=controle_id).first()
    elif periode_filter:
        # Si une période est sélectionnée, prendre le contrôle le plus récent de cette période
        controles_periode = Controle.objects.filter(
            type_controle='commande',
            periode=periode_filter
        ).order_by('-date_execution')
        for controle in controles_periode:
            if FichierSource.objects.filter(controle=controle).exists():
                controle_selected = controle
                break
        if not controle_selected:
            controle_selected = controles_periode.first()
    elif controles_concernes.exists():
        # Chercher dans controles_concernes celui qui a des fichiers
        for controle in controles_concernes:
            if FichierSource.objects.filter(controle=controle).exists():
                controle_selected = controle
                break
        # Si aucun contrôle avec fichiers dans controles_concernes, chercher ailleurs
        if not controle_selected:
            all_controles = Controle.objects.filter(type_controle='commande').order_by('-date_execution')
            for controle in all_controles:
                if FichierSource.objects.filter(controle=controle).exists():
                    controle_selected = controle
                    break
    else:
        # Si pas de contrôles concernés, prendre le plus récent qui a des fichiers
        # Chercher parmi tous les contrôles celui qui a des fichiers
        all_controles = Controle.objects.filter(type_controle='commande').order_by('-date_execution')
        for controle in all_controles:
            if FichierSource.objects.filter(controle=controle).exists():
                controle_selected = controle
                break
        # Si aucun contrôle avec fichiers, prendre le plus récent quand même
        if not controle_selected:
            controle_selected = all_controles.first()
    
    if controle_selected:
        # Charger les commandes Asten
        fichiers_asten = FichierSource.objects.filter(controle=controle_selected, origine='asten')
        for fichier_asten in fichiers_asten:
            # Vérifier si le fichier existe, sinon chercher dans l'archive
            chemin_fichier = fichier_asten.chemin
            if not os.path.exists(chemin_fichier):
                # Chercher dans l'archive
                nom_fichier = os.path.basename(chemin_fichier)
                archive_path = os.path.join(settings.EXTRACTIONS_PATHS.get('archive', ''), '*', nom_fichier)
                fichiers_archive = glob.glob(archive_path)
                if fichiers_archive:
                    chemin_fichier = fichiers_archive[0]
                else:
                    continue
            try:
                df_asten = pd.read_csv(chemin_fichier, sep=';', encoding='utf-8', low_memory=False)
                if len(df_asten.columns) == 1:
                    df_asten = pd.read_csv(chemin_fichier, sep=',', encoding='utf-8', low_memory=False)
                
                for idx, row in df_asten.iterrows():
                    magasin_num = str(row.get('Magasin', '')).strip() if 'Magasin' in row else ''
                    magasin_nom = magasins.get(str(magasin_num), {}).get('name', f'Magasin {magasin_num}' if magasin_num else 'N/A')
                    
                    commandes_asten_data.append({
                        'reference': str(row.get('Référence commande', '')).strip() if 'Référence commande' in row else '',
                        'date_commande': str(row.get('Date commande', '')).strip() if 'Date commande' in row else '',
                        'date_livraison': str(row.get('Date livraison', '')).strip() if 'Date livraison' in row else '',
                        'date_validation': str(row.get('Date validation', '')).strip() if 'Date validation' in row else '',
                        'statut': str(row.get('Statut', '')).strip() if 'Statut' in row else '',
                        'cree_par': str(row.get('Créée par', '')).strip() if 'Créée par' in row else '',
                        'validee_par': str(row.get('Validée par', '')).strip() if 'Validée par' in row else '',
                        'fournisseur': str(row.get('Fournisseur', '')).strip() if 'Fournisseur' in row else '',
                        'type_commande': str(row.get('Type commande', '')).strip() if 'Type commande' in row else '',
                        'magasin_num': magasin_num,
                        'magasin_nom': magasin_nom,
                        'row_data': row.to_dict(),
                    })
            except Exception as e:
                logger.warning(f'Erreur lors du chargement du fichier Asten {fichier_asten.chemin}: {str(e)}')
                continue
        
        # Charger les commandes Cyrus
        fichiers_cyrus = FichierSource.objects.filter(controle=controle_selected, origine='cyrus')
        for fichier_cyrus in fichiers_cyrus:
            # Vérifier si le fichier existe, sinon chercher dans l'archive
            chemin_fichier = fichier_cyrus.chemin
            if not os.path.exists(chemin_fichier):
                # Chercher dans l'archive
                nom_fichier = os.path.basename(chemin_fichier)
                archive_path = os.path.join(settings.EXTRACTIONS_PATHS.get('archive', ''), '*', nom_fichier)
                fichiers_archive = glob.glob(archive_path)
                if fichiers_archive:
                    chemin_fichier = fichiers_archive[0]
                else:
                    continue
            try:
                df_cyrus = pd.read_csv(chemin_fichier, sep=';', encoding='utf-8', low_memory=False)
                if len(df_cyrus.columns) == 1:
                    df_cyrus = pd.read_csv(chemin_fichier, sep=',', encoding='utf-8', low_memory=False)
                
                for idx, row in df_cyrus.iterrows():
                    magasin_num = str(row.get('NCID', '')).strip() if 'NCID' in row else ''
                    if not magasin_num:
                        # Essayer d'extraire depuis NOMMAGASIN ou autres colonnes (peut avoir des caractères spéciaux)
                        for col in df_cyrus.columns:
                            if 'NOMMAGASIN' in col or 'MAGASIN' in col:
                                magasin_val = str(row.get(col, '')).strip()
                                # Extraire les 3 premiers chiffres si c'est un numéro de magasin
                                if magasin_val:
                                    # Chercher les chiffres dans la valeur
                                    digits = re.findall(r'\d+', magasin_val)
                                    if digits:
                                        magasin_num = digits[0][:3] if len(digits[0]) >= 3 else digits[0]
                                break
                    magasin_nom = magasins.get(str(magasin_num), {}).get('name', f'Magasin {magasin_num}' if magasin_num else 'N/A')
                    
                    date_commande_raw = str(row.get('DCDE', '')).strip() if 'DCDE' in row else ''
                    date_commande_formatted = format_cyrus_date(date_commande_raw)
                    
                    commandes_cyrus_data.append({
                        'reference': str(row.get('NCDE', '')).strip() if 'NCDE' in row else '',
                        'date_commande': date_commande_formatted,
                        'date_commande_raw': date_commande_raw,  # Garder la version brute pour le tri
                        'magasin_num': magasin_num,
                        'magasin_nom': magasin_nom,
                        'row_data': row.to_dict(),
                    })
            except Exception as e:
                logger.warning(f'Erreur lors du chargement du fichier Cyrus {chemin_fichier}: {str(e)}')
                continue
    
    # Identifier les commandes communes (présentes dans Asten ET Cyrus)
    commandes_communes_data = []
    total_communes = 0
    
    if controle_selected and commandes_asten_data and commandes_cyrus_data:
        # Fonction pour normaliser les dates
        def normalize_date_for_key(date_str, is_cyrus=False):
            """
            Normalise une date pour créer une clé de comparaison.
            Pour Cyrus : format YYMMDD (ex: 260107 = 07/01/2026)
            Pour Asten : format DD/MM/YYYY ou DDMMYY
            """
            if not date_str or str(date_str).strip() in ['', 'nan', 'None', 'NaN']:
                return ''
            date_str = str(date_str).strip()
            
            # Format YYMMDD (6 chiffres) - Format Cyrus
            if is_cyrus and len(date_str) == 6 and date_str.isdigit():
                year_short = date_str[:2]  # YY
                month = date_str[2:4]      # MM
                day = date_str[4:6]        # DD
                try:
                    year = f"20{year_short}"
                    return f"{day}{month}{year}"  # DDMMYYYY pour comparaison
                except:
                    pass
            
            # Format DDMMYY (6 chiffres) - Format Asten
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
                    pass
            
            # Essayer de parser les formats courants
            from datetime import datetime
            for fmt in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                try:
                    return datetime.strptime(date_str, fmt).strftime('%d%m%Y')
                except ValueError:
                    pass
            
            return date_str
        
        # Créer un dictionnaire des commandes Cyrus avec clé composite
        cyrus_dict = {}
        for cmd_cyrus in commandes_cyrus_data:
            ref = cmd_cyrus.get('reference', '').strip()
            date = normalize_date_for_key(cmd_cyrus.get('date_commande', ''))
            if ref and date:
                key = f"{ref}|{date}"
                if key not in cyrus_dict:
                    cyrus_dict[key] = []
                cyrus_dict[key].append(cmd_cyrus)
        
        # Parcourir les commandes Asten et vérifier si elles existent dans Cyrus
        for cmd_asten in commandes_asten_data:
            ref_asten = cmd_asten.get('reference', '').strip()
            date_asten = normalize_date_for_key(cmd_asten.get('date_commande', ''))
            trouve_dans_cyrus = False
            cmd_cyrus_match = None
            
            if ref_asten and date_asten:
                key = f"{ref_asten}|{date_asten}"
                if key in cyrus_dict:
                    trouve_dans_cyrus = True
                    cmd_cyrus_match = cyrus_dict[key][0]  # Prendre le premier match
                    # Commande trouvée dans les deux sources
                    commandes_communes_data.append({
                        'reference': ref_asten,
                        'date_commande': cmd_asten.get('date_commande', ''),
                        'magasin_num': cmd_asten.get('magasin_num', ''),
                        'magasin_nom': cmd_asten.get('magasin_nom', ''),
                        'data_asten': cmd_asten,
                        'data_cyrus': cmd_cyrus_match,
                        'date_livraison_asten': cmd_asten.get('date_livraison', ''),
                        'date_livraison_cyrus': cmd_cyrus_match.get('date_livraison', ''),
                        'date_validation_asten': cmd_asten.get('date_validation', ''),
                        'date_validation_cyrus': cmd_cyrus_match.get('date_validation', ''),
                        'statut_asten': cmd_asten.get('statut', ''),
                        'statut_cyrus': cmd_cyrus_match.get('statut', ''),
                        'fournisseur_asten': cmd_asten.get('fournisseur', ''),
                        'fournisseur_cyrus': cmd_cyrus_match.get('fournisseur', ''),
                        'type_commande_asten': cmd_asten.get('type_commande', ''),
                        'type_commande_cyrus': cmd_cyrus_match.get('type_commande', ''),
                    })
            
            # Ajouter l'information de correspondance à la commande Asten
            cmd_asten['trouve_dans_cyrus'] = trouve_dans_cyrus
            cmd_asten['data_cyrus_match'] = cmd_cyrus_match
        
        # Parcourir les commandes Cyrus et vérifier si elles existent dans Asten
        asten_dict = {}
        for cmd_asten in commandes_asten_data:
            ref_asten = cmd_asten.get('reference', '').strip()
            date_asten = normalize_date_for_key(cmd_asten.get('date_commande', ''))
            if ref_asten and date_asten:
                key = f"{ref_asten}|{date_asten}"
                if key not in asten_dict:
                    asten_dict[key] = []
                asten_dict[key].append(cmd_asten)
        
        for cmd_cyrus in commandes_cyrus_data:
            ref_cyrus = cmd_cyrus.get('reference', '').strip()
            date_cyrus = normalize_date_for_key(cmd_cyrus.get('date_commande', ''), is_cyrus=True)
            trouve_dans_asten = False
            cmd_asten_match = None
            
            if ref_cyrus and date_cyrus:
                key = f"{ref_cyrus}|{date_cyrus}"
                if key in asten_dict:
                    trouve_dans_asten = True
                    cmd_asten_match = asten_dict[key][0]
            
            # Ajouter l'information de correspondance à la commande Cyrus
            cmd_cyrus['trouve_dans_asten'] = trouve_dans_asten
            cmd_cyrus['data_asten_match'] = cmd_asten_match
        
        total_communes = len(commandes_communes_data)
    
    # Vérifier si on doit afficher la vue "commandes communes"
    vue_communes = request.GET.get('vue', '') == 'communes'
    
    context = {
        'ecarts_data': ecarts_data,
        'ecarts_page': ecarts_page,  # Objet pagination
        'commandes_asten_data': commandes_asten_data,  # Afficher toutes les données
        'commandes_cyrus_data': commandes_cyrus_data,  # Afficher toutes les données
        'commandes_communes_data': commandes_communes_data,  # Afficher toutes les données
        'total_communes': total_communes,
        'vue_communes': vue_communes,
        'magasins': magasins,
        'total_ecarts': total_ecarts,
        'total_asten': total_asten,
        'total_cyrus': total_cyrus,
        'ecarts_asten_cyrus': ecarts_asten_cyrus,  # Éléments dans Asten mais pas dans Cyrus
        'ecarts_absent_b': ecarts_absent_b,  # Commandes Asten absentes de Cyrus
        'ecarts_valeur_differente': ecarts_valeur_differente,  # Commandes avec valeurs différentes
        'ecarts_corriges': ecarts_corriges,  # Écarts corrigés (étaient absents, maintenant présents)
        'ecarts_absent_a': ecarts_absent_a,  # Toujours 0 pour les commandes (non utilisé)
        'controles': controles,
        'periodes': periodes,
        'stats_magasins': sorted(stats_magasins.items(), key=lambda x: x[1]['total'], reverse=True),
        'filters': {
            'magasin': magasin_filter,
            'periode': periode_filter,
            'type_ecart': type_ecart_filter,
            'date_debut': date_debut,
            'date_fin': date_fin,
            'controle': controle_id,
        }
    }
    
    return render(request, 'dashboard/dashboard.html', context)


@require_http_methods(["POST"])
def traiter_fichiers(request):
    """Traite les fichiers déposés dans les dossiers"""
    periode = request.POST.get('periode', timezone.now().strftime('%Y-%m'))
    force = request.POST.get('force', 'false') == 'true'
    
    try:
        # Appeler la commande de traitement directement
        from io import StringIO
        import sys
        from django.core.management import call_command
        
        # Capturer la sortie
        output = StringIO()
        old_stdout = sys.stdout
        sys.stdout = output
        
        try:
            call_command('process_commande', periode=periode, force=force)
            output_str = output.getvalue()
        finally:
            sys.stdout = old_stdout
        
        # Compter les nouveaux écarts créés
        nouveaux_ecarts = Ecart.objects.filter(
            controle__periode=periode,
            controle__date_execution__gte=timezone.now() - timedelta(minutes=5)
        ).count()
        
        return JsonResponse({
            'success': True,
            'message': f'Traitement terminé avec succès ! {nouveaux_ecarts} écarts détectés pour la période {periode}',
            'periode': periode,
            'ecarts': nouveaux_ecarts
        })
    except Exception as e:
        import traceback
        error_msg = str(e)
        return JsonResponse({
            'success': False,
            'message': f'Erreur lors du traitement: {error_msg}'
        }, status=500)


def commandes_communes(request):
    """Vue dédiée pour afficher les commandes communes"""
    # Charger les magasins
    magasins = load_magasins()
    
    # Récupérer les paramètres de filtrage
    controle_id = request.GET.get('controle', '')
    
    # Récupérer le contrôle sélectionné ou le plus récent
    controle_selected = None
    if controle_id:
        controle_selected = Controle.objects.filter(id=controle_id, type_controle='commande').first()
    else:
        controle_selected = Controle.objects.filter(type_controle='commande').order_by('-date_execution').first()
    
    commandes_communes_data = []
    total_communes = 0
    
    if controle_selected:
        # Charger les commandes Asten
        commandes_asten_data = []
        fichiers_asten = FichierSource.objects.filter(controle=controle_selected, origine='asten')
        for fichier_asten in fichiers_asten:
            # Vérifier si le fichier existe, sinon chercher dans l'archive
            chemin_fichier = fichier_asten.chemin
            if not os.path.exists(chemin_fichier):
                # Chercher dans l'archive
                nom_fichier = os.path.basename(chemin_fichier)
                archive_path = os.path.join(settings.EXTRACTIONS_PATHS.get('archive', ''), '*', nom_fichier)
                fichiers_archive = glob.glob(archive_path)
                if fichiers_archive:
                    chemin_fichier = fichiers_archive[0]
                else:
                    continue
            try:
                df_asten = pd.read_csv(chemin_fichier, sep=';', encoding='utf-8', low_memory=False)
                if len(df_asten.columns) == 1:
                    df_asten = pd.read_csv(chemin_fichier, sep=',', encoding='utf-8', low_memory=False)
                
                for idx, row in df_asten.iterrows():
                    magasin_num = str(row.get('Magasin', '')).strip() if 'Magasin' in row else ''
                    magasin_nom = magasins.get(str(magasin_num), {}).get('name', f'Magasin {magasin_num}' if magasin_num else 'N/A')
                    
                    commandes_asten_data.append({
                        'reference': str(row.get('Référence commande', '')).strip() if 'Référence commande' in row else '',
                        'date_commande': str(row.get('Date commande', '')).strip() if 'Date commande' in row else '',
                        'magasin_num': magasin_num,
                        'magasin_nom': magasin_nom,
                        'row_data': row.to_dict(),
                    })
            except Exception as e:
                logger.warning(f'Erreur lors du chargement du fichier Asten {fichier_asten.chemin}: {str(e)}')
                continue
        
        # Charger les commandes Cyrus
        commandes_cyrus_data = []
        fichiers_cyrus = FichierSource.objects.filter(controle=controle_selected, origine='cyrus')
        for fichier_cyrus in fichiers_cyrus:
            # Vérifier si le fichier existe, sinon chercher dans l'archive
            chemin_fichier = fichier_cyrus.chemin
            if not os.path.exists(chemin_fichier):
                # Chercher dans l'archive
                nom_fichier = os.path.basename(chemin_fichier)
                archive_path = os.path.join(settings.EXTRACTIONS_PATHS.get('archive', ''), '*', nom_fichier)
                fichiers_archive = glob.glob(archive_path)
                if fichiers_archive:
                    chemin_fichier = fichiers_archive[0]
                else:
                    continue
            try:
                df_cyrus = pd.read_csv(chemin_fichier, sep=';', encoding='utf-8', low_memory=False)
                if len(df_cyrus.columns) == 1:
                    df_cyrus = pd.read_csv(chemin_fichier, sep=',', encoding='utf-8', low_memory=False)
                
                for idx, row in df_cyrus.iterrows():
                    magasin_num = str(row.get('NCID', '')).strip() if 'NCID' in row else ''
                    if not magasin_num:
                        magasin_num = str(row.get('NOMMAGASIN NOMMAG ASIN', '')).strip()[:3] if 'NOMMAGASIN NOMMAG ASIN' in row else ''
                    magasin_nom = magasins.get(str(magasin_num), {}).get('name', f'Magasin {magasin_num}' if magasin_num else 'N/A')
                    
                    date_commande_raw = str(row.get('DCDE', '')).strip() if 'DCDE' in row else ''
                    date_commande_formatted = format_cyrus_date(date_commande_raw)
                    
                    commandes_cyrus_data.append({
                        'reference': str(row.get('NCDE', '')).strip() if 'NCDE' in row else '',
                        'date_commande': date_commande_formatted,
                        'date_commande_raw': date_commande_raw,
                        'magasin_num': magasin_num,
                        'magasin_nom': magasin_nom,
                        'row_data': row.to_dict(),
                    })
            except Exception as e:
                logger.warning(f'Erreur lors du chargement du fichier Cyrus {chemin_fichier}: {str(e)}')
                continue
        
        # Identifier les commandes communes
        if commandes_asten_data and commandes_cyrus_data:
            # Fonction pour normaliser les dates
            def normalize_date_for_key(date_str, is_cyrus=False):
                """
                Normalise une date pour créer une clé de comparaison.
                Pour Cyrus : format YYMMDD (ex: 260107 = 07/01/2026)
                Pour Asten : format DD/MM/YYYY ou DDMMYY
                """
                if not date_str or str(date_str).strip() in ['', 'nan', 'None', 'NaN']:
                    return ''
                date_str = str(date_str).strip()
                
                # Format YYMMDD (6 chiffres) - Format Cyrus
                if is_cyrus and len(date_str) == 6 and date_str.isdigit():
                    year_short = date_str[:2]  # YY
                    month = date_str[2:4]      # MM
                    day = date_str[4:6]        # DD
                    try:
                        year = f"20{year_short}"
                        return f"{day}{month}{year}"  # DDMMYYYY pour comparaison
                    except:
                        pass
                
                # Format DDMMYY (6 chiffres) - Format Asten
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
                        pass
                
                # Essayer de parser les formats courants
                for fmt in ('%d/%m/%Y %H:%M:%S', '%d/%m/%Y', '%Y-%m-%d %H:%M:%S', '%Y-%m-%d'):
                    try:
                        return datetime.strptime(date_str, fmt).strftime('%d%m%Y')
                    except ValueError:
                        pass
                
                return date_str
            
            # Créer un dictionnaire des commandes Cyrus avec clé composite
            cyrus_dict = {}
            for cmd_cyrus in commandes_cyrus_data:
                ref = cmd_cyrus.get('reference', '').strip()
                # Pour Cyrus, utiliser is_cyrus=True pour le format YYMMDD
                date = normalize_date_for_key(cmd_cyrus.get('date_commande_raw', ''), is_cyrus=True)
                if ref and date:
                    key = f"{ref}|{date}"
                    if key not in cyrus_dict:
                        cyrus_dict[key] = []
                    cyrus_dict[key].append(cmd_cyrus)
            
            # Parcourir les commandes Asten et vérifier si elles existent dans Cyrus
            for cmd_asten in commandes_asten_data:
                ref_asten = cmd_asten.get('reference', '').strip()
                # Pour Asten, utiliser is_cyrus=False pour le format DD/MM/YYYY ou DDMMYY
                date_asten = normalize_date_for_key(cmd_asten.get('date_commande', ''), is_cyrus=False)
                if ref_asten and date_asten:
                    key = f"{ref_asten}|{date_asten}"
                    if key in cyrus_dict:
                        # Commande trouvée dans les deux sources
                        cmd_cyrus = cyrus_dict[key][0]
                        commandes_communes_data.append({
                            'reference': ref_asten,
                            'date_commande_asten': cmd_asten.get('date_commande', ''),
                            'date_commande_cyrus': cmd_cyrus.get('date_commande', ''),
                            'magasin_num': cmd_asten.get('magasin_num', ''),
                            'magasin_nom': cmd_asten.get('magasin_nom', ''),
                            'data_asten': cmd_asten,
                            'data_cyrus': cmd_cyrus,
                        })
            
            total_communes = len(commandes_communes_data)
    
    # Liste des contrôles pour le filtre
    controles = Controle.objects.filter(type_controle='commande').order_by('-date_execution')[:20]
    
    context = {
        'commandes_communes_data': commandes_communes_data[:100],
        'total_communes': total_communes,
        'controle_selected': controle_selected,
        'controles': controles,
        'filters': {
            'controle': controle_id,
        }
    }
    
    return render(request, 'dashboard/commandes_communes.html', context)
