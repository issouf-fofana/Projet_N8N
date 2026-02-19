import csv
import os
import re
import unicodedata
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
from imports.models import ImportFichier, FactureSage, FactureBackupCyrus


def parse_date_cyrus(date_str):
    """
    Parse la date Cyrus au format YYMMDD (ex: 260107 = 2026-01-07)
    Supporte aussi les formats avec séparateurs ou espaces
    """
    if not date_str:
        return None
    
    # Nettoyer la chaîne (enlever espaces, séparateurs)
    date_str = str(date_str).strip().replace('/', '').replace('-', '').replace(' ', '')
    
    # Si la longueur n'est pas 6, essayer d'extraire les 6 premiers chiffres
    if len(date_str) < 6:
        return None
    
    # Extraire les 6 premiers chiffres
    digits = ''.join(ch for ch in date_str if ch.isdigit())
    if len(digits) < 6:
        return None
    
    date_str = digits[:6]
    
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


def parse_facture_backup_line(line):
    """
    Parse une ligne de facture backup.
    - Code magasin: 3 chiffres juste après 'E'
    - Statut: 'G' (générale) ou 'P' (promo)
    - Numéro facture:
        - promo: chiffres juste avant 'P'
        - générale FA: 'FA' + chiffres jusqu'à 'G'
        - générale standard: 10 chiffres juste avant 'G'
    - Thème promo: 4 caractères après 'P'
    """
    if not line:
        return None
    raw = str(line).strip()
    if len(raw) < 4 or not raw.startswith('E'):
        return None
    code_magasin = raw[1:4]
    if not code_magasin.isdigit():
        return None

    promo_matches = list(re.finditer(r'(\d+)(P)([A-Za-z0-9]{4})', raw))
    if promo_matches:
        match = promo_matches[-1]
        return {
            'code_magasin': code_magasin,
            'numero_facture': match.group(1),
            'type_facture': 'promo',
            'theme_promo': match.group(3),
        }

    fa_matches = list(re.finditer(r'(FA\d+)(G)', raw))
    if fa_matches:
        match = fa_matches[-1]
        return {
            'code_magasin': code_magasin,
            'numero_facture': match.group(1),
            'type_facture': 'general',
            'theme_promo': None,
        }

    g_matches = list(re.finditer(r'(\d{10})(G)', raw))
    if g_matches:
        match = g_matches[-1]
        return {
            'code_magasin': code_magasin,
            'numero_facture': match.group(1),
            'type_facture': 'general',
            'theme_promo': None,
        }

    return None


def get_valeur_premiere(row_normalized, candidats):
    for key in candidats:
        valeur = row_normalized.get(key)
        if valeur is not None and str(valeur).strip() != '':
            return str(valeur).strip()
    return ''


def normalize_header_key(valeur):
    if valeur is None:
        return ''
    val = str(valeur).strip().lower()
    # Supprimer accents
    val = ''.join(c for c in unicodedata.normalize('NFD', val) if unicodedata.category(c) != 'Mn')
    # Garder lettres/chiffres seulement
    val = ''.join(ch for ch in val if ch.isalnum())
    return val


def get_valeur_premiere_normalized(row_normalized, candidats):
    """
    Recherche par comparaison normalisée (sans accents, sans espaces/ponctuation).
    """
    normalized_map = {normalize_header_key(k): v for k, v in row_normalized.items()}
    for key in candidats:
        key_norm = normalize_header_key(key)
        if key_norm in normalized_map:
            valeur = normalized_map.get(key_norm)
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


def normalize_commande_fournisseur(valeur):
    if valeur is None:
        return ''
    # Conserver la référence sans partie décimale si numérique
    if isinstance(valeur, (int, float)):
        try:
            return str(int(valeur))
        except Exception:
            pass
    valeur_str = str(valeur).strip()
    if valeur_str.endswith('.0'):
        valeur_str = valeur_str[:-2]
    return valeur_str.upper()


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
    if val in [
        'intégré', 'integre', 'intégrée', 'integree', 'intégrées', 'integrees',
        'trouvé', 'trouve', 'trouvée', 'trouvee', 'trouvées', 'trouvees',
        'oui', 'ok', 'true', '1', 'x'
    ]:
        return True
    if val in [
        'non intégré', 'non integre', 'non intégrée', 'non integree',
        'non trouvé', 'non trouve', 'non trouvée', 'non trouvee', 'non trouvées', 'non trouvees',
        'non', 'absent', '0', 'false'
    ]:
        return False
    if 'trouv' in val and 'non' not in val:
        return True
    if 'non' in val and 'trouv' in val:
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

            # Filtrer les BR non validés (Date validation vide)
            date_validation = None
            validation_values = []
            for k, v in row_normalized.items():
                key_norm = normalize_header_key(k)
                if 'validation' in key_norm:
                    validation_values.append(v)
            if validation_values:
                # Si la colonne existe mais est vide/non parsable => non validé, ignorer
                for v in validation_values:
                    v_str = str(v).strip() if v is not None else ''
                    v_norm = v_str.lower()
                    if v_norm in ('', 'nan', 'nat', 'none', 'null', '0', '0.0'):
                        continue
                    date_validation = parse_date_br(v_str)
                    if date_validation:
                        break
                if not date_validation:
                    return

            numero_br = normalize_numero_br(get_valeur_premiere(
                row_normalized,
                [
                    'N° de bon de livraison', 'N° de bon livraison', 'N° bon de livraison', 'N° bon livraison',
                    'No bon de livraison', 'No bon livraison', 'Numero bon livraison', 'Numéro bon livraison',
                    'Numero BL', 'Numéro BL', 'N° BL', 'N° DE BR', 'N° BR'
                ]
            ))
            # Prioriser la date de création, puis validation, réception, puis date BR
            date_br_str = get_valeur_premiere(
                row_normalized,
                ['Date création', 'Date creation', 'Date validation', 'Date réception', 'Date reception', 'Date', 'Date BR']
            )
            code_magasin = normalize_code_magasin(get_valeur_premiere(row_normalized, ['Magasin', 'Code magasin', 'Code Magasin']))
            commande_fournisseur = normalize_commande_fournisseur(get_valeur_premiere(
                row_normalized,
                ['Commande fournisseur', 'Commande Fournisseur', 'N° Cde', 'N° Cde.', 'N° Commande']
            ))
            statut_ic = statut_ic_force if statut_ic_force is not None else get_valeur_premiere(
                row_normalized, ['Statut IC', 'Statut', 'Intégration IC', 'Integration IC']
            )
            # Si ic_integre_force est défini (feuille Excel), l'utiliser
            # Sinon, si statut_ic est vide, considérer comme intégré par défaut (pour les CSV sans statut)
            if ic_integre_force is not None:
                ic_integre = ic_integre_force
            elif not statut_ic or statut_ic.strip() == '':
                # Nouvelle logique : si pas de statut IC, considérer comme non intégré
                ic_integre = False
                statut_ic = 'Non intégré'
            else:
                ic_integre = parse_statut_ic(statut_ic)

            date_br = parse_date_br(date_br_str)
            if not numero_br or not date_br or not code_magasin:
                # Log pour debug : pourquoi la ligne est ignorée
                if not numero_br:
                    print(f"Ligne ignorée: numéro BR manquant (valeur: {get_valeur_premiere(row_normalized, ['N° de bon de livraison', 'N° bon livraison', 'N° bon de livraison', 'N° BL', 'N° BR']) or 'N/A'})")
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
                    'commande_fournisseur': commande_fournisseur or None,
                    'date_validation': date_validation,
                }
            )
            if created:
                nombre_nouveaux += 1
            else:
                nombre_dupliques += 1
                if br.override_statut_ic:
                    # Ne pas écraser un statut modifié manuellement
                    fields = []
                    if br.fichier_source != nom_fichier:
                        br.fichier_source = nom_fichier
                        fields.append('fichier_source')
                    if br.commande_fournisseur != (commande_fournisseur or None):
                        br.commande_fournisseur = commande_fournisseur or None
                        fields.append('commande_fournisseur')
                    if br.date_validation != date_validation:
                        br.date_validation = date_validation
                        fields.append('date_validation')
                    if fields:
                        br.save(update_fields=fields)
                else:
                    if (
                        br.statut_ic != statut_ic or br.ic_integre != ic_integre or
                        br.fichier_source != nom_fichier or br.commande_fournisseur != (commande_fournisseur or None) or
                        br.date_validation != date_validation
                    ):
                        br.statut_ic = statut_ic
                        br.ic_integre = ic_integre
                        br.fichier_source = nom_fichier
                        br.commande_fournisseur = commande_fournisseur or None
                        br.date_validation = date_validation
                        br.save(update_fields=['statut_ic', 'ic_integre', 'fichier_source', 'commande_fournisseur', 'date_validation'])

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


def _iter_csv_rows(chemin_fichier):
    """
    Itère sur les lignes d'un CSV avec détection simple du séparateur.
    Supporte utf-8/utf-8-sig et latin-1 en fallback.
    """
    last_error = None
    for encoding in ('utf-8-sig', 'utf-8', 'latin-1'):
        try:
            with open(chemin_fichier, 'r', encoding=encoding) as f:
                first_line = f.readline()
                delimiter = ';' if ';' in first_line else ','
                f.seek(0)
                reader = csv.DictReader(f, delimiter=delimiter)
                for row in reader:
                    yield {str(k).lstrip('\ufeff').strip(): v for k, v in row.items()}
            return
        except UnicodeDecodeError as e:
            last_error = e
            continue
    if last_error:
        print(f"Erreur encodage CSV {chemin_fichier}: {last_error}")


def _iter_excel_rows(chemin_fichier):
    """
    Itère sur les lignes d'un Excel en essayant de détecter l'en-tête.
    """
    try:
        xl = pd.ExcelFile(chemin_fichier)
    except Exception as e:
        print(f"Erreur lecture Excel {chemin_fichier}: {e}")
        return

    for sheet_name in xl.sheet_names:
        try:
            df = pd.read_excel(chemin_fichier, sheet_name=sheet_name, header=None)
        except Exception as e:
            print(f"Erreur feuille {sheet_name} ({chemin_fichier}): {e}")
            continue

        if df.empty:
            continue

        # Détecter une ligne d'en-têtes dans les 20 premières lignes
        header_found = False
        for idx in range(min(20, len(df))):
            row = df.iloc[idx]
            if row.isna().all():
                continue
            valeurs = [str(v).strip().lower() if pd.notna(v) else '' for v in row.values]
            has_rec = any('réc' in v or 'rec' in v for v in valeurs)
            has_cde = any('cde' in v for v in valeurs)
            has_date = any('date' in v for v in valeurs)
            if has_rec and has_cde and has_date:
                df.columns = [str(v).strip() if pd.notna(v) else f'Col_{i}' for i, v in enumerate(row.values)]
                df = df.iloc[idx + 1:].reset_index(drop=True)
                header_found = True
                break

        if not header_found:
            # Fallback : utiliser la première ligne non vide comme en-tête
            for idx in range(min(20, len(df))):
                row = df.iloc[idx]
                if row.isna().all():
                    continue
                df.columns = [str(v).strip() if pd.notna(v) else f'Col_{i}' for i, v in enumerate(row.values)]
                df = df.iloc[idx + 1:].reset_index(drop=True)
                header_found = True
                break

        if not header_found:
            continue

        for _, row in df.iterrows():
            if row.isna().all():
                continue
            yield {str(k).lstrip('\ufeff').strip(): v for k, v in row.items()}


def comparer_br_asten_ic():
    """
    Compare les BR Asten avec les BR IC (CSV) sur :
    - N° bon livraison = N° Réc./Ret.
    - Commande fournisseur = N° Cde
    - Date création = Date Réc./Ret. (date sans heure)
    Met à jour ic_integre/statut_ic sur BRAsten.
    """
    dossier_br_ic = Path(settings.DOSSIER_BR_IC_PATH)
    if not dossier_br_ic.exists():
        print(f"Dossier BR IC introuvable: {dossier_br_ic}")
        return {'updated_integre': 0, 'updated_non_integre': 0, 'error': 'dossier_introuvable'}

    fichiers_ic = (
        list(dossier_br_ic.glob('*.csv')) + list(dossier_br_ic.glob('*.CSV')) +
        list(dossier_br_ic.glob('*.xlsx')) + list(dossier_br_ic.glob('*.XLSX')) +
        list(dossier_br_ic.glob('*.xls')) + list(dossier_br_ic.glob('*.XLS'))
    )
    if not fichiers_ic:
        return {'updated_integre': 0, 'updated_non_integre': 0, 'error': 'aucun_fichier'}

    ic_keys = set()
    for fichier in fichiers_ic:
        if fichier.suffix.lower() in ('.xlsx', '.xls'):
            row_iter = _iter_excel_rows(str(fichier))
        else:
            row_iter = _iter_csv_rows(str(fichier))
        for row in row_iter:
            try:
                row_normalized = {str(k).lstrip('\ufeff').strip(): v for k, v in row.items()}
                numero_br = normalize_numero_br(get_valeur_premiere_normalized(
                    row_normalized,
                    [
                        'N° Réc./Ret.', 'N° Réc./Ret', 'N° Rec./Ret.', 'N° Rec./Ret',
                        'N° Réception', 'N° Rec/Ret', 'N° Rec/Ret.',
                        'N° Reception', 'N° Reception/Retours', 'N° Receptions Retours',
                        'N° Réceptions/Retours', 'N° Réceptions Retours', 'N° Rec. Ret.'
                    ]
                ))
                commande_fournisseur = normalize_commande_fournisseur(get_valeur_premiere_normalized(
                    row_normalized,
                    ['N° Cde', 'N° Cde.', 'N° Cde fournisseur', 'Commande fournisseur', 'N° Commande']
                ))
                date_rec_str = get_valeur_premiere_normalized(
                    row_normalized,
                    [
                        'Date Réc./Ret.', 'Date Réc./Ret', 'Date Rec./Ret.', 'Date Rec./Ret',
                        'Date Réception', 'Date Rec/Ret', 'Date creation', 'Date création',
                        'Date Réceptions/Retours', 'Date Receptions Retours'
                    ]
                )
                date_rec = parse_date_br(date_rec_str)
                if numero_br and commande_fournisseur and date_rec:
                    ic_keys.add((numero_br, commande_fournisseur, date_rec))
            except Exception as e:
                print(f"Erreur lecture BR IC {fichier.name}: {e}")
                continue

    if not ic_keys:
        return {'updated_integre': 0, 'updated_non_integre': 0, 'error': 'aucune_cle'}

    ids_integres = []
    ids_non_integres = []
    br_qs = BRAsten.objects.only(
        'id', 'numero_br', 'date_br', 'commande_fournisseur', 'ic_integre', 'statut_ic', 'override_statut_ic'
    )
    for br in br_qs.iterator():
        if getattr(br, 'override_statut_ic', False):
            continue
        if not br.numero_br or not br.date_br or not br.commande_fournisseur:
            continue
        key = (
            normalize_numero_br(br.numero_br),
            normalize_commande_fournisseur(br.commande_fournisseur),
            br.date_br
        )
        is_integre = key in ic_keys
        if is_integre:
            if not br.ic_integre or br.statut_ic != 'Intégré':
                ids_integres.append(br.id)
        else:
            if br.ic_integre or br.statut_ic != 'Non intégré':
                ids_non_integres.append(br.id)

    updated_integre = 0
    updated_non_integre = 0
    if ids_integres:
        updated_integre = BRAsten.objects.filter(id__in=ids_integres).update(
            ic_integre=True,
            statut_ic='Intégré'
        )
    if ids_non_integres:
        updated_non_integre = BRAsten.objects.filter(id__in=ids_non_integres).update(
            ic_integre=False,
            statut_ic='Non intégré'
        )

    return {
        'updated_integre': updated_integre,
        'updated_non_integre': updated_non_integre,
        'error': None
    }


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
                nonlocal nombre_lignes, nombre_nouveaux, nombre_dupliques
                nombre_lignes += 1

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
                if not date_commande:
                    # Log pour debug si la date ne peut pas être parsée
                    if dcde_str:
                        print(f"Date Cyrus non parsable: '{dcde_str}' (format attendu: YYMMDD)")
                    return
                if not numero_commande or not code_magasin:
                    return

                # Vérifier que le magasin existe
                try:
                    magasin = Magasin.objects.get(code=code_magasin)
                except Magasin.DoesNotExist:
                    print(f"Magasin '{code_magasin}' non trouvé pour la commande {numero_commande} du {dcde_str}. Ligne ignorée.")
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
                    try:
                        parse_row_cols(header)
                    except Exception as e:
                        print(f"Erreur ligne header: {e}")
                for cols in reader:
                    try:
                        parse_row_cols(cols)
                    except Exception as e:
                        print(f"Erreur ligne: {e}")
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
    # Utiliser les chemins configurables depuis settings
    dossier_asten = Path(settings.DOSSIER_COMMANDES_ASTEN_PATH)
    dossier_cyrus = Path(settings.DOSSIER_COMMANDES_CYRUS_PATH)
    dossier_gpv = Path(settings.DOSSIER_COMMANDES_GPV_PATH)
    dossier_legend = Path(settings.DOSSIER_COMMANDES_LEGEND_PATH)
    dossier_br_asten = Path(settings.DOSSIER_BR_ASTEN_PATH)
    dossier_br_ic = Path(settings.DOSSIER_BR_IC_PATH)
    
    # Créer les dossiers s'ils n'existent pas
    dossier_asten.mkdir(parents=True, exist_ok=True)
    dossier_cyrus.mkdir(parents=True, exist_ok=True)
    dossier_gpv.mkdir(parents=True, exist_ok=True)
    dossier_legend.mkdir(parents=True, exist_ok=True)
    dossier_br_asten.mkdir(parents=True, exist_ok=True)
    dossier_br_ic.mkdir(parents=True, exist_ok=True)
    
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
                # Ne supprimer le fichier que si l'import a réussi ET qu'au moins une ligne a été importée
                if import_obj and import_obj.statut == 'termine' and import_obj.nombre_nouveaux > 0:
                    supprimer_fichier_source(fichier)
                elif import_obj and import_obj.statut == 'termine' and import_obj.nombre_nouveaux == 0:
                    print(f"Attention: Fichier Cyrus {fichier.name} importé avec 0 nouvelles lignes. Fichier conservé pour investigation.")
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

    # Comparer BR Asten vs BR IC (métadonnées uniquement, pas de suppression)
    try:
        comparer_br_asten_ic()
    except Exception as e:
        print(f"Erreur comparaison BR Asten/IC: {e}")

    # Scanner les factures Sage (métadonnées uniquement, pas de suppression)
    try:
        scanner_factures_sage()
    except Exception as e:
        print(f"Erreur scan Facture Sage: {e}")

    # Scanner les factures Backup (Cyrus)
    try:
        scanner_factures_backup()
    except Exception as e:
        print(f"Erreur scan Facture Backup: {e}")

    return fichiers_importes


def get_factures_sage_prefixes():
    """Retourne la liste des préfixes autorisés pour les factures Sage."""
    prefix_config = str(getattr(settings, 'FACTURES_SAGE_PREFIX', '') or '').strip()
    prefixes = [p.strip() for p in prefix_config.split(',') if p.strip()]
    if not prefixes:
        prefixes = ['']
    return prefixes


def scanner_factures_sage():
    """
    Scanne les fichiers Facture Sage (CSV) et stocke uniquement les métadonnées en base.
    Aucun fichier n'est supprimé.
    """
    dossier_factures = Path(settings.DOSSIER_FACTURES_SAGE_PATH)
    # Si le point de montage contient un sous-dossier ARCHIVES, l'utiliser
    if dossier_factures.exists() and (dossier_factures / 'ARCHIVES').exists():
        dossier_factures = dossier_factures / 'ARCHIVES'
    resultats = {'created': 0, 'updated': 0, 'error': None}

    if not dossier_factures.exists():
        resultats['error'] = f"Dossier factures Sage introuvable: {dossier_factures}"
        return resultats

    prefixes = get_factures_sage_prefixes()
    fichiers = list(dossier_factures.glob('*.csv')) + list(dossier_factures.glob('*.CSV'))

    for fichier in fichiers:
        nom_fichier = fichier.name
        if prefixes != [''] and not any(nom_fichier.startswith(p) for p in prefixes):
            continue

        try:
            stat = fichier.stat()
            if settings.USE_TZ:
                date_modif = datetime.fromtimestamp(stat.st_mtime, tz=timezone.get_current_timezone())
            else:
                date_modif = datetime.fromtimestamp(stat.st_mtime)

            date_depot = date_modif.date()
            chemin_fichier = str(fichier)

            # Compter les lignes (y compris l'en-tête si présent)
            try:
                with open(fichier, 'r', encoding='utf-8', errors='ignore') as f:
                    nombre_lignes = sum(1 for _ in f)
            except Exception:
                nombre_lignes = 0

            existing = FactureSage.objects.filter(nom_fichier=nom_fichier).first()
            if not existing:
                FactureSage.objects.create(
                    nom_fichier=nom_fichier,
                    chemin_fichier=chemin_fichier,
                    date_depot=date_depot,
                    date_modif=date_modif,
                    nombre_lignes=nombre_lignes,
                )
                resultats['created'] += 1
            else:
                existing_modif = existing.date_modif
                if settings.USE_TZ and existing_modif and timezone.is_naive(existing_modif):
                    existing_modif = timezone.make_aware(existing_modif, timezone.get_current_timezone())

                changed = (
                    existing.chemin_fichier != chemin_fichier or
                    existing.date_depot != date_depot or
                    existing_modif != date_modif or
                    existing.nombre_lignes != nombre_lignes
                )
                if changed:
                    existing.chemin_fichier = chemin_fichier
                    existing.date_depot = date_depot
                    existing.date_modif = date_modif
                    existing.nombre_lignes = nombre_lignes
                    existing.save(update_fields=['chemin_fichier', 'date_depot', 'date_modif', 'nombre_lignes', 'date_maj'])
                    resultats['updated'] += 1
        except Exception as e:
            resultats['error'] = str(e)

    return resultats


def scanner_factures_backup():
    """
    Scanne les fichiers Facture Backup (Cyrus) et stocke les données en base.
    Lecture uniquement de la première ligne, aucun fichier n'est supprimé.
    """
    dossier_factures = Path(settings.DOSSIER_FACTURE_BACKUP_PATH)
    dossier_factures.mkdir(parents=True, exist_ok=True)
    resultats = {'created': 0, 'skipped': 0, 'invalid': 0, 'errors': 0}

    fichiers = [f for f in dossier_factures.iterdir() if f.is_file()]
    for fichier in fichiers:
        nom_fichier = fichier.name
        if FactureBackupCyrus.objects.filter(nom_fichier=nom_fichier).exists():
            resultats['skipped'] += 1
            continue
        if ImportFichier.objects.filter(type_fichier='facture_backup', nom_fichier=nom_fichier).exists():
            resultats['skipped'] += 1
            continue

        import_obj = ImportFichier.objects.create(
            type_fichier='facture_backup',
            nom_fichier=nom_fichier,
            chemin_fichier=str(fichier),
            statut='en_cours'
        )

        try:
            with open(fichier, 'r', encoding='utf-8', errors='ignore') as f:
                premiere_ligne = f.readline()

            parsed = parse_facture_backup_line(premiere_ligne)
            if not parsed:
                import_obj.statut = 'erreur'
                import_obj.message_erreur = "Ligne invalide ou format inconnu"
                import_obj.save(update_fields=['statut', 'message_erreur', 'date_import'])
                resultats['invalid'] += 1
                print(f"Facture Backup ignorée (ligne invalide): {nom_fichier}")
                continue

            stat = fichier.stat()
            if settings.USE_TZ:
                date_modif = datetime.fromtimestamp(stat.st_mtime, tz=timezone.get_current_timezone())
            else:
                date_modif = datetime.fromtimestamp(stat.st_mtime)

            FactureBackupCyrus.objects.create(
                code_magasin=parsed['code_magasin'],
                numero_facture=parsed['numero_facture'],
                type_facture=parsed['type_facture'],
                theme_promo=parsed['theme_promo'],
                nom_fichier=nom_fichier,
                chemin_fichier=str(fichier),
                date_modif=date_modif,
            )

            import_obj.nombre_lignes = 1
            import_obj.nombre_nouveaux = 1
            import_obj.nombre_dupliques = 0
            import_obj.statut = 'termine'
            import_obj.save(update_fields=[
                'nombre_lignes', 'nombre_nouveaux', 'nombre_dupliques',
                'statut', 'message_erreur', 'date_import'
            ])
            resultats['created'] += 1
            print(f"Facture Backup importée: {nom_fichier}")
        except Exception as e:
            import_obj.statut = 'erreur'
            import_obj.message_erreur = str(e)
            import_obj.save(update_fields=['statut', 'message_erreur', 'date_import'])
            resultats['errors'] += 1
            print(f"Erreur import Facture Backup {nom_fichier}: {e}")

    return resultats


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

