import csv
import os
from datetime import datetime
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_date
from django.utils import timezone
from core.models import Magasin
from asten.models import CommandeAsten
from cyrus.models import CommandeCyrus
from imports.models import ImportFichier


def parse_date_cyrus(date_str):
    """
    Parse la date Cyrus au format YYMMDD (ex: 260107 = 2026-01-07)
    """
    if not date_str or len(date_str) != 6:
        return None
    try:
        # Format YYMMDD
        year = 2000 + int(date_str[:2])  # 26 -> 2026
        month = int(date_str[2:4])      # 01
        day = int(date_str[4:6])        # 07
        return datetime(year, month, day).date()
    except (ValueError, IndexError):
        return None


def parse_date_asten(date_str):
    """
    Parse la date Asten au format DD/MM/YYYY HH:MM:SS (ex: 09/01/2026 12:08:03)
    """
    if not date_str:
        return None
    try:
        # Extraire juste la partie date (avant l'espace)
        date_part = date_str.split()[0] if ' ' in date_str else date_str
        # Format DD/MM/YYYY
        return datetime.strptime(date_part, '%d/%m/%Y').date()
    except (ValueError, AttributeError):
        return None


def importer_fichier_asten(chemin_fichier):
    """
    Importe un fichier CSV Asten dans la base de données
    
    Colonnes utilisées :
    - Magasin : numéro du magasin
    - Référence commande : numéro_commande
    - Référence commande externe : nom de la commande
    - Date commande : date commande (format DD/MM/YYYY HH:MM:SS)
    - Date livraison : date livraison
    - Date validation : date validation
    - Statut : statut
    - Créée par : créée par
    - Validée par : validée par
    - Fournisseur : fournisseur
    """
    nom_fichier = os.path.basename(chemin_fichier)
    import_obj = ImportFichier.objects.create(
        type_fichier='asten',
        nom_fichier=nom_fichier,
        chemin_fichier=chemin_fichier,
        statut='en_cours'
    )
    
    try:
        nombre_lignes = 0
        nombre_nouveaux = 0
        nombre_dupliques = 0
        
        # Détecter le délimiteur (point-virgule ou virgule)
        with open(chemin_fichier, 'r', encoding='utf-8') as f:
            first_line = f.readline()
            delimiter = ';' if ';' in first_line else ','
            f.seek(0)
            
            reader = csv.DictReader(f, delimiter=delimiter)
            
            for row in reader:
                nombre_lignes += 1
                
                try:
                    # Parsing des données avec les noms de colonnes réels
                    code_magasin = row.get('Magasin', '').strip()
                    numero_commande = row.get('Référence commande', '').strip()
                    date_commande_str = row.get('Date commande', '').strip()
                    date_commande = parse_date_asten(date_commande_str)
                    
                    if not date_commande or not numero_commande or not code_magasin:
                        continue
                    
                    # Vérifier que le magasin existe
                    try:
                        magasin = Magasin.objects.get(code=code_magasin)
                    except Magasin.DoesNotExist:
                        continue
                    
                    # Récupérer les autres informations
                    reference_externe = row.get('Référence commande externe', '').strip() or None
                    date_livraison_str = row.get('Date livraison', '').strip()
                    date_validation_str = row.get('Date validation', '').strip()
                    statut = row.get('Statut', '').strip() or None
                    cree_par = row.get('Créée par', '').strip() or None
                    validee_par = row.get('Validée par', '').strip() or None
                    fournisseur = row.get('Fournisseur', '').strip() or None
                    
                    # Montant optionnel (chercher différentes colonnes possibles)
                    montant = None
                    for col in ['QCDUID TOTAL', 'Montant', 'montant', 'Total']:
                        if row.get(col):
                            try:
                                montant = float(str(row.get(col)).replace(',', '.'))
                                break
                            except (ValueError, TypeError):
                                pass
                    
                    # Créer ou récupérer la commande (évite les doublons)
                    commande, created = CommandeAsten.objects.get_or_create(
                        date_commande=date_commande,
                        numero_commande=numero_commande,
                        code_magasin=magasin,
                        defaults={
                            'montant': montant,
                            'statut': statut,
                            'fichier_source': nom_fichier,
                        }
                    )
                    
                    if created:
                        nombre_nouveaux += 1
                    else:
                        nombre_dupliques += 1
                        
                except Exception as e:
                    print(f"Erreur ligne {nombre_lignes}: {e}")
                    continue
        
        import_obj.nombre_lignes = nombre_lignes
        import_obj.nombre_nouveaux = nombre_nouveaux
        import_obj.nombre_dupliques = nombre_dupliques
        import_obj.statut = 'termine'
        import_obj.save()
        
        return import_obj
        
    except Exception as e:
        import_obj.statut = 'erreur'
        import_obj.message_erreur = str(e)
        import_obj.save()
        raise


def importer_fichier_cyrus(chemin_fichier):
    """
    Importe un fichier CSV Cyrus dans la base de données
    
    Colonnes utilisées :
    - NCID : numéro de magasin
    - NOMMAGASIN NOMMAG ASIN : nom de magasin (pour info, pas utilisé dans le modèle)
    - NCDE : numéro de commande
    - DCDE : date commande (format YYMMDD, ex: 260107 = 2026-01-07)
    - DCRE : date réception
    - TYCM : type commande
    """
    nom_fichier = os.path.basename(chemin_fichier)
    import_obj = ImportFichier.objects.create(
        type_fichier='cyrus',
        nom_fichier=nom_fichier,
        chemin_fichier=chemin_fichier,
        statut='en_cours'
    )
    
    try:
        nombre_lignes = 0
        nombre_nouveaux = 0
        nombre_dupliques = 0
        
        # Détecter le délimiteur (point-virgule ou virgule)
        with open(chemin_fichier, 'r', encoding='utf-8') as f:
            first_line = f.readline()
            delimiter = ';' if ';' in first_line else ','
            f.seek(0)
            
            reader = csv.DictReader(f, delimiter=delimiter)
            
            for row in reader:
                nombre_lignes += 1
                
                try:
                    # Parsing des données avec les noms de colonnes réels
                    code_magasin = row.get('NCID', '').strip()
                    numero_commande = row.get('NCDE', '').strip()
                    dcde_str = row.get('DCDE', '').strip()
                    date_commande = parse_date_cyrus(dcde_str)
                    
                    if not date_commande or not numero_commande or not code_magasin:
                        continue
                    
                    # Vérifier que le magasin existe
                    try:
                        magasin = Magasin.objects.get(code=code_magasin)
                    except Magasin.DoesNotExist:
                        continue
                    
                    # Récupérer les autres informations
                    # Gérer les caractères spéciaux dans le nom de colonne
                    nom_magasin = None
                    for key in row.keys():
                        if 'NOMMAGASIN' in key.upper() or 'NOMMAG' in key.upper():
                            nom_magasin = row.get(key, '').strip() or None
                            break
                    
                    dcre_str = row.get('DCRE', '').strip()
                    tycm = row.get('TYCM', '').strip() or None
                    
                    # Montant optionnel (chercher QCDUID TOTAL)
                    montant = None
                    qcduid_total = row.get('QCDUID TOTAL', '').strip()
                    if qcduid_total:
                        try:
                            montant = float(str(qcduid_total).replace(',', '.'))
                        except (ValueError, TypeError):
                            pass
                    
                    # Utiliser TYCM comme statut
                    statut = tycm
                    
                    # Créer ou récupérer la commande (évite les doublons)
                    commande, created = CommandeCyrus.objects.get_or_create(
                        date_commande=date_commande,
                        numero_commande=numero_commande,
                        code_magasin=magasin,
                        defaults={
                            'montant': montant,
                            'statut': statut,
                            'fichier_source': nom_fichier,
                        }
                    )
                    
                    if created:
                        nombre_nouveaux += 1
                    else:
                        nombre_dupliques += 1
                        
                except Exception as e:
                    print(f"Erreur ligne {nombre_lignes}: {e}")
                    continue
        
        import_obj.nombre_lignes = nombre_lignes
        import_obj.nombre_nouveaux = nombre_nouveaux
        import_obj.nombre_dupliques = nombre_dupliques
        import_obj.statut = 'termine'
        import_obj.save()
        
        return import_obj
        
    except Exception as e:
        import_obj.statut = 'erreur'
        import_obj.message_erreur = str(e)
        import_obj.save()
        raise


def scanner_et_importer_fichiers():
    """
    Scanne les dossiers commande_asten/ et commande_cyrus/ 
    et importe les nouveaux fichiers ou les fichiers modifiés
    """
    media_root = Path(settings.MEDIA_ROOT)
    dossier_asten = media_root / 'commande_asten'
    dossier_cyrus = media_root / 'commande_cyrus'
    
    # Créer les dossiers s'ils n'existent pas
    dossier_asten.mkdir(parents=True, exist_ok=True)
    dossier_cyrus.mkdir(parents=True, exist_ok=True)
    
    fichiers_importes = []
    
    # Importer les fichiers Asten
    for fichier in dossier_asten.glob('*.csv'):
        try:
            # Obtenir la date de modification du fichier
            date_modif_fichier = datetime.fromtimestamp(fichier.stat().st_mtime)
            date_modif_fichier_tz = timezone.make_aware(date_modif_fichier)
            
            # Vérifier si le fichier a déjà été importé
            import_existant = ImportFichier.objects.filter(
                type_fichier='asten',
                nom_fichier=fichier.name
            ).first()
            
            # Importer si nouveau fichier ou si le fichier a été modifié après l'import
            if not import_existant or date_modif_fichier_tz > import_existant.date_import:
                # Si le fichier a déjà été importé mais modifié, supprimer l'ancien import
                if import_existant:
                    import_existant.delete()
                
                import_obj = importer_fichier_asten(str(fichier))
                fichiers_importes.append(import_obj)
        except Exception as e:
            print(f"Erreur import fichier {fichier.name}: {e}")
    
    # Importer les fichiers Cyrus
    for fichier in dossier_cyrus.glob('*.csv'):
        try:
            # Obtenir la date de modification du fichier
            date_modif_fichier = datetime.fromtimestamp(fichier.stat().st_mtime)
            date_modif_fichier_tz = timezone.make_aware(date_modif_fichier)
            
            # Vérifier si le fichier a déjà été importé
            import_existant = ImportFichier.objects.filter(
                type_fichier='cyrus',
                nom_fichier=fichier.name
            ).first()
            
            # Importer si nouveau fichier ou si le fichier a été modifié après l'import
            if not import_existant or date_modif_fichier_tz > import_existant.date_import:
                # Si le fichier a déjà été importé mais modifié, supprimer l'ancien import
                if import_existant:
                    import_existant.delete()
                
                import_obj = importer_fichier_cyrus(str(fichier))
                fichiers_importes.append(import_obj)
        except Exception as e:
            print(f"Erreur import fichier {fichier.name}: {e}")
    
    return fichiers_importes

