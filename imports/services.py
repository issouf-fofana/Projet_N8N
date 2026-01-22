import csv
import os
import pandas as pd
from datetime import datetime
from pathlib import Path
from django.conf import settings
from django.db import transaction
from django.utils.dateparse import parse_date
from django.utils import timezone
from core.models import Magasin
from asten.models import CommandeAsten
from cyrus.models import CommandeCyrus
from gpv.models import CommandeGPV
try:
    from legend.models import CommandeLegend
except Exception:
    CommandeLegend = None
from br.models import BRAsten
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


def parse_date_gpv(date_str):
    """
    Parse la date GPV au format DD/MM/YYYY HH:MM (ex: 14/01/2026 14:06)
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


def parse_date_br(date_str):
    """
    Parse la date BR (supporte DD/MM/YYYY et YYYY-MM-DD, avec ou sans heure)
    Gère aussi les objets datetime pandas et les dates Excel sérialisées
    """
    if not date_str:
        return None
    
    # Gérer les valeurs NaN/NaT de pandas
    try:
        import pandas as pd
        if pd.isna(date_str):
            return None
    except:
        pass
    
    # Si c'est un Timestamp pandas
    if hasattr(date_str, 'to_pydatetime'):
        try:
            return date_str.to_pydatetime().date()
        except (AttributeError, ValueError):
            pass
    
    # Si c'est déjà un objet date ou datetime Python
    if hasattr(date_str, 'date'):
        try:
            return date_str.date()
        except (AttributeError, ValueError):
            pass
    
    # Si c'est un nombre (date Excel sérialisée)
    if isinstance(date_str, (int, float)):
        try:
            # Excel date serial: 1 = 1900-01-01, mais pandas utilise 1900-01-01 comme 0
            # On utilise pandas pour convertir si disponible
            import pandas as pd
            if isinstance(date_str, float) or isinstance(date_str, int):
                # Convertir le nombre en date via pandas
                date_obj = pd.to_datetime(date_str, origin='1899-12-30', unit='D')
                return date_obj.date()
        except (ValueError, TypeError, AttributeError):
            pass
    
    # Sinon, traiter comme une chaîne de caractères
    date_str = str(date_str).strip()
    for fmt in (
        '%d/%m/%Y',
        '%d/%m/%Y %H:%M',
        '%d/%m/%Y %H:%M:%S',
        '%Y-%m-%d',
        '%Y-%m-%d %H:%M',
        '%Y-%m-%d %H:%M:%S',
    ):
        try:
            return datetime.strptime(date_str, fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def get_valeur_premiere(row_normalized, candidats):
    for key in candidats:
        valeur = row_normalized.get(key)
        if valeur is not None and str(valeur).strip() != '':
            return str(valeur).strip()
    return ''


def normalize_numero_br(valeur):
    if valeur is None:
        return ''
    # Conserver le numéro sans partie décimale
    if isinstance(valeur, (int, float)):
        try:
            return str(int(valeur))
        except Exception:
            pass
    valeur_str = str(valeur).strip()
    if '.' in valeur_str:
        valeur_str = valeur_str.split('.')[0]
    # Extraire uniquement les chiffres
    digits = ''.join(ch for ch in valeur_str if ch.isdigit())
    return digits


def normalize_code_magasin(valeur):
    if valeur is None:
        return ''
    valeur_str = str(valeur).strip()
    if '/' in valeur_str:
        valeur_str = valeur_str.split('/')[-1].strip()
    # Normaliser sur 3 caractères (000 -> 999)
    digits = ''.join(ch for ch in valeur_str if ch.isdigit())
    if digits:
        return digits.zfill(3) if len(digits) < 3 else digits
    return valeur_str


def parse_statut_ic(valeur):
    if valeur is None:
        return False
    val = str(valeur).strip().lower()
    if val in ['intégré', 'integre', 'trouvé', 'trouve', 'oui', 'ok', 'true', '1', 'x']:
        return True
    if val in ['non intégré', 'non integre', 'non trouvé', 'non trouve', 'non', 'absent', '0', 'false']:
        return False
    return False




def parse_date_legend(date_str):
    """
    Parse la date Legend au format DD/MM/YYYY (ex: 13/01/2026)
    """
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str.strip(), '%d/%m/%Y').date()
    except (ValueError, AttributeError):
        return None


def extraire_numero_legend(numero_brut):
    """
    Extrait la partie numérique d'un numéro Legend (ex: DIV-260148 -> 260148)
    """
    if not numero_brut:
        return None
    numero_brut = numero_brut.strip()
    if '-' in numero_brut:
        return numero_brut.split('-')[-1].strip()
    return numero_brut


def parse_exportee_legend(valeur):
    """
    Convertit le statut Exportée Legend en booléen.
    Valeurs attendues : "Coché", "Oui", "True", "1"
    """
    if not valeur:
        return False
    valeur = str(valeur).strip().lower()
    return valeur in ['coché', 'coche', 'oui', 'true', '1', 'x']


def importer_fichier_legend(chemin_fichier):
    """
    Importe un fichier CSV Legend dans la base de données.
    """
    if CommandeLegend is None:
        raise RuntimeError("L'application legend n'est pas installée.")
    nom_fichier = os.path.basename(chemin_fichier)
    import_obj = ImportFichier.objects.create(
        type_fichier='legend',
        nom_fichier=nom_fichier,
        chemin_fichier=chemin_fichier,
        statut='en_cours'
    )

    try:
        nombre_lignes = 0
        nombre_nouveaux = 0
        nombre_dupliques = 0

        with open(chemin_fichier, 'r', encoding='utf-8') as f:
            first_line = f.readline()
            delimiter = ';' if ';' in first_line else ','
            f.seek(0)

            reader = csv.DictReader(f, delimiter=delimiter)
            for row in reader:
                nombre_lignes += 1
                try:
                    # Normaliser les clés pour gérer un éventuel BOM (﻿) et les espaces
                    row_normalized = {str(k).lstrip('\ufeff').strip(): v for k, v in row.items()}

                    numero_brut = row_normalized.get('Numéro', '').strip()
                    numero_commande = extraire_numero_legend(numero_brut)
                    depot_destination = row_normalized.get('Dépôt de destination', '').strip() or None
                    depot_origine = row_normalized.get("Dépôt d'origine", '').strip() or None
                    date_commande = parse_date_legend(row_normalized.get('Date', '').strip())
                    observation = row_normalized.get('Observation', '').strip() or None
                    transfert = row_normalized.get('Transfert entre dépôt', '').strip() or None
                    exportee = parse_exportee_legend(row_normalized.get('Exportée', '').strip())
                    code_client = row_normalized.get('Code du client', '').strip() or None
                    code_depot = row_normalized.get('Code du dépôt', '').strip() or None
                    date_livraison_prevue = parse_date_legend(row_normalized.get('Date de livraison prévue', '').strip())

                    if not numero_commande or not date_commande or not depot_origine:
                        continue

                    commande, created = CommandeLegend.objects.get_or_create(
                        date_commande=date_commande,
                        numero_commande=numero_commande,
                        depot_origine=depot_origine,
                        defaults={
                            'numero_brut': numero_brut,
                            'depot_destination': depot_destination,
                            'observation': observation,
                            'transfert': transfert,
                            'exportee': exportee,
                            'code_client': code_client,
                            'code_depot': code_depot,
                            'date_livraison_prevue': date_livraison_prevue,
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


def importer_fichier_br_asten(chemin_fichier):
    """
    Importe un fichier BR ASTEN (CSV ou Excel).
    Colonnes utilisées :
    - N° de bon de livraison / N° DE BR -> numero_br
    - Date -> date_br
    - Magasin -> code_magasin
    - Statut IC -> ic_integre / statut_ic (si présent)
    Pour les fichiers Excel à feuilles "BRS TROUVEES"/"BRS NON TROUVEES",
    le statut IC est déduit du nom de la feuille.
    """
    nom_fichier = os.path.basename(chemin_fichier)
    import_obj = ImportFichier.objects.create(
        type_fichier='br_asten',
        nom_fichier=nom_fichier,
        chemin_fichier=chemin_fichier,
        statut='en_cours'
    )

    try:
        nombre_lignes = 0
        nombre_nouveaux = 0
        nombre_dupliques = 0

        def enregistrer_br(row_normalized, statut_ic_force=None, ic_integre_force=None):
            nonlocal nombre_lignes, nombre_nouveaux, nombre_dupliques
            nombre_lignes += 1

            numero_br = normalize_numero_br(get_valeur_premiere(
                row_normalized,
                ['N° de bon de livraison', 'N° de bon livraison', 'N° bon de livraison', 'Numero BL', 'Numéro BL', 'N° DE BR', 'N° BR']
            ))
            # Prioriser la date de validation, puis date de réception, puis date BR
            date_br_str = get_valeur_premiere(row_normalized, ['Date validation', 'Date réception', 'Date reception', 'Date', 'Date BR'])
            code_magasin = normalize_code_magasin(get_valeur_premiere(row_normalized, ['Magasin', 'Code magasin', 'Code Magasin']))
            statut_ic = statut_ic_force if statut_ic_force is not None else get_valeur_premiere(
                row_normalized, ['Statut IC', 'Statut', 'Intégration IC', 'Integration IC']
            )
            # Si ic_integre_force est défini (feuille Excel), l'utiliser
            # Sinon, si statut_ic est vide, considérer comme intégré par défaut (pour les CSV sans statut)
            if ic_integre_force is not None:
                ic_integre = ic_integre_force
            elif not statut_ic or statut_ic.strip() == '':
                # Par défaut, si pas de statut IC, considérer comme intégré
                ic_integre = True
                statut_ic = 'Intégré'
            else:
                ic_integre = parse_statut_ic(statut_ic)

            date_br = parse_date_br(date_br_str)
            if not numero_br or not date_br or not code_magasin:
                # Log pour debug : pourquoi la ligne est ignorée
                if not numero_br:
                    print(f"Ligne ignorée: numéro BR manquant (valeur: {row_normalized.get('N° de bon de livraison', 'N/A')})")
                elif not date_br:
                    print(f"Ligne ignorée: date BR invalide (valeur: {date_br_str}, type: {type(date_br_str)})")
                elif not code_magasin:
                    print(f"Ligne ignorée: code magasin manquant (valeur: {row_normalized.get('Magasin', 'N/A')})")
                return

            magasin, _ = Magasin.objects.get_or_create(
                code=code_magasin,
                defaults={'nom': code_magasin}
            )

            br, created = BRAsten.objects.get_or_create(
                numero_br=numero_br,
                date_br=date_br,
                code_magasin=magasin,
                defaults={
                    'fichier_source': nom_fichier,
                    'statut_ic': statut_ic,
                    'ic_integre': ic_integre,
                }
            )
            if created:
                nombre_nouveaux += 1
            else:
                nombre_dupliques += 1
                if br.statut_ic != statut_ic or br.ic_integre != ic_integre or br.fichier_source != nom_fichier:
                    br.statut_ic = statut_ic
                    br.ic_integre = ic_integre
                    br.fichier_source = nom_fichier
                    br.save(update_fields=['statut_ic', 'ic_integre', 'fichier_source'])

        if chemin_fichier.lower().endswith(('.xlsx', '.xls')):
            xl = pd.ExcelFile(chemin_fichier)
            # Vérifier s'il y a des feuilles avec "BRS" ou "BR" dans le nom
            feuilles_avec_br = [s for s in xl.sheet_names if 'BRS' in s.upper() or ('BR' in s.upper() and not s.upper().startswith('BR'))]
            traiter_toutes_les_feuilles = len(feuilles_avec_br) == 0
            
            if traiter_toutes_les_feuilles:
                print(f"Aucune feuille avec 'BR' trouvée. Traitement de toutes les feuilles: {xl.sheet_names}")
            
            for sheet_name in xl.sheet_names:
                sheet_upper = sheet_name.upper()
                
                # Ignorer les feuilles qui ne sont clairement pas des BR (comme "MERGE", "Anomalies", etc.)
                if 'ANOMALIE' in sheet_upper or sheet_upper == 'MERGE':
                    continue
                
                # Si on ne traite pas toutes les feuilles, ignorer celles sans "BR" ou "BRS"
                if not traiter_toutes_les_feuilles:
                    if 'BRS' not in sheet_upper and 'BR' not in sheet_upper:
                        continue
                
                # Déterminer le statut IC selon le nom de la feuille
                statut_ic_force = None
                ic_integre_force = None
                
                if 'BRS' in sheet_upper or 'BR' in sheet_upper:
                    # BR_TROUVEE = BR intégré
                    if 'TROUVEE' in sheet_upper and 'NON' not in sheet_upper:
                        statut_ic_force = 'Intégré'
                        ic_integre_force = True
                    # BR_NON_TROUVEE = BR non intégré
                    elif 'NON' in sheet_upper or 'NON_TROUVEE' in sheet_upper or 'NON TROUVEE' in sheet_upper:
                        statut_ic_force = 'Non intégré'
                        ic_integre_force = False
                    elif 'TROUVEES' in sheet_upper or 'TROUVÉES' in sheet_upper:
                        statut_ic_force = 'Intégré'
                        ic_integre_force = True

                # Lire le fichier Excel
                df = pd.read_excel(chemin_fichier, sheet_name=sheet_name)
                
                # Si toutes les colonnes sont "Unnamed", essayer de détecter les colonnes
                if all(str(col).startswith('Unnamed') for col in df.columns):
                    # Chercher la première ligne non vide qui pourrait être l'en-tête
                    header_found = False
                    for idx in range(min(5, len(df))):
                        row = df.iloc[idx]
                        if not row.isna().all():
                            # Vérifier si cette ligne ressemble à des en-têtes
                            valeurs = [str(v).strip().lower() if pd.notna(v) else '' for v in row.values]
                            if any('br' in v or 'date' in v or 'magasin' in v or 'réception' in v or 'reception' in v or 'validation' in v for v in valeurs):
                                # Utiliser cette ligne comme en-têtes
                                df.columns = [str(v).strip() if pd.notna(v) else f'Col_{i}' for i, v in enumerate(row.values)]
                                df = df.iloc[idx+1:].reset_index(drop=True)
                                header_found = True
                                break
                    
                    # Si pas d'en-tête trouvé, utiliser les positions standard basées sur l'image
                    # Colonne 0: Magasin, Colonne 1: Date réception, Colonne 2: Date validation, Colonne 3: N° DE BR
                    if not header_found and len(df.columns) >= 4:
                        # Renommer les colonnes selon l'ordre attendu
                        df.columns = ['Magasin', 'Date réception', 'Date validation', 'N° DE BR'] + [f'Col_{i}' for i in range(4, len(df.columns))]
                
                # Essayer de convertir les colonnes de date automatiquement
                for col in df.columns:
                    col_str = str(col).lower()
                    if 'date' in col_str:
                        try:
                            # Essayer de convertir en datetime pandas (gère les dates Excel sérialisées)
                            df[col] = pd.to_datetime(df[col], errors='coerce', dayfirst=True)
                        except:
                            pass
                
                for _, row in df.iterrows():
                    try:
                        # Ignorer les lignes complètement vides
                        if row.isna().all():
                            continue
                        row_normalized = {str(k).lstrip('\ufeff').strip(): v for k, v in row.items()}
                        enregistrer_br(row_normalized, statut_ic_force, ic_integre_force)
                    except Exception as e:
                        print(f"Erreur ligne feuille {sheet_name}: {e}")
                        continue
        else:
            with open(chemin_fichier, 'r', encoding='utf-8') as f:
                first_line = f.readline()
                delimiter = ';' if ';' in first_line else ','
                f.seek(0)

                reader = csv.DictReader(f, delimiter=delimiter)
                for row in reader:
                    try:
                        row_normalized = {str(k).lstrip('\ufeff').strip(): v for k, v in row.items()}
                        enregistrer_br(row_normalized)
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
                    code_magasin = normalize_code_magasin(row.get('Magasin', '').strip())
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

            reader = csv.reader(f, delimiter=delimiter)
            header = next(reader, None)
            if header is None:
                header = []
            header_normalized = [str(h).strip().upper().replace(' ', '') for h in header]
            has_header = any(h in header_normalized for h in ['NCID', 'NCDE', 'DCDE'])

            def traiter_ligne(code_magasin, numero_commande, dcde_str, dcre_str, tycm, nom_magasin, qcduid_total):
                nonlocal nombre_nouveaux, nombre_dupliques

                # Normaliser le code magasin sur 3 caractères
                code_magasin = normalize_code_magasin(code_magasin)

                # Normaliser le numéro de commande (garder uniquement les chiffres)
                numero_str = str(numero_commande)
                digits = ''.join(ch for ch in numero_str if ch.isdigit())
                if digits:
                    numero_commande = digits.lstrip('0') or '0'
                else:
                    numero_commande = numero_str.strip()

                date_commande = parse_date_cyrus(dcde_str)
                if not date_commande or not numero_commande or not code_magasin:
                    return

                # Vérifier que le magasin existe
                try:
                    magasin = Magasin.objects.get(code=code_magasin)
                except Magasin.DoesNotExist:
                    return

                # Montant optionnel
                montant = None
                if qcduid_total:
                    try:
                        montant = float(str(qcduid_total).replace(',', '.'))
                    except (ValueError, TypeError):
                        pass

                # Utiliser TYCM comme statut
                statut = tycm or None

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

            if has_header:
                dict_reader = csv.DictReader(f, delimiter=delimiter, fieldnames=header)
                for row in dict_reader:
                    nombre_lignes += 1
                    try:
                        row_normalized = {}
                        for key, value in row.items():
                            key_norm = str(key).strip().upper().replace(' ', '')
                            row_normalized[key_norm] = str(value).strip() if value is not None else ''

                        code_magasin = row_normalized.get('NCID', '')
                        numero_commande = row_normalized.get('NCDE', '')
                        dcde_str = row_normalized.get('DCDE', '')
                        dcre_str = row_normalized.get('DCRE', '')
                        tycm = row_normalized.get('TYCM', '') or None

                        nom_magasin = None
                        for key, value in row_normalized.items():
                            if 'NOMMAGASIN' in key or 'NOMMAG' in key:
                                nom_magasin = value or None
                                break

                        qcduid_total = row_normalized.get('QCDUIDTOTAL', '')
                        traiter_ligne(code_magasin, numero_commande, dcde_str, dcre_str, tycm, nom_magasin, qcduid_total)
                    except Exception as e:
                        print(f"Erreur ligne {nombre_lignes}: {e}")
                        continue
            else:
                # Fichier sans en-tête (format positionnel)
                # Exemple: 1;;80;MANDARINE MARCORY;117514;4517.0;260117;260117;G;GPV
                def parse_row_cols(cols):
                    if len(cols) < 10:
                        return
                    code_magasin = cols[2]
                    nom_magasin = cols[3]
                    numero_commande = cols[4]
                    qcduid_total = cols[5]
                    dcde_str = cols[6]
                    dcre_str = cols[7]
                    tycm = cols[9] if len(cols) > 9 else None
                    traiter_ligne(code_magasin, numero_commande, dcde_str, dcre_str, tycm, nom_magasin, qcduid_total)

                if header:
                    nombre_lignes += 1
                    try:
                        parse_row_cols(header)
                    except Exception as e:
                        print(f"Erreur ligne {nombre_lignes}: {e}")
                for cols in reader:
                    nombre_lignes += 1
                    try:
                        parse_row_cols(cols)
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
    Scanne les dossiers commande_asten/, commande_cyrus/, commande_gpv/, commande_legend/ et br_asten/
    et importe les nouveaux fichiers ou les fichiers modifiés
    """
    media_root = Path(settings.MEDIA_ROOT)
    dossier_asten = media_root / 'commande_asten'
    dossier_cyrus = media_root / 'commande_cyrus'
    dossier_gpv = media_root / 'commande_gpv'
    dossier_legend = media_root / 'commande_legend'
    dossier_br_asten = media_root / 'br_asten'
    
    # Créer les dossiers s'ils n'existent pas
    dossier_asten.mkdir(parents=True, exist_ok=True)
    dossier_cyrus.mkdir(parents=True, exist_ok=True)
    dossier_gpv.mkdir(parents=True, exist_ok=True)
    dossier_legend.mkdir(parents=True, exist_ok=True)
    dossier_br_asten.mkdir(parents=True, exist_ok=True)
    
    fichiers_importes = []

    def supprimer_fichier_source(chemin_fichier):
        try:
            Path(chemin_fichier).unlink(missing_ok=True)
        except Exception:
            pass
    
    # Importer les fichiers Asten
    fichiers_asten = list(dossier_asten.glob('*.csv')) + list(dossier_asten.glob('*.CSV'))
    for fichier in fichiers_asten:
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
                    # Supprimer les anciennes données
                    CommandeAsten.objects.filter(fichier_source=fichier.name).delete()
                    import_existant.delete()
                
                import_obj = importer_fichier_asten(str(fichier))
                fichiers_importes.append(import_obj)
                if import_obj and import_obj.statut == 'termine':
                    supprimer_fichier_source(fichier)
            elif import_existant and import_existant.statut == 'termine':
                # Fichier déjà importé avec succès : nettoyer le dossier
                supprimer_fichier_source(fichier)
        except Exception as e:
            print(f"Erreur import fichier {fichier.name}: {e}")
    
    # Importer les fichiers Cyrus
    fichiers_cyrus = list(dossier_cyrus.glob('*.csv')) + list(dossier_cyrus.glob('*.CSV'))
    for fichier in fichiers_cyrus:
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
                    # Supprimer les anciennes données
                    CommandeCyrus.objects.filter(fichier_source=fichier.name).delete()
                    import_existant.delete()
                
                import_obj = importer_fichier_cyrus(str(fichier))
                fichiers_importes.append(import_obj)
                if import_obj and import_obj.statut == 'termine':
                    supprimer_fichier_source(fichier)
            elif import_existant and import_existant.statut == 'termine':
                # Fichier déjà importé avec succès : nettoyer le dossier
                supprimer_fichier_source(fichier)
        except Exception as e:
            print(f"Erreur import fichier {fichier.name}: {e}")
    
    # Importer les fichiers GPV
    fichiers_gpv = list(dossier_gpv.glob('*.csv')) + list(dossier_gpv.glob('*.CSV'))
    for fichier in fichiers_gpv:
        try:
            # Obtenir la date de modification du fichier
            date_modif_fichier = datetime.fromtimestamp(fichier.stat().st_mtime)
            date_modif_fichier_tz = timezone.make_aware(date_modif_fichier)
            
            # Vérifier si le fichier a déjà été importé
            import_existant = ImportFichier.objects.filter(
                type_fichier='gpv',
                nom_fichier=fichier.name
            ).first()
            
            # Importer si nouveau fichier ou si le fichier a été modifié après l'import
            if not import_existant or date_modif_fichier_tz > import_existant.date_import:
                # Si le fichier a déjà été importé mais modifié, supprimer l'ancien import
                if import_existant:
                    # Supprimer les anciennes données
                    CommandeGPV.objects.filter(fichier_source=fichier.name).delete()
                    import_existant.delete()
                
                import_obj = importer_fichier_gpv(str(fichier))
                fichiers_importes.append(import_obj)
                if import_obj and import_obj.statut == 'termine':
                    supprimer_fichier_source(fichier)
            elif import_existant and import_existant.statut == 'termine':
                # Fichier déjà importé avec succès : nettoyer le dossier
                supprimer_fichier_source(fichier)
        except Exception as e:
            print(f"Erreur import fichier {fichier.name}: {e}")

    # Importer les fichiers Legend
    fichiers_legend = list(dossier_legend.glob('*.csv')) + list(dossier_legend.glob('*.CSV'))
    for fichier in fichiers_legend:
        try:
            date_modif_fichier = datetime.fromtimestamp(fichier.stat().st_mtime)
            date_modif_fichier_tz = timezone.make_aware(date_modif_fichier)

            import_existant = ImportFichier.objects.filter(
                type_fichier='legend',
                nom_fichier=fichier.name
            ).first()

            if not import_existant or date_modif_fichier_tz > import_existant.date_import:
                if import_existant:
                    CommandeLegend.objects.filter(fichier_source=fichier.name).delete()
                    import_existant.delete()

                import_obj = importer_fichier_legend(str(fichier))
                fichiers_importes.append(import_obj)
                if import_obj and import_obj.statut == 'termine':
                    supprimer_fichier_source(fichier)
            elif import_existant and import_existant.statut == 'termine':
                # Fichier déjà importé avec succès : nettoyer le dossier
                supprimer_fichier_source(fichier)
        except Exception as e:
            print(f"Erreur import fichier Legend {fichier.name}: {e}")

    # Importer les fichiers BR Asten
    fichiers_br_asten = (
        list(dossier_br_asten.glob('*.csv')) + list(dossier_br_asten.glob('*.CSV')) +
        list(dossier_br_asten.glob('*.xlsx')) + list(dossier_br_asten.glob('*.XLSX')) +
        list(dossier_br_asten.glob('*.xls')) + list(dossier_br_asten.glob('*.XLS'))
    )
    for fichier in fichiers_br_asten:
        try:
            date_modif_fichier = datetime.fromtimestamp(fichier.stat().st_mtime)
            date_modif_fichier_tz = timezone.make_aware(date_modif_fichier)

            import_existant = ImportFichier.objects.filter(
                type_fichier='br_asten',
                nom_fichier=fichier.name
            ).first()

            no_records = BRAsten.objects.filter(fichier_source=fichier.name).count() == 0
            if not import_existant or date_modif_fichier_tz > import_existant.date_import or no_records:
                if import_existant:
                    BRAsten.objects.filter(fichier_source=fichier.name).delete()
                    import_existant.delete()

                import_obj = importer_fichier_br_asten(str(fichier))
                fichiers_importes.append(import_obj)
                # Ne supprimer le fichier que si l'import a réussi ET qu'au moins une ligne a été importée
                if import_obj and import_obj.statut == 'termine' and import_obj.nombre_lignes > 0:
                    supprimer_fichier_source(fichier)
                elif import_obj and import_obj.statut == 'termine' and import_obj.nombre_lignes == 0:
                    print(f"Attention: Fichier {fichier.name} importé avec 0 lignes. Fichier conservé pour investigation.")
            elif import_existant and import_existant.statut == 'termine':
                # Fichier déjà importé avec succès : nettoyer le dossier
                supprimer_fichier_source(fichier)
        except Exception as e:
            print(f"Erreur import fichier BR Asten {fichier.name}: {e}")

    # BR IC désactivé : on ne compare plus, seul le fichier BR ASTEN est utilisé

    return fichiers_importes


def importer_fichier_gpv(chemin_fichier):
    """
    Importe un fichier CSV GPV dans la base de données
    
    Colonnes utilisées :
    - NUMERO COMMANDE : numéro de commande
    - CODE MAGASIN : code du magasin
    - NOM  MAGASIN : nom du magasin
    - DATE CREATION : date de création (format DD/MM/YYYY HH:MM)
    - DATE VALIDATION : date de validation (format DD/MM/YYYY HH:MM)
    - DATE TRANSFERT : date de transfert (peut être vide)
    - STATUT : statut de la commande
    """
    nom_fichier = os.path.basename(chemin_fichier)
    import_obj = ImportFichier.objects.create(
        type_fichier='gpv',
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
                    numero_commande = row.get('NUMERO COMMANDE', '').strip()
                    code_magasin = normalize_code_magasin(row.get('CODE MAGASIN', '').strip())
                    nom_magasin = row.get('NOM  MAGASIN', '').strip() or None
                    date_creation_str = row.get('DATE CREATION', '').strip()
                    date_validation_str = row.get('DATE VALIDATION', '').strip()
                    date_transfert_str = row.get('DATE TRANSFERT', '').strip()
                    statut = row.get('STATUT', '').strip() or None
                    
                    # Parser les dates
                    date_creation = parse_date_gpv(date_creation_str)
                    date_validation = parse_date_gpv(date_validation_str) if date_validation_str else None
                    date_transfert = parse_date_gpv(date_transfert_str) if date_transfert_str else None
                    
                    if not date_creation or not numero_commande or not code_magasin:
                        continue
                    
                    # Vérifier que le magasin existe
                    try:
                        magasin = Magasin.objects.get(code=code_magasin)
                    except Magasin.DoesNotExist:
                        continue
                    
                    # Créer ou récupérer la commande (évite les doublons)
                    commande, created = CommandeGPV.objects.get_or_create(
                        date_creation=date_creation,
                        numero_commande=numero_commande,
                        code_magasin=magasin,
                        defaults={
                            'nom_magasin': nom_magasin,
                            'date_validation': date_validation,
                            'date_transfert': date_transfert,
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

