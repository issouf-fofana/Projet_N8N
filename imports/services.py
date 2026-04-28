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
    Retourne un datetime (avec heure) pour conserver les heures.
    """
    if not date_str:
        return None
    date_str = date_str.strip()
    try:
        if ' ' in date_str:
            return datetime.strptime(date_str, '%d/%m/%Y %H:%M')
        return datetime.strptime(date_str, '%d/%m/%Y')
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


def importer_br_ic_en_base(fichier_path):
    """
    Lit un fichier BR IC (CSV ou Excel) et insère les nouvelles lignes dans BRICLigne.
    Dédoublonne sur (numero_br, commande_fournisseur, date_reception).
    Un même fichier peut être déposé plusieurs fois sans créer de doublons.
    Retourne le nombre de lignes nouvellement insérées.
    """
    from br.models import BRICLigne
    fichier = Path(fichier_path)
    if not fichier.exists():
        return 0

    if fichier.suffix.lower() in ('.xlsx', '.xls'):
        row_iter = _iter_excel_rows(str(fichier))
    else:
        row_iter = _iter_csv_rows(str(fichier))

    nouvelles = []
    cles_vues = set()

    for row in row_iter:
        try:
            row_n = {str(k).lstrip('\ufeff').strip(): v for k, v in row.items()}
            numero_br = normalize_numero_br(get_valeur_premiere_normalized(
                row_n,
                ['N° Réc./Ret.', 'N° Réc./Ret', 'N° Rec./Ret.', 'N° Rec./Ret',
                 'N° Réception', 'N° Rec/Ret', 'N° Rec/Ret.',
                 'N° Reception', 'N° Reception/Retours', 'N° Receptions Retours',
                 'N° Réceptions/Retours', 'N° Réceptions Retours', 'N° Rec. Ret.']
            ))
            commande_fournisseur = normalize_commande_fournisseur(get_valeur_premiere_normalized(
                row_n,
                ['N° Cde', 'N° Cde.', 'N° Cde fournisseur', 'Commande fournisseur', 'N° Commande']
            ))
            date_rec_str = get_valeur_premiere_normalized(
                row_n,
                ['Date Réc./Ret.', 'Date Réc./Ret', 'Date Rec./Ret.', 'Date Rec./Ret',
                 'Date Réception', 'Date Rec/Ret', 'Date creation', 'Date création',
                 'Date Réceptions/Retours', 'Date Receptions Retours']
            )
            date_rec = parse_date_br(date_rec_str)
            if not (numero_br and commande_fournisseur and date_rec):
                continue
            cle = (numero_br, commande_fournisseur, date_rec)
            if cle in cles_vues:
                continue
            cles_vues.add(cle)
            nouvelles.append(BRICLigne(
                numero_br=numero_br,
                commande_fournisseur=commande_fournisseur,
                date_reception=date_rec,
                fichier_source=fichier.name,
            ))
        except Exception as e:
            print(f"[BR IC] Erreur ligne {fichier.name}: {e}")
            continue

    created = BRICLigne.objects.bulk_create(nouvelles, ignore_conflicts=True)
    return len(created)


def comparer_br_asten_ic():
    """
    Compare les BRAsten avec les BRICLigne stockées en base.
    Clé de jointure : (commande_fournisseur + numero_br)
    La date n'est pas utilisée pour le matching — elle peut différer entre Asten et IC.
    Met à jour ic_integre/statut_ic sur BRAsten.
    """
    from br.models import BRICLigne

    # Clé principale : (numero_br, commande_fournisseur) — sans date
    ic_keys = set()
    # Clé fallback : commande_fournisseur → nouveau numero_br IC (BR corrigé)
    ic_commande_to_br: dict = {}

    for numero_br, commande_fournisseur in BRICLigne.objects.values_list('numero_br', 'commande_fournisseur'):
        cf = normalize_commande_fournisseur(commande_fournisseur)
        nb = normalize_numero_br(numero_br)
        if cf and nb and cf.upper() != 'NAN':
            ic_keys.add((nb, cf))
            ic_commande_to_br[cf] = nb  # dernier numero_br IC pour cette commande

    if not ic_keys:
        return {'updated_integre': 0, 'updated_non_integre': 0, 'error': 'aucune_cle'}

    ids_integres = []
    ids_integres_corriges = []  # list of (id, nouveau_numero_br)
    ids_non_integres = []
    br_qs = BRAsten.objects.only(
        'id', 'numero_br', 'commande_fournisseur', 'ic_integre', 'statut_ic',
        'override_statut_ic', 'numero_br_corrige'
    )
    for br in br_qs.iterator():
        if not br.numero_br or not br.commande_fournisseur:
            continue
        nb = normalize_numero_br(br.numero_br)
        cf = normalize_commande_fournisseur(br.commande_fournisseur)
        key = (nb, cf)

        # Override respecté seulement si déjà intégré manuellement —
        # si IC prouve que c'est intégré, on écrase toujours le statut manuel "Non intégré"
        override = getattr(br, 'override_statut_ic', False)

        if key in ic_keys:
            # Étape 1 — match exact : IC a raison, on écrase même l'override
            if not br.ic_integre or br.statut_ic != 'Intégré':
                ids_integres.append(br.id)
        elif cf in ic_commande_to_br:
            # Étape 2 — même commande, BR différent : IC a raison, on écrase même l'override
            nouveau_br = ic_commande_to_br[cf]
            if not br.ic_integre or br.statut_ic != 'Intégré (corrigé)' or br.numero_br_corrige != nouveau_br:
                ids_integres_corriges.append((br.id, nouveau_br))
        else:
            # Étape 3 — absent de IC : on respecte l'override si présent
            if override:
                continue
            if br.ic_integre or br.statut_ic != 'Non intégré':
                ids_non_integres.append(br.id)

    updated_integre = 0
    updated_non_integre = 0
    if ids_integres:
        updated_integre = BRAsten.objects.filter(id__in=ids_integres).update(
            ic_integre=True, statut_ic='Intégré', numero_br_corrige=None
        )
    if ids_integres_corriges:
        for br_id, nouveau_br in ids_integres_corriges:
            BRAsten.objects.filter(id=br_id).update(
                ic_integre=True, statut_ic='Intégré (corrigé)', numero_br_corrige=nouveau_br
            )
        updated_integre += len(ids_integres_corriges)
    if ids_non_integres:
        updated_non_integre = BRAsten.objects.filter(id__in=ids_non_integres).update(
            ic_integre=False, statut_ic='Non intégré', numero_br_corrige=None
        )

    return {
        'updated_integre': updated_integre,
        'updated_non_integre': updated_non_integre,
        'nb_corriges': len(ids_integres_corriges),
        'error': None
    }


def importer_anomalies_br_en_base(fichier_path):
    """
    Lit un fichier CSV Anomalies BR et insère les nouvelles lignes dans BRAnomalie.
    Pas de doublon grâce à unique_together (numero_br, code_magasin, date_reception).
    Retourne le nombre de nouvelles lignes insérées.
    """
    from br.models import BRAnomalie
    import io as _io
    from datetime import datetime

    fichier_path = Path(fichier_path)
    nom_fichier = fichier_path.name

    content = None
    for enc in ('utf-8-sig', 'cp850', 'latin-1', 'cp1252'):
        try:
            with open(str(fichier_path), encoding=enc, errors='strict') as f:
                content = f.read()
            break
        except (UnicodeDecodeError, LookupError):
            continue
    if content is None:
        with open(str(fichier_path), encoding='utf-8', errors='replace') as f:
            content = f.read()

    reader = csv.DictReader(_io.StringIO(content), delimiter=';')
    nouvelles = []
    seen = set()

    for row in reader:
        numero_br  = str(row.get('Nr bon reception', '') or '').strip()
        code_mag   = str(row.get('Nr de site', '') or '').strip()
        if not numero_br or not code_mag:
            continue

        # Parser la date
        date_rec = None
        date_raw = str(row.get('Date recept/retour', '') or '').strip()
        for fmt in ('%d/%m/%Y', '%Y-%m-%d', '%d-%m-%Y'):
            try:
                date_rec = datetime.strptime(date_raw, fmt).date()
                break
            except (ValueError, TypeError):
                continue

        key = (numero_br, code_mag, date_rec)
        if key in seen:
            continue
        seen.add(key)

        # Montant
        montant = None
        montant_raw = str(row.get('Montant HT Total', '') or '').strip().replace(',', '.')
        try:
            if montant_raw:
                montant = float(montant_raw)
        except ValueError:
            pass

        nouvelles.append(BRAnomalie(
            numero_br         = numero_br,
            code_magasin      = code_mag,
            date_reception    = date_rec,
            nom_fichier_integ = str(row.get('Nom de fichier integ', '') or '').strip() or None,
            type_mouvement    = str(row.get('1=BR,2=RETOUR', '') or '').strip() or None,
            fournisseur       = str(row.get('Fournisseur', '') or '').strip() or None,
            montant_ht        = montant,
            rejet_csm_entete  = str(row.get('Rejet CSM entete', '') or '').strip() or None,
            rejet_csm_detail  = str(row.get('Rejet CSM detail art', '') or '').strip() or None,
            rejet_ic_entete   = str(row.get('Rejet IC Entete', '') or '').strip() or None,
            facture           = str(row.get('Facture OUI/NON', '') or '').strip() or None,
            fichier_source    = nom_fichier,
        ))

    if not nouvelles:
        return 0

    avant = BRAnomalie.objects.count()
    BRAnomalie.objects.bulk_create(nouvelles, ignore_conflicts=True)
    return BRAnomalie.objects.count() - avant


def sync_anomalies_br():
    """
    Rapproche BRAnomalie (table DB) avec BRAsten :
    met à jour les champs anomalie sur les BRAsten correspondants.
    Source : BRAnomalie en base (plus de lecture fichier ici).
    """
    from br.models import BRAnomalie

    # Réinitialiser les BR qui étaient en anomalie
    BRAsten.objects.filter(en_anomalie=True).update(
        en_anomalie=False,
        nom_fichier_integ=None, type_mouvement=None,
        fournisseur_anomalie=None, montant_ht_anomalie=None,
        rejet_csm_entete=None, rejet_csm_detail=None,
        rejet_ic_entete=None, facture_anomalie=None,
    )

    anomalies = BRAnomalie.objects.values(
        'numero_br', 'code_magasin', 'nom_fichier_integ', 'type_mouvement',
        'fournisseur', 'montant_ht', 'rejet_csm_entete', 'rejet_csm_detail',
        'rejet_ic_entete', 'facture',
    )

    if not anomalies:
        return {'synced': 0, 'not_found': 0, 'error': 'aucune_anomalie_en_base'}

    synced = 0
    not_found = 0

    for a in anomalies:
        qs = BRAsten.objects.filter(numero_br=a['numero_br'], code_magasin__code=a['code_magasin'])
        if not qs.exists():
            not_found += 1
            continue
        updated = qs.update(
            en_anomalie=True,
            nom_fichier_integ    = a['nom_fichier_integ'],
            type_mouvement       = a['type_mouvement'],
            fournisseur_anomalie = a['fournisseur'],
            montant_ht_anomalie  = a['montant_ht'],
            rejet_csm_entete     = a['rejet_csm_entete'],
            rejet_csm_detail     = a['rejet_csm_detail'],
            rejet_ic_entete      = a['rejet_ic_entete'],
            facture_anomalie     = a['facture'],
        )
        synced += updated

    return {'synced': synced, 'not_found': not_found, 'error': None}


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
                    
                    date_livraison = parse_date_asten(date_livraison_str) if date_livraison_str else None
                    date_validation = parse_date_asten(date_validation_str) if date_validation_str else None

                    # Créer ou mettre à jour la commande
                    commande, created = CommandeAsten.objects.get_or_create(
                        date_commande=date_commande,
                        numero_commande=numero_commande,
                        code_magasin=magasin,
                        defaults={
                            'montant':           montant,
                            'statut':            statut,
                            'date_validation':   date_validation,
                            'date_livraison':    date_livraison,
                            'reference_externe': reference_externe,
                            'fournisseur':       fournisseur,
                            'cree_par':          cree_par,
                            'validee_par':       validee_par,
                            'fichier_source':    nom_fichier,
                        }
                    )
                    if not created:
                        # Mettre à jour les champs si la commande existe déjà
                        updated = False
                        for field, val in [
                            ('statut', statut), ('date_validation', date_validation),
                            ('date_livraison', date_livraison), ('fournisseur', fournisseur),
                            ('validee_par', validee_par), ('cree_par', cree_par),
                            ('reference_externe', reference_externe),
                        ]:
                            if val and getattr(commande, field) != val:
                                setattr(commande, field, val)
                                updated = True
                        if updated:
                            commande.save()
                    
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

                date_reception = parse_date_cyrus(dcre_str) if dcre_str else None
                type_commande = tycm or None
                statut = None  # TYCM est maintenant stocké dans type_commande

                commande, created = CommandeCyrus.objects.get_or_create(
                    date_commande=date_commande,
                    numero_commande=numero_commande,
                    code_magasin=magasin,
                    defaults={
                        'date_reception': date_reception,
                        'nom_magasin': nom_magasin,
                        'type_commande': type_commande,
                        'montant': montant,
                        'statut': statut,
                        'fichier_source': nom_fichier,
                    }
                )
                if not created:
                    updated = False
                    if date_reception and not commande.date_reception:
                        commande.date_reception = date_reception; updated = True
                    if nom_magasin and not commande.nom_magasin:
                        commande.nom_magasin = nom_magasin; updated = True
                    if type_commande and not commande.type_commande:
                        commande.type_commande = type_commande; updated = True
                    if updated:
                        commande.save(update_fields=['date_reception', 'nom_magasin', 'type_commande'])
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
                    if import_obj.nombre_nouveaux > 0:
                        print(f"[Asten] {fichier.name} → {import_obj.nombre_nouveaux} nouvelles lignes importées")
                    else:
                        print(f"[Asten] {fichier.name} → 0 nouvelles lignes (déjà en base), fichier supprimé.")
                    supprimer_fichier_source(fichier)
                elif import_obj:
                    print(f"[Asten] {fichier.name} → Erreur : {import_obj.statut}")
            elif import_existant and import_existant.statut == 'termine':
                supprimer_fichier_source(fichier)
        except Exception as e:
            print(f"Erreur import fichier Asten {fichier.name}: {e}")

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
                    if import_obj.nombre_nouveaux > 0:
                        print(f"[Cyrus] {fichier.name} → {import_obj.nombre_nouveaux} nouvelles lignes importées")
                    else:
                        print(f"[Cyrus] {fichier.name} → 0 nouvelles lignes (déjà en base), fichier supprimé.")
                    supprimer_fichier_source(fichier)
                elif import_obj:
                    print(f"[Cyrus] {fichier.name} → Erreur : {import_obj.statut}")
            elif import_existant and import_existant.statut == 'termine':
                supprimer_fichier_source(fichier)
        except Exception as e:
            print(f"Erreur import fichier Cyrus {fichier.name}: {e}")
    
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
                    if import_obj.nombre_nouveaux > 0:
                        print(f"[GPV] {fichier.name} → {import_obj.nombre_nouveaux} nouvelles lignes importées")
                    else:
                        print(f"[GPV] {fichier.name} → 0 nouvelles lignes (déjà en base), fichier supprimé.")
                    supprimer_fichier_source(fichier)
                elif import_obj:
                    print(f"[GPV] {fichier.name} → Erreur : {import_obj.statut}")
            elif import_existant and import_existant.statut == 'termine':
                supprimer_fichier_source(fichier)
        except Exception as e:
            print(f"Erreur import fichier GPV {fichier.name}: {e}")

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
                    if import_obj.nombre_nouveaux > 0:
                        print(f"[Legend] {fichier.name} → {import_obj.nombre_nouveaux} nouvelles lignes importées")
                    else:
                        print(f"[Legend] {fichier.name} → 0 nouvelles lignes (déjà en base), fichier supprimé.")
                    supprimer_fichier_source(fichier)
                elif import_obj:
                    print(f"[Legend] {fichier.name} → Erreur : {import_obj.statut}")
            elif import_existant and import_existant.statut == 'termine':
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
                if import_obj and import_obj.statut == 'termine':
                    if import_obj.nombre_lignes > 0:
                        print(f"[BR Asten] {fichier.name} → {import_obj.nombre_lignes} lignes importées")
                    else:
                        print(f"[BR Asten] {fichier.name} → 0 nouvelles lignes (déjà en base), fichier supprimé.")
                    supprimer_fichier_source(fichier)
                elif import_obj:
                    print(f"[BR Asten] {fichier.name} → Erreur : {import_obj.statut}")
            elif import_existant and import_existant.statut == 'termine':
                supprimer_fichier_source(fichier)
        except Exception as e:
            print(f"Erreur import fichier BR Asten {fichier.name}: {e}")
            # Marquer l'import comme erreur pour éviter les tentatives répétées
            try:
                imp = ImportFichier.objects.filter(type_fichier='br_asten', nom_fichier=fichier.name, statut='en_cours').first()
                if imp:
                    imp.statut = 'erreur'
                    imp.save()
            except Exception:
                pass

    # Importer les fichiers BR IC en base puis comparer avec BRAsten
    try:
        fichiers_ic = (
            list(dossier_br_ic.glob('*.csv')) + list(dossier_br_ic.glob('*.CSV')) +
            list(dossier_br_ic.glob('*.xlsx')) + list(dossier_br_ic.glob('*.XLSX')) +
            list(dossier_br_ic.glob('*.xls')) + list(dossier_br_ic.glob('*.XLS'))
        )
        for f_ic in fichiers_ic:
            try:
                import_obj = ImportFichier.objects.create(
                    type_fichier='br_ic',
                    nom_fichier=f_ic.name,
                    chemin_fichier=str(f_ic),
                    statut='en_cours',
                )
                nb = importer_br_ic_en_base(str(f_ic))
                import_obj.nombre_lignes = nb
                import_obj.nombre_nouveaux = nb
                import_obj.statut = 'termine'
                import_obj.save(update_fields=['nombre_lignes', 'nombre_nouveaux', 'statut'])
                print(f"[BR IC] {f_ic.name} → {nb} nouvelles lignes en base")
                supprimer_fichier_source(f_ic)
            except Exception as e:
                if 'import_obj' in dir():
                    import_obj.statut = 'erreur'
                    import_obj.save(update_fields=['statut'])
                print(f"Erreur import BR IC {f_ic.name}: {e}")
        comparer_br_asten_ic()
    except Exception as e:
        print(f"Erreur traitement BR IC: {e}")

    # Importer les anomalies BR en base puis synchroniser avec BRAsten
    try:
        dossier_anomalie = Path(settings.DOSSIER_ANOMALIE_BR_PATH)
        fichiers_anomalie = (
            list(dossier_anomalie.glob('*.csv')) + list(dossier_anomalie.glob('*.CSV'))
        ) if dossier_anomalie.exists() else []
        for f_anom in fichiers_anomalie:
            try:
                nb = importer_anomalies_br_en_base(str(f_anom))
                print(f"[BR Anomalie] {f_anom.name} → {nb} nouvelles lignes en base")
                supprimer_fichier_source(f_anom)
            except Exception as e:
                print(f"Erreur import BR Anomalie {f_anom.name}: {e}")
        sync_anomalies_br()
    except Exception as e:
        print(f"Erreur traitement anomalies BR: {e}")

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

    # Importer les fichiers Entrée Journal (POS)
    try:
        from entree_journal.services import importer_fichiers as importer_entree_journal
        importer_entree_journal()
    except Exception as e:
        print(f"Erreur import Entrée Journal: {e}")

    # Importer factures Cyrus et Asten en base de données
    try:
        importer_factures_cyrus_en_base()
    except Exception as e:
        print(f"Erreur import factures Cyrus en base: {e}")
    try:
        importer_factures_asten_en_base()
    except Exception as e:
        print(f"Erreur import factures Asten en base: {e}")

    # Rafraîchir la vue matérialisée + vider le cache factures
    try:
        from django.db import connection as _conn
        from django.core.cache import cache as _cache
        with _conn.cursor() as _cur:
            _cur.execute('REFRESH MATERIALIZED VIEW mv_factures_joined')
        _cache.delete('factures_verification_v1')
        _cache.delete('factures_stats_sql_v1')
        print("[MV] mv_factures_joined rafraîchie, cache vidé")
    except Exception as e:
        print(f"Erreur refresh vue matérialisée: {e}")

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



# ─────────────────────────────────────────────
#  Versions Asten — scan backups prdP2A
# ─────────────────────────────────────────────
_VERSION_PATTERN   = re.compile(r'^prdP2A_(\d{4}-\d{2}-\d{2})_(\d{2}h\d{2}m\d{2}s)$')
_FICHIERS_REQUIS      = ['linkedcodes', 'pricing', 'products', 'productssuppliers']
_FICHIERS_OPTIONNELS  = ['precommande']  # présent mais n'influence pas la conformité
_FICHIERS_TOUS        = _FICHIERS_REQUIS + _FICHIERS_OPTIONNELS  # pour l'affichage complet
_FICHIERS_GLOBAUX     = ['departement', 'linkedcodes', 'pricing', 'products', 'suppliers']
# precommande = .txt  |  tous les autres = .csv
_FICHIERS_TXT         = {'precommande'}
_NB_FICHIERS_MIN      = 72   # 18 assortiments × 4 fichiers obligatoires
_NB_FICHIERS_MAX      = 95   # + fichiers optionnels et 999
_HEURE_CIBLE          = '23' # heure de génération attendue
# Préfixes de fichiers globaux (ex: Cmdpro_0186.txt, Pricing_0186.txt) :
# ce sont des fichiers par magasin, PAS des assortiments — ignorés des écarts
_PREFIXES_FICHIERS_GLOBAUX = {'Cmdpro', 'Pricing'}

FILE_TYPE_LABELS = {
    'linkedcodes':       'Linked Code',
    'pricing':           'Price Updater',
    'products':          'Product',
    'productssuppliers': 'Product Suppliers',
    'precommande':       'Pre-order',
    'departement':       'departement',
    'suppliers':         'suppliers',
}


def _fmt_size(nb):
    if nb < 1024:
        return f"{nb} o"
    if nb < 1024 * 1024:
        return f"{nb / 1024:.1f} Ko"
    return f"{nb / 1024 / 1024:.1f} Mo"


def _load_assortiments():
    """Charge la liste des codes assortiment depuis assortiment.txt."""
    path = Path(settings.BASE_DIR) / 'assortiment.txt'
    if not path.exists():
        return []
    return [l.strip() for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]


def _parse_version_dir(entry, assortiments_attendus, today):
    """
    Analyse un dossier de version et retourne un dict de stats + conformité.
    Retourne None si la version ne correspond pas au format ou à l'heure 23h.
    """
    from datetime import timedelta
    m = _VERSION_PATTERN.match(entry.name)
    if not m:
        return None

    date_str, time_str = m.group(1), m.group(2)
    heure = time_str[:2]

    # Ignorer les versions hors 23h
    if heure != _HEURE_CIBLE:
        return None

    try:
        date = datetime.strptime(date_str, '%Y-%m-%d').date()
    except ValueError:
        return None

    heure_fmt = f"{time_str[:2]}:{time_str[3:5]}:{time_str[6:8]}"
    delta = (today - date).days

    # Scanner les fichiers CSV + TXT (precommande)
    all_files  = list(entry.glob('*.csv')) + list(entry.glob('*.txt'))
    nb_total   = len(all_files)
    total_size = sum(f.stat().st_size for f in all_files)

    # Détecter les assortiments présents (hors 999)
    assortiments_presents = {}
    for f in all_files:
        parts = f.name.split('_')
        code  = parts[0]
        ftype = parts[1] if len(parts) > 1 else ''
        # Normaliser : retirer l'extension du ftype si présente
        ftype = ftype.split('.')[0]
        if code == '999' or code in _PREFIXES_FICHIERS_GLOBAUX:
            continue
        if code not in assortiments_presents:
            assortiments_presents[code] = set()
        assortiments_presents[code].add(ftype)

    # Vérification conformité par assortiment
    assortiments_ok      = []
    assortiments_warning = []   # présents mais fichiers manquants
    assortiments_manquants = [] # absents du dossier

    for code in assortiments_attendus:
        if code in assortiments_presents:
            fichiers_presents = assortiments_presents[code]
            manquants = [f for f in _FICHIERS_REQUIS if f not in fichiers_presents]
            if manquants:
                assortiments_warning.append({'code': code, 'manquants': manquants})
            else:
                assortiments_ok.append(code)  # les 4 obligatoires présents = OK
        else:
            assortiments_manquants.append(code)

    # Assortiments non attendus (présents mais pas dans la liste)
    assortiments_extra = []
    for _code in assortiments_presents:
        if _code in assortiments_attendus:
            continue
        _fichiers_trouves = sorted(assortiments_presents[_code])
        assortiments_extra.append({
            'code':    _code,
            'fichiers': _fichiers_trouves,
            'labels':  [FILE_TYPE_LABELS.get(f, f) for f in _fichiers_trouves],
        })

    nb_obligatoires = len(assortiments_attendus) * len(_FICHIERS_REQUIS)
    nb_conformes    = len(assortiments_ok) * len(_FICHIERS_REQUIS)
    conformite_pct  = round(nb_conformes / nb_obligatoires * 100) if nb_obligatoires else 0

    # Statut global
    if assortiments_manquants or assortiments_warning:
        statut = 'error' if assortiments_manquants else 'warning'
    elif nb_total < _NB_FICHIERS_MIN:
        statut = 'warning'
    else:
        statut = 'ok'

    return {
        'name':                   entry.name,
        'date':                   date,
        'date_str':               date_str,
        'heure':                  heure_fmt,
        'delta_jours':            delta,
        'is_today':               delta == 0,
        'is_hier':                delta == 1,
        'nb_total':               nb_total,
        'nb_obligatoires':        len(assortiments_attendus) * len(_FICHIERS_REQUIS),
        'nb_attendu_max':         _NB_FICHIERS_MAX,
        'nb_assortiments':        len(assortiments_presents),
        'taille_fmt':             _fmt_size(total_size),
        'taille_raw':             total_size,
        'statut':                 statut,
        'conformite_pct':         conformite_pct,
        'assortiments_ok':        assortiments_ok,
        'assortiments_warning':   assortiments_warning,
        'assortiments_manquants': assortiments_manquants,
        'assortiments_extra':     assortiments_extra,
    }


def get_versions_asten(limit=None, filtre_jours=None, date_debut=None, date_fin=None):
    """
    Retourne la liste des versions prdP2A générées à 23h uniquement.
    Essaie d'abord le SMB (lecture directe + sync auto si nouvelles versions),
    sinon repli sur la base de données.
    {'disponible': bool, 'versions': list, 'stats': dict, 'assortiments': list}
    """
    from datetime import date as date_cls, timedelta
    backup_dir   = Path(settings.DOSSIER_VERSIONS_ASTEN_PATH)
    assortiments = _load_assortiments()

    smb_ok = backup_dir.exists()
    if smb_ok:
        try:
            list(backup_dir.iterdir())  # teste l'accès
        except PermissionError:
            smb_ok = False

    # SMB indisponible → repli sur DB
    if not smb_ok:
        from imports.models import VersionAstenSnap
        if VersionAstenSnap.objects.exists():
            result = get_versions_asten_from_db(
                limit=limit, filtre_jours=filtre_jours,
                date_debut=date_debut, date_fin=date_fin
            )
            result['disponible'] = False   # SMB absent, données depuis DB
            result['source']     = 'db_fallback'
            return result
        return {'disponible': False, 'versions': [], 'stats': {}, 'assortiments': assortiments}

    # SMB disponible : sync automatique des nouvelles versions (non bloquant)
    try:
        sync_versions_to_db(force=False)
    except Exception:
        pass  # la sync est optionnelle, ne bloque pas l'affichage

    today    = datetime.today().date()
    versions = []

    try:
        entries = list(backup_dir.iterdir())
    except PermissionError:
        return {'disponible': False, 'versions': [], 'stats': {}, 'assortiments': assortiments}

    for entry in entries:
        if not entry.is_dir():
            continue
        if filtre_jours or date_debut or date_fin:
            m = _VERSION_PATTERN.match(entry.name)
            if m:
                try:
                    d = datetime.strptime(m.group(1), '%Y-%m-%d').date()
                    if filtre_jours and (today - d).days > filtre_jours:
                        continue
                    if date_debut and d < date_debut:
                        continue
                    if date_fin and d > date_fin:
                        continue
                except ValueError:
                    continue

        v = _parse_version_dir(entry, assortiments, today)
        if v:
            versions.append(v)

    versions.sort(key=lambda v: (v['date'], v['heure']), reverse=True)
    if limit:
        versions = versions[:limit]

    # Streak jours consécutifs (23h OK)
    dates_set = {v['date'] for v in versions}
    streak = 0
    d = today
    while d in dates_set:
        streak += 1
        d = d - timedelta(days=1)

    nb_ok      = sum(1 for v in versions if v['statut'] == 'ok')
    nb_warning = sum(1 for v in versions if v['statut'] == 'warning')
    nb_error   = sum(1 for v in versions if v['statut'] == 'error')

    stats = {
        'total':          len(versions),
        'nb_ok':          nb_ok,
        'nb_warning':     nb_warning,
        'nb_error':       nb_error,
        'derniere':       versions[0]['date'] if versions else None,
        'delta_derniere': versions[0]['delta_jours'] if versions else None,
        'statut_derniere':versions[0]['statut'] if versions else None,
        'streak':         streak,
        'taille_totale':  _fmt_size(sum(v['taille_raw'] for v in versions)),
        'nb_assortiments_attendus': len(assortiments),
    }

    return {'disponible': True, 'versions': versions, 'stats': stats, 'assortiments': assortiments}


def get_version_asten_detail(version_name):
    """
    Retourne le détail complet d'une version prdP2A avec :
    - Conformité par assortiment
    - Nombre d'articles par fichier
    - Fichiers manquants
    """
    backup_dir  = Path(settings.DOSSIER_VERSIONS_ASTEN_PATH)
    version_dir = backup_dir / version_name

    if not version_dir.exists() or not _VERSION_PATTERN.match(version_name):
        return None

    assortiments_attendus = _load_assortiments()
    today = datetime.today().date()

    m = _VERSION_PATTERN.match(version_name)
    date_str, time_str = m.group(1), m.group(2)
    date  = datetime.strptime(date_str, '%Y-%m-%d').date()
    heure = f"{time_str[:2]}:{time_str[3:5]}:{time_str[6:8]}"

    all_files  = list(version_dir.glob('*.csv')) + list(version_dir.glob('*.txt'))
    csv_files  = list(version_dir.glob('*.csv'))
    total_size = sum(f.stat().st_size for f in csv_files)

    # Index fichiers par code assortiment (ignoré les préfixes globaux Cmdpro/Pricing)
    fichiers_par_code = {}
    for f in csv_files:
        parts = f.name.split('_')
        code  = parts[0]
        if code in _PREFIXES_FICHIERS_GLOBAUX:
            continue
        ftype = parts[1] if len(parts) > 1 else ''
        if code not in fichiers_par_code:
            fichiers_par_code[code] = {}
        try:
            nb_lignes = sum(1 for _ in open(f, encoding='utf-8', errors='replace'))
        except Exception:
            nb_lignes = 0
        fichiers_par_code[code][ftype] = {
            'nom':      f.name,
            'label':    FILE_TYPE_LABELS.get(ftype, ftype),
            'nb_lignes': nb_lignes,
            'taille':   _fmt_size(f.stat().st_size),
        }

    # Construire la liste des assortiments avec statut
    assortiments_detail = []
    for code in assortiments_attendus:
        fichiers = fichiers_par_code.get(code, {})
        conformite = []
        for ftype in _FICHIERS_TOUS:
            optionnel = ftype in _FICHIERS_OPTIONNELS
            if ftype in fichiers:
                conformite.append({'type': ftype, 'label': FILE_TYPE_LABELS[ftype],
                                   'present': True, 'optionnel': optionnel, **fichiers[ftype]})
            else:
                conformite.append({'type': ftype, 'label': FILE_TYPE_LABELS[ftype],
                                   'present': False, 'optionnel': optionnel, 'nb_lignes': 0, 'taille': '—'})
        # Seuls les fichiers obligatoires comptent pour le statut
        nb_manquants = sum(1 for c in conformite if not c['present'] and not c['optionnel'])
        total_articles = sum(c['nb_lignes'] for c in conformite if c['present'] and c['type'] == 'products')
        if not fichiers:
            statut = 'absent'
        elif nb_manquants > 0:
            statut = 'incomplet'
        else:
            statut = 'ok'
        assortiments_detail.append({
            'code':           code,
            'nom':            f'Assortiment {code}',
            'statut':         statut,
            'fichiers':       conformite,
            'nb_manquants':   nb_manquants,
            'total_articles': total_articles,
            'nb_fichiers_presents': sum(1 for c in conformite if c['present'] and not c['optionnel']),
        })

    # Assortiments extra (présents dans les fichiers mais pas dans assortiment.txt)
    codes_attendus = set(assortiments_attendus)
    assortiments_extra = []
    for code, fichiers in fichiers_par_code.items():
        if code == '999' or code in codes_attendus:
            continue
        conformite = []
        for ftype in _FICHIERS_TOUS:
            if ftype in fichiers:
                conformite.append({'type': ftype, 'label': FILE_TYPE_LABELS[ftype],
                                   'present': True, 'optionnel': ftype in _FICHIERS_OPTIONNELS, **fichiers[ftype]})
        total_articles = sum(c['nb_lignes'] for c in conformite if c['type'] == 'products')
        assortiments_extra.append({
            'code': code,
            'nom':  f'Assortiment {code}',
            'fichiers': conformite,
            'total_articles': total_articles,
        })

    # Fichiers globaux 999
    fichiers_999 = []
    for ftype in _FICHIERS_GLOBAUX:
        if ftype in fichiers_par_code.get('999', {}):
            fichiers_999.append({'label': FILE_TYPE_LABELS[ftype], 'present': True,
                                  **fichiers_par_code['999'][ftype]})
        else:
            fichiers_999.append({'label': FILE_TYPE_LABELS.get(ftype, ftype),
                                  'present': False, 'nb_lignes': 0, 'taille': '—'})

    nb_ok       = sum(1 for a in assortiments_detail if a['statut'] == 'ok')
    nb_incomplet= sum(1 for a in assortiments_detail if a['statut'] == 'incomplet')
    nb_absent   = sum(1 for a in assortiments_detail if a['statut'] == 'absent')
    conformite_pct = round(nb_ok / len(assortiments_attendus) * 100) if assortiments_attendus else 0

    return {
        'name':               version_name,
        'date':               date,
        'date_str':           date_str,
        'heure':              heure,
        'nb_fichiers_total':  len(csv_files),
        'nb_fichiers_min':    _NB_FICHIERS_MIN,
        'nb_fichiers_max':    _NB_FICHIERS_MAX,
        'taille_fmt':         _fmt_size(total_size),
        'assortiments':       assortiments_detail,
        'assortiments_extra': assortiments_extra,
        'fichiers_999':       fichiers_999,
        'nb_ok':              nb_ok,
        'nb_incomplet':       nb_incomplet,
        'nb_absent':          nb_absent,
        'conformite_pct':     conformite_pct,
        'statut_global':      'ok' if nb_incomplet == 0 and nb_absent == 0 else
                              ('warning' if nb_absent == 0 else 'error'),
    }


# ─────────────────────────────────────────────
#  Sync SMB → base de données (lecture seule du SMB)
# ─────────────────────────────────────────────

def sync_versions_to_db(force=False):
    """
    Lit les versions prdP2A depuis le SMB et les sauvegarde en base.
    Ne supprime JAMAIS les fichiers originaux — lecture seule du SMB.

    force=True : re-sync même les versions déjà en base (utile après correction)
    Retourne un dict de stats : {synced, skipped, errors, smb_disponible}
    """
    from imports.models import VersionAstenSnap, AssortimentVersionSnap

    backup_dir     = Path(settings.DOSSIER_VERSIONS_ASTEN_PATH)
    assortiments   = _load_assortiments()
    today          = datetime.today().date()

    if not backup_dir.exists():
        return {'smb_disponible': False, 'synced': 0, 'skipped': 0, 'errors': 0}

    try:
        entries = [e for e in backup_dir.iterdir() if e.is_dir()]
    except PermissionError:
        return {'smb_disponible': False, 'synced': 0, 'skipped': 0, 'errors': 0}

    # Noms déjà en base (pour skip rapide)
    existants = set(VersionAstenSnap.objects.values_list('nom', flat=True))

    synced = skipped = errors = 0

    for entry in entries:
        if not _VERSION_PATTERN.match(entry.name):
            continue
        if entry.name in existants and not force:
            skipped += 1
            continue
        try:
            v = _parse_version_dir(entry, assortiments, today)
            if v is None:
                continue  # heure non-23h ou format invalide

            # Upsert VersionAstenSnap
            snap, _ = VersionAstenSnap.objects.update_or_create(
                nom=entry.name,
                defaults={
                    'date':           v['date'],
                    'heure':          v['heure'],
                    'statut':         v['statut'],
                    'conformite_pct': v['conformite_pct'],
                    'nb_fichiers_total': v['nb_total'],
                    'taille_raw':     v['taille_raw'],
                    'taille_fmt':     v['taille_fmt'],
                    'nb_ok':          len(v['assortiments_ok']),
                    'nb_incomplet':   len(v['assortiments_warning']),
                    'nb_absent':      len(v['assortiments_manquants']),
                    'assortiments_manquants': v['assortiments_manquants'],
                    'assortiments_extra':     v['assortiments_extra'],
                }
            )

            # Détail complet via get_version_asten_detail (parse les fichiers)
            detail = get_version_asten_detail(entry.name)
            if detail:
                # Supprimer les anciens assortiments pour réécrire proprement
                snap.assortiments.all().delete()
                AssortimentVersionSnap.objects.bulk_create([
                    AssortimentVersionSnap(
                        version=snap,
                        code=a['code'],
                        statut=a['statut'],
                        nb_fichiers_presents=a['nb_fichiers_presents'],
                        nb_manquants=a['nb_manquants'],
                        total_articles=a['total_articles'],
                        fichiers=a['fichiers'],
                    )
                    for a in detail['assortiments']
                ])
            synced += 1
        except Exception as e:
            errors += 1

    return {
        'smb_disponible': True,
        'synced':  synced,
        'skipped': skipped,
        'errors':  errors,
        'total_en_base': VersionAstenSnap.objects.count(),
    }


def get_versions_asten_from_db(limit=None, filtre_jours=None, date_debut=None, date_fin=None):
    """
    Lit les versions depuis la base de données (disponible sans SMB).
    Même interface de retour que get_versions_asten().
    """
    from imports.models import VersionAstenSnap
    from datetime import date as date_cls, timedelta

    today = date_cls.today()
    qs    = VersionAstenSnap.objects.all()

    if filtre_jours:
        qs = qs.filter(date__gte=today - timedelta(days=filtre_jours))
    if date_debut:
        qs = qs.filter(date__gte=date_debut)
    if date_fin:
        qs = qs.filter(date__lte=date_fin)

    if limit:
        qs = qs[:limit]

    assortiments = _load_assortiments()
    versions = []
    for snap in qs:
        delta = (today - snap.date).days
        versions.append({
            'name':                   snap.nom,
            'date':                   snap.date,
            'date_str':               str(snap.date),
            'heure':                  snap.heure,
            'delta_jours':            delta,
            'is_today':               delta == 0,
            'is_hier':                delta == 1,
            'nb_total':               snap.nb_fichiers_total,
            'nb_obligatoires':        len(assortiments) * len(_FICHIERS_REQUIS),
            'nb_attendu_max':         _NB_FICHIERS_MAX,
            'nb_assortiments':        snap.nb_ok + snap.nb_incomplet + snap.nb_absent,
            'taille_fmt':             snap.taille_fmt,
            'taille_raw':             snap.taille_raw,
            'statut':                 snap.statut,
            'conformite_pct':         snap.conformite_pct,
            'assortiments_ok':        [],   # non stocké en liste, mais nb_ok disponible
            'assortiments_warning':   [],
            'assortiments_manquants': snap.assortiments_manquants,
            'assortiments_extra':     snap.assortiments_extra,
            'source':                 'db',
        })

    nb_ok      = sum(1 for v in versions if v['statut'] == 'ok')
    nb_warning = sum(1 for v in versions if v['statut'] == 'warning')
    nb_error   = sum(1 for v in versions if v['statut'] == 'error')
    dates_set  = {v['date'] for v in versions}
    streak = 0
    d = today
    while d in dates_set:
        streak += 1
        d = d - timedelta(days=1)

    stats = {
        'total':           len(versions),
        'nb_ok':           nb_ok,
        'nb_warning':      nb_warning,
        'nb_error':        nb_error,
        'derniere':        versions[0]['date'] if versions else None,
        'delta_derniere':  versions[0]['delta_jours'] if versions else None,
        'statut_derniere': versions[0]['statut'] if versions else None,
        'streak':          streak,
        'taille_totale':   _fmt_size(sum(v['taille_raw'] for v in versions)),
        'nb_assortiments_attendus': len(assortiments),
    }

    return {
        'disponible': True,
        'source':     'db',
        'versions':   versions,
        'stats':      stats,
        'assortiments': assortiments,
    }


# ─────────────────────────────────────────────
#  Lecture d'un fichier CSV d'assortiment
# ─────────────────────────────────────────────

# Schéma des colonnes par type de fichier
_FILE_SCHEMAS = {
    'products': {
        'sep': ';',
        'cols': [
            {'key': 'code_famille',       'label': 'Code famille',    'searchable': False},
            {'key': 'ref_interne',        'label': 'Réf. interne',    'searchable': False},
            {'key': '_skip1',             'label': '',                'hidden': True},
            {'key': 'code_article',       'label': 'Code article',    'searchable': True,  'highlight': True},
            {'key': 'quantite',           'label': 'Qté',             'searchable': False},
            {'key': 'designation',        'label': 'Désignation',     'searchable': True},
            {'key': 'designation_court',  'label': 'Désig. courte',   'searchable': False},
            {'key': '_flag1',             'label': '',                'hidden': True},
            {'key': '_flag2',             'label': '',                'hidden': True},
            {'key': 'statut',             'label': 'Statut',          'searchable': False},
            {'key': '_skip2',             'label': '',                'hidden': True},
            {'key': 'prix1',              'label': 'Prix 1',          'searchable': False},
            {'key': 'prix2',              'label': 'Prix 2',          'searchable': False},
            {'key': 'prix3',              'label': 'Prix PV',         'searchable': False},
            {'key': 'type_prix',          'label': 'Type',            'searchable': False},
        ],
        'search_col': 3,   # code_article
    },
    'pricing': {
        'sep': ';',
        'cols': [
            {'key': 'type_ligne',    'label': 'Type',          'searchable': False},
            {'key': 'code_article',  'label': 'Code article',  'searchable': True, 'highlight': True},
            {'key': 'prix',          'label': 'Prix',          'searchable': False},
            {'key': '_c3',           'label': '',              'hidden': True},
            {'key': '_c4',           'label': '',              'hidden': True},
            {'key': '_c5',           'label': '',              'hidden': True},
            {'key': '_c6',           'label': '',              'hidden': True},
        ],
        'search_col': 1,
        'filter_rows': lambda row: row[0] == 'P' if row else False,  # only P lines
    },
    'linkedcodes': {
        'sep': ';',
        'cols': [
            {'key': '_skip',         'label': '',              'hidden': True},
            {'key': 'code_article',  'label': 'Code article',  'searchable': True, 'highlight': True},
            {'key': 'code_barre',    'label': 'Code barre',    'searchable': True},
            {'key': '_end',          'label': '',              'hidden': True},
        ],
        'search_col': 1,
    },
    'productssuppliers': {
        'sep': ';',
        'cols': [
            {'key': 'code_article',      'label': 'Code article',   'searchable': True, 'highlight': True},
            {'key': 'code_fournisseur',  'label': 'Fournisseur',    'searchable': False},
            {'key': 'prix',              'label': 'Prix achat',     'searchable': False},
            {'key': 'ref_fournisseur',   'label': 'Réf. fourn.',    'searchable': False},
            {'key': 'flag',              'label': 'Flag',           'searchable': False},
        ],
        'search_col': 0,
    },
}


def get_fichier_content(version_name, assortiment_code, file_type, search=None, page=1, per_page=50):
    """
    Lit et parse un fichier CSV d'assortiment.
    Retourne {'rows': list, 'cols': list, 'total': int, 'page': int, 'nb_pages': int, ...}
    """
    backup_dir  = Path(settings.DOSSIER_VERSIONS_ASTEN_PATH)
    version_dir = backup_dir / version_name

    if not version_dir.exists():
        return None

    # Trouver le fichier correspondant
    pattern = f"{assortiment_code}_{file_type}_*.csv"
    matches = list(version_dir.glob(pattern))
    if not matches:
        return {'rows': [], 'cols': [], 'total': 0, 'page': 1, 'nb_pages': 0,
                'fichier_nom': None, 'file_type': file_type,
                'assortiment': assortiment_code, 'version': version_name}

    fichier = matches[0]
    schema  = _FILE_SCHEMAS.get(file_type, None)

    # Lecture brute
    try:
        raw_lines = fichier.read_text(encoding='utf-8', errors='replace').splitlines()
    except Exception:
        return None

    sep = schema['sep'] if schema else ';'

    # Parse toutes les lignes
    all_rows = []
    for line in raw_lines:
        if not line.strip():
            continue
        parts = line.split(sep)
        all_rows.append(parts)

    # Filtre spécifique au type (ex: pricing → garder uniquement les lignes P)
    if schema and 'filter_rows' in schema:
        all_rows = [r for r in all_rows if schema['filter_rows'](r)]

    # Filtre recherche
    search_clean = search.strip() if search else ''
    if search_clean and schema:
        search_col = schema.get('search_col', 0)
        search_lower = search_clean.lower()
        filtered = []
        for row in all_rows:
            # Cherche dans toutes les colonnes searchable
            searchable_idxs = [i for i, c in enumerate(schema['cols']) if c.get('searchable') and i < len(row)]
            for idx in searchable_idxs:
                if idx < len(row) and search_lower in row[idx].lower():
                    filtered.append(row)
                    break
        all_rows = filtered
    elif search_clean:
        all_rows = [r for r in all_rows if any(search_clean.lower() in cell.lower() for cell in r)]

    total = len(all_rows)
    nb_pages = max(1, (total + per_page - 1) // per_page)
    page = max(1, min(page, nb_pages))
    start = (page - 1) * per_page
    page_rows = all_rows[start:start + per_page]

    # Colonnes visibles
    if schema:
        visible_cols = [c for c in schema['cols'] if not c.get('hidden')]
        col_indices  = [i for i, c in enumerate(schema['cols']) if not c.get('hidden')]
    else:
        # Colonnes génériques si pas de schéma
        max_cols = max((len(r) for r in page_rows), default=0)
        visible_cols = [{'key': f'col{i}', 'label': f'Col {i+1}', 'searchable': True} for i in range(max_cols)]
        col_indices  = list(range(max_cols))

    # Construire les lignes avec cellules nommées
    rows_out = []
    for raw in page_rows:
        cells = []
        for idx, col in zip(col_indices, visible_cols):
            val = raw[idx].strip() if idx < len(raw) else ''
            cells.append({'value': val, 'highlight': col.get('highlight', False)})
        rows_out.append(cells)

    # Plages de pages avec ellipses
    def _page_range(cur, tot, delta=2):
        pages, left, right = [], max(1, cur-delta), min(tot, cur+delta)
        if left > 1:
            pages.append(1)
            if left > 2: pages.append(-1)
        pages += list(range(left, right+1))
        if right < tot:
            if right < tot-1: pages.append(-1)
            pages.append(tot)
        return pages

    return {
        'rows':        rows_out,
        'cols':        visible_cols,
        'total':       total,
        'page':        page,
        'nb_pages':    nb_pages,
        'per_page':    per_page,
        'page_start':  start + 1,
        'page_end':    min(start + per_page, total),
        'page_range':  _page_range(page, nb_pages),
        'fichier_nom': fichier.name,
        'file_type':   file_type,
        'assortiment': assortiment_code,
        'version':     version_name,
        'search':      search_clean,
        'has_prev':    page > 1,
        'has_next':    page < nb_pages,
        'prev_page':   page - 1,
        'next_page':   page + 1,
    }


# ---------------------------------------------------------------------------
# Vérification des factures Asten / Cyrus
# ---------------------------------------------------------------------------

# Cache module-level pour le fichier Cyrus (peut dépasser 100 Mo)
_cyrus_cache = {'data': None, 'mtime': 0, 'path': None}


def _parse_dfac_cyrus(dfac_str):
    """Parse DFAC Cyrus (format YYMMDD) → date. Retourne None si invalide."""
    dfac_str = str(dfac_str).strip()
    try:
        return datetime.strptime('20' + dfac_str, '%Y%m%d').date()
    except (ValueError, TypeError):
        return None


def _parse_date_asten_facture(date_str):
    """Parse 'Date réception' Asten (DD/MM/YYYY HH:MM ou DD/MM/YYYY) → date."""
    date_str = str(date_str).strip()
    for fmt in ('%d/%m/%Y %H:%M', '%d/%m/%Y'):
        try:
            return datetime.strptime(date_str, fmt).date()
        except (ValueError, TypeError):
            pass
    return None


def _lire_cyrus_factures(dossier_path):
    """
    Lit tous les CSV Cyrus depuis dossier_path avec cache basé sur mtime.
    Retourne (liste_de_dicts, mtime_max).
    """
    global _cyrus_cache
    dossier = Path(dossier_path)
    if not dossier.exists():
        return [], 0

    csvs = sorted(dossier.glob('*.csv')) + sorted(dossier.glob('*.CSV'))
    if not csvs:
        return [], 0

    mtime_max = max(f.stat().st_mtime for f in csvs)

    if (
        _cyrus_cache['data'] is not None
        and _cyrus_cache['mtime'] == mtime_max
        and _cyrus_cache['path'] == str(dossier_path)
    ):
        return _cyrus_cache['data'], mtime_max

    rows = []
    for csv_path in csvs:
        for row in _iter_csv_rows(csv_path):
            nsee  = str(row.get('NSEE', '')).strip()
            nfac  = str(row.get('NFAC', '')).strip()
            dfac  = str(row.get('DFAC', '')).strip()
            cidc  = str(row.get('CIDC', '')).strip()
            lart  = str(row.get('LART', '')).strip()
            if not nsee or not nfac:
                continue
            cle = (nsee + '00' + nfac)
            if len(cle) < 10:
                cle = cle.zfill(10)
            try:
                pfth = float(str(row.get('PFTH', '0') or '0').replace(',', '.'))
            except (ValueError, TypeError):
                pfth = 0.0
            rows.append({
                **row,
                'nsee':         nsee,
                'nfac':         nfac,
                'cle_facture':  cle,
                'dfac_str':     dfac,
                'dfac_date':    _parse_dfac_cyrus(dfac),
                'cidc':         cidc,
                'lart':         lart,
                'pfth':         pfth,
                'fichier':      csv_path.name,
            })

    _cyrus_cache['data'] = rows
    _cyrus_cache['mtime'] = mtime_max
    _cyrus_cache['path'] = str(dossier_path)
    return rows, mtime_max


def importer_factures_cyrus_en_base():
    """
    Lit les CSV Cyrus dans media/facture_cyrus/, insère en base (FactureCyrusLigne),
    supprime les fichiers après import réussi.
    """
    from django.core.cache import cache
    cache.delete('factures_verification_v1')
    cache.delete('factures_stats_sql_v1')
    from imports.models import FactureCyrusLigne
    dossier = Path(settings.DOSSIER_FACTURES_CYRUS_PATH)
    if not dossier.exists():
        return

    for csv_path in sorted(dossier.glob('*.csv')):
        fichier = csv_path.name
        # Déjà importé ?
        if FactureCyrusLigne.objects.filter(fichier=fichier).exists():
            csv_path.unlink(missing_ok=True)
            continue
        lignes = []
        try:
            for row in _iter_csv_rows(csv_path):
                nsee = str(row.get('NSEE', '')).strip()
                nfac = str(row.get('NFAC', '')).strip()
                cidc = str(row.get('CIDC', '')).strip()
                dfac_str = str(row.get('DFAC', '')).strip()
                if not nfac or not cidc:
                    continue
                cle = f"{nsee}00{nfac}" if nsee else nfac

                def _dec(val):
                    try:
                        return float(str(val or '0').replace(',', '.').replace(' ', '')) or None
                    except (ValueError, TypeError):
                        return None

                dfac_date = None
                if dfac_str:
                    for fmt in ('%y%m%d', '%Y%m%d', '%d/%m/%Y', '%d/%m/%y'):
                        try:
                            from datetime import date as date_cls
                            dfac_date = datetime.strptime(dfac_str, fmt).date()
                            break
                        except ValueError:
                            continue

                lignes.append(FactureCyrusLigne(
                    cle_facture=cle,
                    nsee=nsee,
                    nfac=nfac,
                    dfac_str=dfac_str,
                    dfac_date=dfac_date,
                    cidc=cidc,
                    lart=str(row.get('LART', '') or '').strip(),
                    nart=str(row.get('NART', '') or '').strip(),
                    qlvu=_dec(row.get('QLVU')),
                    pvtc=_dec(row.get('PVTC')),
                    ptvc=_dec(row.get('PTVC')),
                    pfth=_dec(row.get('PFTH')),
                    fichier=fichier,
                ))
            if lignes:
                FactureCyrusLigne.objects.bulk_create(lignes, batch_size=500)
                print(f"  [Facture Cyrus] {fichier} → {len(lignes)} lignes importées")
            csv_path.unlink(missing_ok=True)
        except Exception as e:
            print(f"  [Facture Cyrus] ERREUR {fichier}: {e}")
    # Rafraîchir la vue matérialisée
    try:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute('REFRESH MATERIALIZED VIEW CONCURRENTLY mv_factures_joined')
    except Exception:
        try:
            from django.db import connection
            with connection.cursor() as cur:
                cur.execute('REFRESH MATERIALIZED VIEW mv_factures_joined')
        except Exception as e:
            print(f"  [Vue] Impossible de rafraîchir mv_factures_joined: {e}")


def importer_factures_asten_en_base():
    """
    Lit les CSV Asten dans media/facture_asten/, insère en base (FactureAstenLigne),
    supprime les fichiers après import réussi.
    """
    from django.core.cache import cache
    cache.delete('factures_verification_v1')
    cache.delete('factures_stats_sql_v1')
    from imports.models import FactureAstenLigne
    dossier = Path(settings.DOSSIER_FACTURES_ASTEN_CSV_PATH)
    if not dossier.exists():
        return

    for csv_path in sorted(dossier.glob('*.csv')):
        fichier = csv_path.name
        if FactureAstenLigne.objects.filter(fichier=fichier).exists():
            csv_path.unlink(missing_ok=True)
            continue
        lignes = []
        try:
            for row in _iter_csv_rows(csv_path):
                n_bon = str(row.get('N° bon livraison', '')).strip()
                magasin = str(row.get('Magasin', '')).strip()
                if not n_bon:
                    continue
                date_str = str(row.get('Date réception', '')).strip()

                def _dec(val):
                    try:
                        v = str(val or '').strip()
                        return float(v.replace(',', '.')) if v else None
                    except (ValueError, TypeError):
                        return None

                lignes.append(FactureAstenLigne(
                    n_bon_livraison=n_bon,
                    magasin=magasin,
                    fournisseur=str(row.get('Fournisseur', '') or '').strip(),
                    statut_commande=str(row.get('Statut commande', '') or '').strip(),
                    date_reception_str=date_str,
                    date_reception_date=_parse_date_asten_facture(date_str),
                    quantite_totale=_dec(row.get('Quantité totale')),
                    valorisation_ht=_dec(row.get('Valorisation HT')),
                    valorisation_ttc=_dec(row.get('Valorisation TTC')),
                    type_reception=str(row.get('Type réception', '') or '').strip(),
                    fichier=fichier,
                ))
            if lignes:
                FactureAstenLigne.objects.bulk_create(lignes, batch_size=500)
                print(f"  [Facture Asten] {fichier} → {len(lignes)} lignes importées")
            csv_path.unlink(missing_ok=True)
        except Exception as e:
            print(f"  [Facture Asten] ERREUR {fichier}: {e}")
    # Rafraîchir la vue matérialisée
    try:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute('REFRESH MATERIALIZED VIEW CONCURRENTLY mv_factures_joined')
    except Exception:
        try:
            from django.db import connection
            with connection.cursor() as cur:
                cur.execute('REFRESH MATERIALIZED VIEW mv_factures_joined')
        except Exception as e:
            print(f"  [Vue] Impossible de rafraîchir mv_factures_joined: {e}")


def _lire_asten_factures(dossier_path):
    """
    Lit tous les CSV Asten (receptions) depuis dossier_path.
    Retourne liste de dicts avec champs normalisés.
    """
    dossier = Path(dossier_path)
    if not dossier.exists():
        return []

    rows = []
    for csv_path in sorted(dossier.glob('*.csv')):
        for row in _iter_csv_rows(csv_path):
            magasin  = str(row.get('Magasin', '')).strip()
            n_bon    = str(row.get('N° bon livraison', '')).strip()
            date_str = str(row.get('Date réception', '')).strip()
            if not n_bon:
                continue
            rows.append({
                **row,
                'magasin':             magasin,
                'n_bon_livraison':     n_bon,
                'date_reception_str':  date_str,
                'date_reception_date': _parse_date_asten_facture(date_str),
                'fournisseur':         str(row.get('Fournisseur', '')).strip(),
                'statut_commande':     str(row.get('Statut commande', '')).strip(),
                'quantite_totale':     str(row.get('Quantité totale', '')).strip(),
                'valorisation_ht':     str(row.get('Valorisation HT', '')).strip(),
                'valorisation_ttc':    str(row.get('Valorisation TTC', '')).strip(),
                'type_reception':      str(row.get('Type réception', '')).strip(),
                'fichier':             csv_path.name,
            })
    return rows


def get_factures_stats_sql():
    """
    Stats Cyrus/Asten calculées depuis mv_factures_joined.
    Tout en SQL — zéro boucle Python sur 38k lignes. Cache 20 min.
    """
    from django.core.cache import cache
    from django.db import connection
    from datetime import date, timedelta
    CACHE_KEY = 'factures_stats_sql_v1'
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached

    try:
        today          = date.today()
        debut_sem      = today - timedelta(days=today.weekday())
        debut_sem_prec = debut_sem - timedelta(weeks=1)
        fin_sem_prec   = debut_sem - timedelta(days=1)

        with connection.cursor() as cur:
            # ── Agrégats globaux et par magasin en une seule requête ──
            cur.execute("""
                SELECT
                    m.cidc,
                    COALESCE(fa.full_asten, false)                          AS is_full_asten,
                    COUNT(*)                                                 AS total,
                    COUNT(*) FILTER (WHERE m.integree AND m.qt_asten > 0)   AS integrees,
                    COUNT(*) FILTER (WHERE m.integree AND m.qt_asten = 0)   AS integrees_vide,
                    COUNT(*) FILTER (WHERE NOT m.integree
                                     AND COALESCE(fa.full_asten, false))    AS ecarts,
                    COUNT(*) FILTER (WHERE NOT m.integree
                                     AND NOT COALESCE(fa.full_asten, false)) AS non_full_asten,
                    -- Semaine courante
                    COUNT(*) FILTER (WHERE m.dfac_date >= %s
                                     AND m.integree)                        AS sem_cur_int,
                    COUNT(*) FILTER (WHERE m.dfac_date >= %s
                                     AND NOT m.integree
                                     AND COALESCE(fa.full_asten, false))    AS sem_cur_eca,
                    -- Semaine précédente
                    COUNT(*) FILTER (WHERE m.dfac_date BETWEEN %s AND %s
                                     AND m.integree)                        AS sem_prev_int,
                    COUNT(*) FILTER (WHERE m.dfac_date BETWEEN %s AND %s
                                     AND NOT m.integree
                                     AND COALESCE(fa.full_asten, false))    AS sem_prev_eca
                FROM mv_factures_joined m
                LEFT JOIN core_magasin fa ON fa.code = m.cidc
                GROUP BY m.cidc, fa.full_asten
            """, [
                debut_sem, debut_sem,
                debut_sem_prec, fin_sem_prec,
                debut_sem_prec, fin_sem_prec,
            ])
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, row)) for row in cur.fetchall()]

        nb_integrees = nb_integrees_vide = nb_non_integrees = nb_non_full_asten = 0
        sem_courante   = {'integrees': 0, 'non_integrees': 0}
        sem_precedente = {'integrees': 0, 'non_integrees': 0}
        par_magasin    = {}

        for row in rows:
            cidc  = row['cidc']
            is_fa = bool(row['is_full_asten'])
            nb_integrees       += int(row['integrees'])
            nb_integrees_vide  += int(row['integrees_vide'])
            nb_non_integrees   += int(row['ecarts'])
            nb_non_full_asten  += int(row['non_full_asten'])
            sem_courante['integrees']     += int(row['sem_cur_int'])
            sem_courante['non_integrees'] += int(row['sem_cur_eca'])
            sem_precedente['integrees']     += int(row['sem_prev_int'])
            sem_precedente['non_integrees'] += int(row['sem_prev_eca'])
            par_magasin[cidc] = {
                'integrees':     int(row['integrees']),
                'integrees_vide': int(row['integrees_vide']),
                'ecarts':        int(row['ecarts']),
                'full_asten':    is_fa,
            }

        total = nb_integrees + nb_integrees_vide + nb_non_integrees + nb_non_full_asten
        taux  = round((nb_integrees + nb_integrees_vide) / total * 100, 1) if total else 0
        top10 = sorted(
            [(c, s) for c, s in par_magasin.items() if s['ecarts'] > 0],
            key=lambda x: x[1]['ecarts'], reverse=True
        )[:10]

        result = {
            'total': total, 'integrees': nb_integrees, 'integrees_vide': nb_integrees_vide,
            'ecarts': nb_non_integrees, 'non_full_asten': nb_non_full_asten, 'taux': taux,
            'par_magasin': par_magasin, 'top10_ecarts': top10,
            'semaine_courante': sem_courante, 'semaine_precedente': sem_precedente,
        }
        cache.set(CACHE_KEY, result, 60 * 20)
        return result
    except Exception as e:
        return {
            'total': 0, 'integrees': 0, 'integrees_vide': 0, 'ecarts': 0,
            'non_full_asten': 0, 'taux': 0, 'par_magasin': {},
            'top10_ecarts': [], 'semaine_courante': {}, 'semaine_precedente': {},
        }


def get_factures_verification():
    """
    Lit mv_factures_joined (vue matérialisée PostgreSQL) pour éviter de charger
    600k+ lignes Cyrus + 400k+ lignes Asten en Python.
    Applique les statuts manuels (FactureEcartStatut) par-dessus.
    Résultat mis en cache 30 min — invalidé automatiquement après import.
    """
    from django.core.cache import cache
    CACHE_KEY = 'factures_verification_v1'
    cached = cache.get(CACHE_KEY)
    if cached is not None:
        return cached
    from django.db import connection
    from imports.models import FactureEcartStatut, FactureCyrusLigne
    from core.models import Magasin
    try:
        full_asten_codes = set(Magasin.objects.filter(full_asten=True).values_list('code', flat=True))

        # Statuts manuels : (cle_facture, dfac_str, cidc) → statut
        statuts_db = {
            (s.cle_facture, s.dfac_str, s.cidc): s.statut
            for s in FactureEcartStatut.objects.all()
        }

        # Vérifier si la vue matérialisée existe
        use_mv = False
        with connection.cursor() as cur:
            cur.execute("""
                SELECT EXISTS (
                    SELECT 1 FROM pg_matviews WHERE matviewname = 'mv_factures_joined'
                )
            """)
            use_mv = cur.fetchone()[0]

        if use_mv:
            with connection.cursor() as cur:
                cur.execute("""
                    SELECT cle_facture, nfac, nsee, dfac_str, dfac_date, cidc,
                           pfth_total, nb_articles, qt_asten, valo_ttc, integree
                    FROM mv_factures_joined
                    ORDER BY cidc, dfac_date
                """)
                cols = [d[0] for d in cur.description]
                mv_rows = [dict(zip(cols, row)) for row in cur.fetchall()]
        else:
            # Fallback : construire depuis les tables (lent mais fiable)
            from imports.models import FactureAstenLigne
            qs_cyrus = FactureCyrusLigne.objects.exclude(nsee='19').exclude(qlvu__lt=0)
            factures_dict = {}
            factures_pfth = {}
            factures_nb   = {}
            for obj in qs_cyrus.values('cle_facture', 'nsee', 'nfac', 'dfac_str', 'dfac_date', 'cidc', 'lart', 'pfth'):
                key = (obj['cle_facture'], obj['dfac_date'], obj['cidc'])
                if key not in factures_dict:
                    factures_dict[key] = obj
                    factures_pfth[key] = 0.0
                    factures_nb[key]   = 0
                factures_pfth[key] += float(obj['pfth'] or 0)
                factures_nb[key]   += 1
            asten_index = {}
            for obj in FactureAstenLigne.objects.all().values('n_bon_livraison', 'magasin', 'quantite_totale', 'valorisation_ttc'):
                k = (obj['n_bon_livraison'], str(obj['magasin']))
                if k not in asten_index:
                    asten_index[k] = obj
            mv_rows = []
            for key, cr in factures_dict.items():
                am = asten_index.get((cr['cle_facture'], cr['cidc']))
                qt = float(str(am.get('quantite_totale') or '0').replace(',', '.')) if am else 0.0
                valo = None
                if am:
                    raw = str(am.get('valorisation_ttc') or '').strip()
                    if raw:
                        try:
                            valo = float(raw.replace(',', '.'))
                        except (ValueError, TypeError):
                            pass
                mv_rows.append({
                    'cle_facture': cr['cle_facture'],
                    'nfac':        cr['nfac'],
                    'nsee':        cr['nsee'],
                    'dfac_str':    cr['dfac_str'],
                    'dfac_date':   cr['dfac_date'],
                    'cidc':        cr['cidc'],
                    'pfth_total':  factures_pfth[key],
                    'nb_articles': factures_nb[key],
                    'qt_asten':    qt,
                    'valo_ttc':    valo,
                    'integree':    am is not None,
                })

        joined            = []
        stats_mag         = {}
        nb_integrees      = 0
        nb_integrees_vide = 0
        nb_ignores        = 0

        for row in mv_rows:
            cidc         = row['cidc']
            cle          = row['cle_facture']
            dfac_str     = row['dfac_str']
            integree_csv = bool(row['integree'])
            qt_asten     = float(row['qt_asten'] or 0)
            pfth_cyrus   = float(row['pfth_total'] or 0)
            valo_ttc     = row['valo_ttc']
            valo_asten   = float(valo_ttc) if valo_ttc is not None else 0.0
            valo_dispo   = valo_ttc is not None

            ecart_valo     = abs(round(valo_asten - pfth_cyrus, 2)) if integree_csv and valo_dispo and qt_asten > 0 else 0.0
            has_ecart_valo = ecart_valo > 1

            statut_manuel = statuts_db.get((cle, dfac_str, cidc))
            is_full_asten = cidc in full_asten_codes

            if integree_csv:
                statut_effectif = 'integre' if qt_asten > 0 else 'integre_vide'
            elif statut_manuel == 'ignore':
                nb_ignores += 1
                continue
            elif statut_manuel == 'integre':
                statut_effectif = 'integre'
            elif not is_full_asten:
                statut_effectif = 'non_full_asten'
            else:
                statut_effectif = 'non_integre'

            integree_effective = statut_effectif in ('integre', 'integre_vide')

            if statut_effectif == 'integre':
                nb_integrees += 1
            elif statut_effectif == 'integre_vide':
                nb_integrees_vide += 1

            joined.append({
                'cle_facture':     cle,
                'nfac':            row['nfac'],
                'nsee':            row['nsee'],
                'dfac_str':        dfac_str,
                'dfac_date':       row['dfac_date'],
                'cidc':            cidc,
                'pfth':            pfth_cyrus,
                'integree':        integree_effective,
                'integree_csv':    integree_csv,
                'statut_manuel':   statut_manuel,
                'statut_effectif': statut_effectif,
                'nb_articles':     int(row['nb_articles'] or 0),
                'pfth_cyrus':      pfth_cyrus,
                'valo_asten':      valo_asten,
                'qt_asten':        qt_asten,
                'ecart_valo':      ecart_valo,
                'has_ecart_valo':  has_ecart_valo,
            })

            if cidc not in stats_mag:
                stats_mag[cidc] = {'total': 0, 'integrees': 0, 'integrees_vide': 0, 'ecarts': 0, 'ecarts_valo': 0, 'non_full_asten': 0, 'full_asten': is_full_asten}
            stats_mag[cidc]['total'] += 1
            if statut_effectif == 'integre':
                stats_mag[cidc]['integrees'] += 1
            elif statut_effectif == 'integre_vide':
                stats_mag[cidc]['integrees_vide'] += 1
            elif statut_effectif == 'non_full_asten':
                stats_mag[cidc]['non_full_asten'] += 1
            else:
                stats_mag[cidc]['ecarts'] += 1
            if has_ecart_valo:
                stats_mag[cidc]['ecarts_valo'] += 1

        nb_non_integrees  = sum(1 for r in joined if r['statut_effectif'] == 'non_integre')
        nb_non_full_asten = sum(1 for r in joined if r['statut_effectif'] == 'non_full_asten')
        nb_ecarts_valo    = sum(1 for r in joined if r['has_ecart_valo'])
        result = {
            'joined': joined,
            'stats':  {
                'total':          len(joined),
                'integrees':      nb_integrees,
                'integrees_vide': nb_integrees_vide,
                'ecarts':         nb_non_integrees,
                'ecarts_valo':    nb_ecarts_valo,
                'non_full_asten': nb_non_full_asten,
                'ignores':        nb_ignores,
                'par_magasin':    dict(sorted(stats_mag.items())),
            },
            'error': None,
        }
        cache.set(CACHE_KEY, result, 60 * 30)
        return result
    except Exception as e:
        return {
            'joined': [],
            'stats':  {'total': 0, 'integrees': 0, 'integrees_vide': 0, 'ecarts': 0, 'ecarts_valo': 0, 'ignores': 0, 'par_magasin': {}},
            'error':  str(e),
        }
