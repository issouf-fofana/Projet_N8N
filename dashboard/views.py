from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils.dateparse import parse_date
from django.urls import reverse
from django.core.paginator import Paginator
from datetime import datetime
from django.utils import timezone
from django.db.models import Q, Prefetch, Exists, OuterRef
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from imports.services import scanner_et_importer_fichiers
from imports.models import ImportFichier
from ecarts.services import recalculer_ecarts, get_statistiques
from asten.models import CommandeAsten
from cyrus.models import CommandeCyrus
from gpv.models import CommandeGPV
from legend.models import CommandeLegend
from br.models import BRAsten
from ecarts.models import EcartCommande, EcartGPV, EcartLegend
from core.models import Magasin
from django.conf import settings
from pathlib import Path
from tickets.models import Ticket


def dashboard(request):
    """Vue principale du dashboard"""
    # Les données existantes en base sont TOUJOURS chargées et affichées
    # Vérifier s'il y a de nouveaux fichiers à importer (même si déjà actualisé dans la session)
    # Les données restent en base de données donc elles persistent même si on change de type
    if request.GET.get('type_donnees') != 'br':
        try:
            # Vérifier s'il y a de nouveaux fichiers à importer
            # Utiliser les chemins configurables depuis settings
            sources = {
                'asten': Path(settings.DOSSIER_COMMANDES_ASTEN_PATH),
                'cyrus': Path(settings.DOSSIER_COMMANDES_CYRUS_PATH),
                'gpv': Path(settings.DOSSIER_COMMANDES_GPV_PATH),
                'legend': Path(settings.DOSSIER_COMMANDES_LEGEND_PATH),
            }
            nouveaux_fichiers = False
            for type_fichier, dossier in sources.items():
                if not dossier.exists():
                    continue
                fichiers = list(dossier.glob('*.csv')) + list(dossier.glob('*.CSV'))
                for fichier in fichiers:
                    import_existant = ImportFichier.objects.filter(
                        type_fichier=type_fichier, nom_fichier=fichier.name
                    ).first()
                    if not import_existant:
                        nouveaux_fichiers = True
                        break
                    date_modif = timezone.make_aware(datetime.fromtimestamp(fichier.stat().st_mtime))
                    if date_modif > import_existant.date_import:
                        nouveaux_fichiers = True
                        break
                if nouveaux_fichiers:
                    break
            
            # Importer seulement s'il y a de nouveaux fichiers ou si c'est la première visite
            if nouveaux_fichiers or 'donnees_actualisees' not in request.session:
                # Scanner et importer les nouveaux fichiers (silencieux, en arrière-plan)
                scanner_et_importer_fichiers()
                # Recalculer les écarts (silencieux, en arrière-plan)
                recalculer_ecarts()
                # Marquer comme actualisé dans la session
                request.session['donnees_actualisees'] = True
        except Exception as e:
            # En cas d'erreur, on continue quand même (pas de message visible pour l'utilisateur)
            # Les erreurs sont loggées mais n'interrompent pas l'affichage
            pass
    
    # Recalculer les écarts seulement si demandé explicitement ou si c'est la première fois
    # Ne pas le faire à chaque chargement pour améliorer les performances
    if request.GET.get('recalculer') == '1' or ('donnees_actualisees' not in request.session and request.GET.get('type_donnees') != 'br'):
        try:
            recalculer_ecarts()
        except Exception as e:
            # En cas d'erreur, on continue quand même
            pass
    
    # Récupérer les filtres (gérer les valeurs "None" en string et la sélection multiple)
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    code_magasin = request.GET.getlist('magasin')  # Récupérer plusieurs valeurs pour la sélection multiple
    type_donnees = request.GET.get('type_donnees', 'commandes_asten')  # Par défaut: commandes Asten
    periode = request.GET.get('periode', 'tous')  # Par défaut: 'tous' pour afficher toutes les données
    show = request.GET.get('show', '')  # 'non_integres' pour afficher uniquement les écarts ouverts
    
    # Si periode est 'tous' ou vide, ne pas appliquer de filtre de date
    if periode == 'tous' or periode == '':
        date_debut = None
        date_fin = None
        date_debut_parsed = None
        date_fin_parsed = None
    else:
        # Nettoyer les valeurs "None" en string
        if date_debut == 'None' or date_debut == '':
            date_debut = None
        if date_fin == 'None' or date_fin == '':
            date_fin = None
        # Convertir les dates seulement si periode n'est pas 'tous'
        date_debut_parsed = parse_date(date_debut) if date_debut else None
        date_fin_parsed = parse_date(date_fin) if date_fin else None
    
    # Nettoyer la liste des magasins
    if code_magasin:
        code_magasin = [m for m in code_magasin if m and m != 'None' and m != '']
        if not code_magasin:
            code_magasin = None
        elif len(code_magasin) == 1:
            # Si un seul magasin est sélectionné, garder comme liste pour cohérence
            pass
    
    # Liste des magasins pour le filtre
    magasins = Magasin.objects.all().order_by('code')
    
    # Initialiser les variables avec des valeurs par défaut
    # IMPORTANT: Les données doivent TOUJOURS être chargées depuis la base, même sans actualisation
    stats = {
        'total_source': 0,
        'total_target': 0,
        'integres': 0,
        'non_integres': 0,
        'taux_integration': 0,
        'taux_non_integration': 0,
    }
    commandes_data = []
    titre_tableau = "Comparaison Asten vs Cyrus"


    # Traiter selon le type de données sélectionné
    if type_donnees == 'commandes_asten':
        # Récupérer les commandes avec leurs statuts d'intégration
        # TOUJOURS charger les données existantes en base, même sans actualisation
        filtres_asten = {}
        filtres_cyrus = {}
        if date_debut_parsed:
            filtres_asten['date_commande__gte'] = date_debut_parsed
            filtres_cyrus['date_commande__gte'] = date_debut_parsed
        if date_fin_parsed:
            filtres_asten['date_commande__lte'] = date_fin_parsed
            filtres_cyrus['date_commande__lte'] = date_fin_parsed
        if code_magasin:
            # Gérer la sélection multiple de magasins
            filtres_asten['code_magasin__code__in'] = code_magasin
            filtres_cyrus['code_magasin__code__in'] = code_magasin
        
        # Calculer les statistiques avec les filtres appliqués
        total_asten = CommandeAsten.objects.filter(**filtres_asten).count()
        total_cyrus = CommandeCyrus.objects.filter(**filtres_cyrus).count()
        
        # Compter les commandes réellement intégrées dans Cyrus (optimisé avec une sous-requête)
        commandes_reellement_integres = CommandeAsten.objects.filter(**filtres_asten).filter(
            Exists(
                CommandeCyrus.objects.filter(
                    date_commande=OuterRef('date_commande'),
                    numero_commande=OuterRef('numero_commande'),
                    code_magasin=OuterRef('code_magasin')
                )
            )
        ).count()
        
        # Compter les écarts avec les filtres
        filtres_ecarts = {}
        if date_debut_parsed:
            filtres_ecarts['commande_asten__date_commande__gte'] = date_debut_parsed
        if date_fin_parsed:
            filtres_ecarts['commande_asten__date_commande__lte'] = date_fin_parsed
        if code_magasin:
            filtres_ecarts['commande_asten__code_magasin__code__in'] = code_magasin
        
        # Compter les écarts par statut
        total_ecarts_ouverts = EcartCommande.objects.filter(**filtres_ecarts).filter(statut='ouvert').count()
        total_ecarts_resolus = EcartCommande.objects.filter(**filtres_ecarts).filter(statut='resolu').count()
        total_ecarts_ignores = EcartCommande.objects.filter(**filtres_ecarts).filter(statut='ignore').count()
        total_ecarts_quantite_0 = EcartCommande.objects.filter(**filtres_ecarts).filter(statut='quantite_0').count()
        
        # Logique de calcul :
        # - Les écarts "ouverts" = commandes non intégrées
        # - Les écarts "résolus" = commandes considérées comme intégrées (même si pas encore dans Cyrus)
        # - Les écarts "ignorés" = commandes considérées comme intégrées
        # - Les écarts "quantite_0" = NE COMPTENT PAS dans les statistiques (exclus du total)
        # 
        # Total pour les statistiques = total_asten - total_ecarts_quantite_0 (exclure les quantite_0)
        # Commandes intégrées = total_asten - total_ecarts_ouverts - total_ecarts_quantite_0
        # (les écarts résolus et ignorés sont déjà comptés comme intégrés dans cette formule)
        
        # Exclure les commandes avec écart "quantite_0" du total affiché
        total_asten_pour_stats = total_asten - total_ecarts_quantite_0
        
        # Commandes intégrées = total - écarts ouverts - écarts quantite_0
        # (les écarts résolus et ignorés sont déjà dans le total, donc ils sont comptés comme intégrés)
        commandes_integres = total_asten - total_ecarts_ouverts - total_ecarts_quantite_0
        commandes_non_integres = total_ecarts_ouverts
        
        # Calculer les taux basés sur le total sans les quantite_0
        taux_integration = round((commandes_integres / total_asten_pour_stats * 100) if total_asten_pour_stats > 0 else 0, 2)
        taux_non_integration = round((commandes_non_integres / total_asten_pour_stats * 100) if total_asten_pour_stats > 0 else 0, 2)
        
        # Normaliser les statistiques pour correspondre au template
        # Utiliser total_asten_pour_stats pour exclure les quantite_0 du total affiché
        stats = {
            'total_source': total_asten_pour_stats,  # Total sans les quantite_0
            'total_target': total_cyrus,
            'integres': commandes_integres,
            'non_integres': commandes_non_integres,  # Utiliser le calcul réel, pas les écarts
            'taux_integration': taux_integration,
            'taux_non_integration': taux_non_integration,
        }
        
        # Optimiser les requêtes : précharger les écarts et les commandes Cyrus correspondantes
        # Utiliser prefetch_related pour éviter les requêtes N+1
        commandes_asten = CommandeAsten.objects.filter(**filtres_asten).select_related('code_magasin').prefetch_related(
            Prefetch('ecart', queryset=EcartCommande.objects.all())
        ).order_by('-date_commande', 'numero_commande')
        
        # Limiter le nombre de commandes pour l'affichage (pagination implicite)
        # Charger seulement les 200 premières pour améliorer les performances
        commandes_asten_limited = list(commandes_asten[:200])
        
        # Créer un dictionnaire des commandes Cyrus pour lookup rapide
        # Récupérer toutes les commandes Cyrus correspondantes en une seule requête optimisée
        cyrus_lookup = {}
        if commandes_asten_limited:
            # Convertir en liste pour éviter les problèmes d'itération
            commandes_list = commandes_asten_limited
            
            # Récupérer les clés uniques des commandes Asten
            asten_keys = []
            for cmd in commandes_list:
                asten_keys.append((cmd.date_commande, cmd.numero_commande, cmd.code_magasin.code))
            
            # Construire une requête optimisée avec Q objects
            # Utiliser toutes les clés pour vérifier toutes les commandes affichées
            if asten_keys:
                # Construire la requête Q par lots de 50 pour éviter les requêtes SQL trop complexes
                # mais traiter toutes les commandes affichées
                cyrus_commands_list = []
                for i in range(0, len(asten_keys), 50):
                    batch_keys = asten_keys[i:i+50]
                    q_objects = Q()
                    for date, numero, code in batch_keys:
                        q_objects |= Q(date_commande=date, numero_commande=numero, code_magasin__code=code)
                    
                    batch_cyrus = CommandeCyrus.objects.filter(q_objects).select_related('code_magasin')
                    cyrus_commands_list.extend(batch_cyrus)
                
                # Créer un dictionnaire pour lookup rapide
                for cyrus_cmd in cyrus_commands_list:
                    key = (cyrus_cmd.date_commande, cyrus_cmd.numero_commande, cyrus_cmd.code_magasin.code)
                    cyrus_lookup[key] = cyrus_cmd
        
        # Préparer les données pour l'affichage
        commandes_integres_list = []
        commandes_non_integres_list = []
        
        for cmd_asten in commandes_asten_limited:
            # Lookup rapide dans le dictionnaire
            key = (cmd_asten.date_commande, cmd_asten.numero_commande, cmd_asten.code_magasin.code)
            cmd_cyrus = cyrus_lookup.get(key)
            
            # Récupérer l'écart (déjà préchargé avec prefetch_related)
            try:
                ecart = cmd_asten.ecart
            except:
                ecart = None
            
            # Si l'écart est résolu, considérer comme intégré même si pas dans Cyrus
            # Si l'écart est quantite_0, ne pas le compter (ni intégré ni non intégré)
            is_integre = False
            if ecart:
                if ecart.statut == 'resolu':
                    is_integre = True  # Écart résolu = considéré comme intégré
                elif ecart.statut == 'quantite_0':
                    continue  # Écart quantite_0 = exclu de l'affichage
                elif ecart.statut == 'ignore':
                    is_integre = True  # Écart ignoré = considéré comme intégré
                else:
                    is_integre = cmd_cyrus is not None  # Écart ouvert = vérifier si dans Cyrus
            else:
                is_integre = cmd_cyrus is not None  # Pas d'écart = vérifier si dans Cyrus
            
            item = {
                'asten': cmd_asten,
                'cyrus': cmd_cyrus,
                'integre': is_integre,
                'ecart': ecart,
            }
            
            # Séparer les intégrées et non intégrées
            if is_integre:
                commandes_integres_list.append(item)
            else:
                commandes_non_integres_list.append(item)
        
        # Mettre les non intégrées en premier, puis les intégrées (ou seulement non intégrées selon le filtre)
        if show == 'non_integres':
            commandes_data = commandes_non_integres_list
        else:
            commandes_data = commandes_non_integres_list + commandes_integres_list
        titre_tableau = "Comparaison Asten vs Cyrus"
        
    elif type_donnees == 'commandes_gpv':
        # Récupérer les commandes GPV avec leurs statuts d'intégration
        # IMPORTANT: Seules les commandes avec statut "Transmise" doivent être dans Cyrus
        filtres_gpv = {}
        filtres_cyrus = {}
        if date_debut_parsed:
            filtres_gpv['date_creation__gte'] = date_debut_parsed
            filtres_cyrus['date_commande__gte'] = date_debut_parsed
        if date_fin_parsed:
            filtres_gpv['date_creation__lte'] = date_fin_parsed
            filtres_cyrus['date_commande__lte'] = date_fin_parsed
        if code_magasin:
            # Gérer la sélection multiple de magasins
            filtres_gpv['code_magasin__code__in'] = code_magasin
            filtres_cyrus['code_magasin__code__in'] = code_magasin
        
        # Filtrer uniquement les commandes "Transmise" pour les statistiques
        # (car seules celles-ci doivent être dans Cyrus)
        filtres_gpv_transmise = filtres_gpv.copy()
        filtres_gpv_transmise['statut__iexact'] = 'Transmise'
        
        # Calculer les statistiques avec les filtres appliqués
        # Total GPV "Transmise" = seules celles qui doivent être dans Cyrus
        total_gpv_transmise = CommandeGPV.objects.filter(**filtres_gpv_transmise).count()
        total_cyrus = CommandeCyrus.objects.filter(**filtres_cyrus).count()
        
        # Compter les commandes réellement intégrées dans Cyrus (optimisé avec une sous-requête)
        commandes_reellement_integres = CommandeGPV.objects.filter(**filtres_gpv_transmise).filter(
            Exists(
                CommandeCyrus.objects.filter(
                    date_commande=OuterRef('date_creation'),
                    numero_commande=OuterRef('numero_commande'),
                    code_magasin=OuterRef('code_magasin')
                )
            )
        ).count()
        
        # Compter les écarts avec les filtres (seulement pour les "Transmise")
        filtres_ecarts = {}
        if date_debut_parsed:
            filtres_ecarts['commande_gpv__date_creation__gte'] = date_debut_parsed
        if date_fin_parsed:
            filtres_ecarts['commande_gpv__date_creation__lte'] = date_fin_parsed
        if code_magasin:
            filtres_ecarts['commande_gpv__code_magasin__code__in'] = code_magasin
        
        # Compter les écarts par statut
        total_ecarts_ouverts = EcartGPV.objects.filter(**filtres_ecarts).filter(statut='ouvert').count()
        total_ecarts_resolus = EcartGPV.objects.filter(**filtres_ecarts).filter(statut='resolu').count()
        total_ecarts_ignores = EcartGPV.objects.filter(**filtres_ecarts).filter(statut='ignore').count()
        total_ecarts_quantite_0 = EcartGPV.objects.filter(**filtres_ecarts).filter(statut='quantite_0').count()
        
        # Logique de calcul :
        # - Les écarts "ouverts" = commandes non intégrées
        # - Les écarts "résolus" = commandes considérées comme intégrées (même si pas encore dans Cyrus)
        # - Les écarts "ignorés" = commandes considérées comme intégrées
        # - Les écarts "quantite_0" = NE COMPTENT PAS dans les statistiques (exclus du total)
        # 
        # Total pour les statistiques = total_gpv_transmise - total_ecarts_quantite_0 (exclure les quantite_0)
        # Commandes intégrées = total_gpv_transmise - total_ecarts_ouverts - total_ecarts_quantite_0
        # (les écarts résolus et ignorés sont déjà comptés comme intégrés dans cette formule)
        
        # Exclure les commandes avec écart "quantite_0" du total affiché
        total_gpv_pour_stats = total_gpv_transmise - total_ecarts_quantite_0
        
        # Commandes intégrées = total - écarts ouverts - écarts quantite_0
        # (les écarts résolus et ignorés sont déjà dans le total, donc ils sont comptés comme intégrés)
        commandes_integres = total_gpv_transmise - total_ecarts_ouverts - total_ecarts_quantite_0
        commandes_non_integres = total_ecarts_ouverts
        
        # Calculer les taux basés sur le total sans les quantite_0
        taux_integration = round((commandes_integres / total_gpv_pour_stats * 100) if total_gpv_pour_stats > 0 else 0, 2)
        taux_non_integration = round((commandes_non_integres / total_gpv_pour_stats * 100) if total_gpv_pour_stats > 0 else 0, 2)
        
        # Normaliser les statistiques pour correspondre au template
        # Utiliser total_gpv_pour_stats pour exclure les quantite_0 du total affiché
        stats = {
            'total_source': total_gpv_pour_stats,  # Total sans les quantite_0
            'total_target': total_cyrus,
            'integres': commandes_integres,  # Seulement les "Transmise" intégrées
            'non_integres': commandes_non_integres,  # Calcul réel : total - intégrées
            'taux_integration': taux_integration,
            'taux_non_integration': taux_non_integration,
        }
        
        # Optimiser les requêtes : précharger les écarts
        commandes_gpv = CommandeGPV.objects.filter(**filtres_gpv).select_related('code_magasin').prefetch_related(
            Prefetch('ecart', queryset=EcartGPV.objects.all())
        ).order_by('-date_creation', 'numero_commande')
        
        # Limiter le nombre de commandes pour l'affichage (pagination implicite)
        # Charger seulement les 200 premières pour améliorer les performances
        commandes_gpv_limited = list(commandes_gpv[:200])
        
        # Créer un dictionnaire des commandes Cyrus pour lookup rapide
        # Filtrer seulement les commandes "Transmise" pour le lookup Cyrus
        cyrus_lookup = {}
        cyrus_pair_lookup = set()
        commandes_transmise = [cmd for cmd in commandes_gpv_limited if (cmd.statut or '').strip().upper() in ['TRANSMISE', 'TRANSMIS']]
        if commandes_transmise:
            # Récupérer les clés uniques des commandes GPV transmises
            gpv_keys = []
            gpv_pairs = []
            for cmd in commandes_transmise:
                gpv_keys.append((cmd.date_creation, cmd.numero_commande, cmd.code_magasin.code))
                gpv_pairs.append((cmd.numero_commande, cmd.code_magasin.code))
            
            # Construire une requête optimisée avec Q objects
            # Utiliser toutes les clés pour vérifier toutes les commandes affichées
            if gpv_keys:
                # Construire la requête Q par lots de 50 pour éviter les requêtes SQL trop complexes
                # mais traiter toutes les commandes affichées
                cyrus_commands_list = []
                for i in range(0, len(gpv_keys), 50):
                    batch_keys = gpv_keys[i:i+50]
                    q_objects = Q()
                    for date, numero, code in batch_keys:
                        q_objects |= Q(date_commande=date, numero_commande=numero, code_magasin__code=code)
                    
                    batch_cyrus = CommandeCyrus.objects.filter(q_objects).select_related('code_magasin')
                    cyrus_commands_list.extend(batch_cyrus)
                
                for cyrus_cmd in cyrus_commands_list:
                    key = (cyrus_cmd.date_commande, cyrus_cmd.numero_commande, cyrus_cmd.code_magasin.code)
                    cyrus_lookup[key] = cyrus_cmd
            
            # Lookup fallback : numéro + magasin (date différente)
            if gpv_pairs:
                gpv_pairs = list({pair for pair in gpv_pairs})
                cyrus_pairs_list = []
                for i in range(0, len(gpv_pairs), 50):
                    batch_pairs = gpv_pairs[i:i+50]
                    q_objects = Q()
                    for numero, code in batch_pairs:
                        q_objects |= Q(numero_commande=numero, code_magasin__code=code)
                    if q_objects:
                        cyrus_pairs_list.extend(
                            CommandeCyrus.objects.filter(q_objects).select_related('code_magasin')
                        )
                for cyrus_cmd in cyrus_pairs_list:
                    cyrus_pair_lookup.add((cyrus_cmd.numero_commande, cyrus_cmd.code_magasin.code))
        
        # Préparer les données pour l'affichage
        commandes_integres_list = []
        commandes_non_integres_list = []
        
        for cmd_gpv in commandes_gpv_limited:
            # Normaliser le statut
            statut_gpv = (cmd_gpv.statut or '').strip().upper()
            doit_etre_dans_cyrus = (statut_gpv == 'TRANSMISE' or statut_gpv == 'TRANSMIS')
            
            # Vérifier si intégrée dans Cyrus (lookup rapide dans le dictionnaire)
            cmd_cyrus = None
            if doit_etre_dans_cyrus:
                key = (cmd_gpv.date_creation, cmd_gpv.numero_commande, cmd_gpv.code_magasin.code)
                cmd_cyrus = cyrus_lookup.get(key)
                if cmd_cyrus is None:
                    pair_key = (cmd_gpv.numero_commande, cmd_gpv.code_magasin.code)
                    if pair_key in cyrus_pair_lookup:
                        cmd_cyrus = True
            
            # Récupérer l'écart (déjà préchargé avec prefetch_related)
            ecart = None
            if doit_etre_dans_cyrus:
                try:
                    ecart = cmd_gpv.ecart
                except:
                    ecart = None
            
            # Si l'écart est résolu, considérer comme intégré même si pas dans Cyrus
            # Si l'écart est quantite_0, ne pas le compter (ni intégré ni non intégré)
            is_integre = False
            if doit_etre_dans_cyrus:
                if ecart:
                    if ecart.statut == 'resolu':
                        is_integre = True  # Écart résolu = considéré comme intégré
                    elif ecart.statut == 'quantite_0':
                        continue  # Écart quantite_0 = exclu de l'affichage
                    elif ecart.statut == 'ignore':
                        is_integre = True  # Écart ignoré = considéré comme intégré
                    else:
                        is_integre = cmd_cyrus is not None  # Écart ouvert = vérifier si dans Cyrus
                else:
                    is_integre = cmd_cyrus is not None  # Pas d'écart = vérifier si dans Cyrus
            else:
                # Si le statut n'est pas "Transmise", ne pas créer d'écart et considérer comme intégré
                is_integre = True
            
            item = {
                'gpv': cmd_gpv,
                'cyrus': cmd_cyrus,
                'integre': is_integre,
                'ecart': ecart,
                'doit_etre_dans_cyrus': doit_etre_dans_cyrus,
            }
            
            # Séparer les intégrées et non intégrées
            if is_integre:
                commandes_integres_list.append(item)
            else:
                commandes_non_integres_list.append(item)
        
        # Mettre les non intégrées en premier, puis les intégrées (ou seulement non intégrées selon le filtre)
        if show == 'non_integres':
            commandes_data = commandes_non_integres_list
        else:
            commandes_data = commandes_non_integres_list + commandes_integres_list
        titre_tableau = "Comparaison GPV vs Cyrus"
        
    elif type_donnees == 'commandes_legend':
        # Récupérer les commandes Legend (seules les exportées sont éligibles)
        filtres_legend = {}
        if date_debut_parsed:
            filtres_legend['date_commande__gte'] = date_debut_parsed
        if date_fin_parsed:
            filtres_legend['date_commande__lte'] = date_fin_parsed

        # Statistiques basées uniquement sur les commandes exportées
        total_legend_exportee = CommandeLegend.objects.filter(exportee=True, **filtres_legend).count()

        # Total Cyrus sur la même période (comparaison sans code magasin)
        filtres_cyrus = {}
        if date_debut_parsed:
            filtres_cyrus['date_commande__gte'] = date_debut_parsed
        if date_fin_parsed:
            filtres_cyrus['date_commande__lte'] = date_fin_parsed
        total_cyrus = CommandeCyrus.objects.filter(**filtres_cyrus).count()

        # Compter les écarts Legend par statut
        filtres_ecarts = {'commande_legend__exportee': True}
        if date_debut_parsed:
            filtres_ecarts['commande_legend__date_commande__gte'] = date_debut_parsed
        if date_fin_parsed:
            filtres_ecarts['commande_legend__date_commande__lte'] = date_fin_parsed
        
        total_ecarts_ouverts = EcartLegend.objects.filter(**filtres_ecarts).filter(statut='ouvert').count()
        total_ecarts_resolus = EcartLegend.objects.filter(**filtres_ecarts).filter(statut='resolu').count()
        total_ecarts_ignores = EcartLegend.objects.filter(**filtres_ecarts).filter(statut='ignore').count()
        total_ecarts_quantite_0 = EcartLegend.objects.filter(**filtres_ecarts).filter(statut='quantite_0').count()
        
        # Logique de calcul :
        # - Les écarts "ouverts" = commandes non intégrées
        # - Les écarts "résolus" = commandes considérées comme intégrées (même si pas encore dans Cyrus)
        # - Les écarts "ignorés" = commandes considérées comme intégrées
        # - Les écarts "quantite_0" = NE COMPTENT PAS dans les statistiques (exclus du total)
        # 
        # Total pour les statistiques = total_legend_exportee - total_ecarts_quantite_0 (exclure les quantite_0)
        # Commandes intégrées = total_legend_exportee - total_ecarts_ouverts - total_ecarts_quantite_0
        # (les écarts résolus et ignorés sont déjà comptés comme intégrés dans cette formule)
        
        # Exclure les commandes avec écart "quantite_0" du total affiché
        total_legend_pour_stats = total_legend_exportee - total_ecarts_quantite_0
        
        # Commandes intégrées = total - écarts ouverts - écarts quantite_0
        # (les écarts résolus et ignorés sont déjà dans le total, donc ils sont comptés comme intégrés)
        commandes_integres = total_legend_exportee - total_ecarts_ouverts - total_ecarts_quantite_0
        commandes_non_integres = total_ecarts_ouverts

        # Calculer les taux basés sur le total sans les quantite_0
        taux_integration = round((commandes_integres / total_legend_pour_stats * 100) if total_legend_pour_stats > 0 else 0, 2)
        taux_non_integration = round((commandes_non_integres / total_legend_pour_stats * 100) if total_legend_pour_stats > 0 else 0, 2)

        # Normaliser les statistiques pour correspondre au template
        # Utiliser total_legend_pour_stats pour exclure les quantite_0 du total affiché
        stats = {
            'total_source': total_legend_pour_stats,  # Total sans les quantite_0
            'total_target': total_cyrus,
            'integres': commandes_integres,
            'non_integres': commandes_non_integres,
            'taux_integration': taux_integration,
            'taux_non_integration': taux_non_integration,
        }

        # Préparer les données pour l'affichage
        commandes_legend = CommandeLegend.objects.filter(**filtres_legend).prefetch_related(
            Prefetch('ecart', queryset=EcartLegend.objects.all())
        ).order_by('-date_commande', 'numero_commande')

        commandes_legend_limited = list(commandes_legend[:200])

        # Fonction de normalisation pour comparer les numéros de commande
        def normalize_numero(numero):
            """Normalise un numéro de commande pour la comparaison (enlève les zéros en tête)"""
            if not numero:
                return ''
            numero_str = str(numero).strip()
            # Extraire uniquement les chiffres
            digits = ''.join(ch for ch in numero_str if ch.isdigit())
            if digits:
                return digits.lstrip('0') or '0'
            return numero_str

        legend_keys = [(cmd.date_commande, normalize_numero(cmd.numero_commande)) for cmd in commandes_legend_limited]
        cyrus_lookup = set()
        cyrus_numero_lookup = set()

        if legend_keys:
            # Récupérer tous les numéros normalisés pour la recherche
            numeros_normalises = list({numero for _, numero in legend_keys})
            
            # Récupérer toutes les commandes Cyrus de la période
            filtres_cyrus_lookup = {}
            if date_debut_parsed:
                filtres_cyrus_lookup['date_commande__gte'] = date_debut_parsed
            if date_fin_parsed:
                filtres_cyrus_lookup['date_commande__lte'] = date_fin_parsed
            
            # Récupérer toutes les commandes Cyrus de la période et les normaliser
            for cyrus_cmd in CommandeCyrus.objects.filter(**filtres_cyrus_lookup):
                numero_normalise = normalize_numero(cyrus_cmd.numero_commande)
                cyrus_lookup.add((cyrus_cmd.date_commande, numero_normalise))
                cyrus_numero_lookup.add(numero_normalise)

        commandes_data = []
        for cmd_legend in commandes_legend_limited:
            numero_normalise = normalize_numero(cmd_legend.numero_commande)
            key = (cmd_legend.date_commande, numero_normalise)
            cyrus_present = key in cyrus_lookup
            if not cyrus_present:
                cyrus_present = numero_normalise in cyrus_numero_lookup

            try:
                ecart = cmd_legend.ecart
            except Exception:
                ecart = None

            integre = True
            etape_blocage = None
            if cmd_legend.exportee:
                # Si l'écart est résolu, considérer comme intégré même si pas dans Cyrus
                # Si l'écart est quantite_0, ne pas le compter (ni intégré ni non intégré)
                if ecart:
                    if ecart.statut == 'resolu':
                        integre = True  # Écart résolu = considéré comme intégré
                    elif ecart.statut == 'quantite_0':
                        continue  # Écart quantite_0 = exclu de l'affichage
                    elif ecart.statut == 'ignore':
                        integre = True  # Écart ignoré = considéré comme intégré
                    elif not cyrus_present:
                        integre = False  # Écart ouvert = vérifier si dans Cyrus
                        etape_blocage = "Absente dans Cyrus"
                elif not cyrus_present:
                    integre = False
                    etape_blocage = "Absente dans Cyrus"

            commandes_data.append({
                'legend': cmd_legend,
                'cyrus_present': cyrus_present,
                'integre': integre,
                'etape_blocage': etape_blocage,
                'ecart': ecart,
            })

        # Mettre les non intégrées en premier (False avant True)
        # Trier : non intégrées (integre=False) en premier, puis intégrées (integre=True)
        def sort_legend_key(x):
            # Priorité 0 pour non intégrées (False), 1 pour intégrées (True)
            priority = 1 if x['integre'] else 0
            # Date la plus récente en premier
            try:
                date_cmd = x['legend'].date_commande
                if hasattr(date_cmd, 'timestamp'):
                    date_timestamp = date_cmd.timestamp()
                else:
                    date_timestamp = 0
            except:
                date_timestamp = 0
            return (priority, -date_timestamp)
        
        commandes_data.sort(key=sort_legend_key)
        titre_tableau = "Comparaison Legend vs Cyrus"
        
    elif type_donnees == 'factures':
        # TODO: À implémenter quand les modèles Factures seront créés
        stats = {
            'total_source': 0,
            'total_target': 0,
            'integres': 0,
            'non_integres': 0,
            'taux_integration': 0,
            'taux_non_integration': 0,
        }
        titre_tableau = "Comparaison Factures Asten vs Cyrus"
        
    elif type_donnees == 'br':
        # BR ASTEN (statut IC fourni dans le fichier)
        # IMPORTANT: Les statistiques en haut affichent TOUJOURS le total global (sans filtre de date)
        # Par défaut, on affiche tous les BR non intégrés (sans filtre de date)
        
        # Calculer les statistiques GLOBALES (sans filtre de date) pour l'affichage en haut
        filtres_br_global = {}
        if code_magasin:
            filtres_br_global['code_magasin__code__in'] = code_magasin
        
        br_queryset_global = BRAsten.objects.filter(**filtres_br_global)
        br_quantite_0_global = br_queryset_global.filter(
            Q(statut_ic__icontains='Quantité 0') | 
            Q(statut_ic__icontains='quantite_0') |
            Q(statut_ic__icontains='Quantite 0')
        ).count()
        
        total_asten_global = br_queryset_global.count()
        total_asten_pour_stats_global = total_asten_global - br_quantite_0_global
        
        br_trouvees_count_global = br_queryset_global.filter(ic_integre=True).exclude(
            Q(statut_ic__icontains='Quantité 0') | 
            Q(statut_ic__icontains='quantite_0') |
            Q(statut_ic__icontains='Quantite 0')
        ).count()
        br_non_trouvees_count_global = br_queryset_global.filter(ic_integre=False).exclude(
            Q(statut_ic__icontains='Quantité 0') | 
            Q(statut_ic__icontains='quantite_0') |
            Q(statut_ic__icontains='Quantite 0')
        ).count()

        # Calculer les taux basés sur le total global
        # Utiliser plus de décimales pour les petits pourcentages
        taux_integration_global = round((br_trouvees_count_global / total_asten_pour_stats_global * 100) if total_asten_pour_stats_global > 0 else 0, 3)
        taux_non_integration_global = round((br_non_trouvees_count_global / total_asten_pour_stats_global * 100) if total_asten_pour_stats_global > 0 else 0, 3)

        # Statistiques globales pour l'affichage en haut
        stats = {
            'total_source': total_asten_pour_stats_global,  # Total global sans les quantite_0
            'total_target': br_trouvees_count_global,
            'integres': br_trouvees_count_global,
            'non_integres': br_non_trouvees_count_global,
            'trouvees': br_trouvees_count_global,
            'non_trouvees': br_non_trouvees_count_global,
            'taux_integration': taux_integration_global,
            'taux_non_integration': taux_non_integration_global,
        }

        # Pour les tableaux : TOUJOURS afficher tous les BR non intégrés par défaut (SANS filtre de date)
        # Même si une période est sélectionnée, on affiche tous les BR non intégrés
        # L'utilisateur peut utiliser le filtre statut_ic pour voir les intégrés
        filtres_br_tableaux = {}
        # Ne PAS appliquer le filtre de date aux tableaux - toujours afficher tous les BR non intégrés
        # Le filtre de date est utilisé uniquement pour les statistiques si nécessaire, mais ici on veut toujours tout afficher
        if code_magasin:
            filtres_br_tableaux['code_magasin__code__in'] = code_magasin

        statut_ic = request.GET.get('statut_ic')
        br_queryset_base = BRAsten.objects.filter(**filtres_br_tableaux)
        
        # Créer deux querysets séparés : un pour les trouvées et un pour les non trouvées
        # Par défaut, afficher TOUS les BR non intégrés dans le tableau non trouvées
        # Mais toujours afficher les trouvées aussi
        if statut_ic == 'integre':
            # Si on filtre sur intégrés, ne montrer que les intégrés
            br_trouvees = br_queryset_base.filter(ic_integre=True).select_related('code_magasin').order_by('-date_br', 'numero_br')[:200]
            br_non_trouvees = BRAsten.objects.none()  # Ne rien afficher dans non trouvées
        elif statut_ic == 'non_integre':
            # Si on filtre sur non intégrés, ne montrer que les non intégrés
            br_trouvees = BRAsten.objects.none()  # Ne rien afficher dans trouvées
            br_non_trouvees = br_queryset_base.filter(ic_integre=False).select_related('code_magasin').order_by('-date_br', 'numero_br')[:200]
        else:
            # Par défaut : afficher TOUS les BR non intégrés dans non trouvées, ET tous les intégrés dans trouvées
            br_trouvees = br_queryset_base.filter(ic_integre=True).select_related('code_magasin').order_by('-date_br', 'numero_br')[:200]
            br_non_trouvees = br_queryset_base.filter(ic_integre=False).select_related('code_magasin').order_by('-date_br', 'numero_br')[:200]
        commandes_data = []
        titre_tableau = "BR ASTEN (Statut IC)"
    
    context = {
        'stats': stats,
        'commandes': commandes_data,
        'br_trouvees': br_trouvees if type_donnees == 'br' else None,
        'br_non_trouvees': br_non_trouvees if type_donnees == 'br' else None,
        'magasins': magasins,
        'type_donnees': type_donnees,
        'titre_tableau': titre_tableau,
        'stats_label_source': 'Asten' if type_donnees in ['commandes_asten', 'br'] else 'Source',
        'stats_label_target': 'IC' if type_donnees == 'br' else 'Cyrus',
        'filtres': {
            'date_debut': date_debut or '',
            'date_fin': date_fin or '',
            'magasin': code_magasin if code_magasin else [],
            'type_donnees': type_donnees,
            'statut_ic': statut_ic if type_donnees == 'br' else '',
        },
        'periode': periode,
        'show': show,
    }
    
    return render(request, 'dashboard/dashboard.html', context)


def accueil(request):
    """Vue d'accueil affichant toutes les statistiques en un coup d'œil"""
    from django.db.models import Q, Exists, OuterRef
    from datetime import datetime, timedelta
    from django.utils import timezone
    
    # Gérer les filtres de période
    periode = request.GET.get('periode', 'tous')  # Par défaut: 'tous' pour afficher toutes les données
    date_debut = None
    date_fin = None
    
    # Si periode est 'tous' ou vide, ne pas appliquer de filtre de date
    if periode == 'tous' or periode == '':
        date_debut = None
        date_fin = None
    elif periode == 'aujourdhui':
        date_debut = timezone.now().date()
        date_fin = timezone.now().date()
    elif periode == 'hier':
        date_debut = timezone.now().date() - timedelta(days=1)
        date_fin = timezone.now().date() - timedelta(days=1)
    elif periode == 'semaine':
        date_fin = timezone.now().date()
        date_debut = date_fin - timedelta(days=7)
    elif periode == 'mois':
        date_fin = timezone.now().date()
        date_debut = date_fin - timedelta(days=30)
    elif periode == '3mois':
        date_fin = timezone.now().date()
        date_debut = date_fin - timedelta(days=90)
    elif periode == 'annee':
        date_fin = timezone.now().date()
        date_debut = date_fin.replace(month=1, day=1)  # 1er janvier de l'année en cours
    elif periode == 'personnalise':
        date_debut_str = request.GET.get('date_debut', '')
        date_fin_str = request.GET.get('date_fin', '')
        if date_debut_str:
            try:
                date_debut = parse_date(date_debut_str)
            except:
                date_debut = None
        if date_fin_str:
            try:
                date_fin = parse_date(date_fin_str)
            except:
                date_fin = None
    
    # Calculer les statistiques pour chaque type de données
    stats_asten = {}
    stats_gpv = {}
    stats_legend = {}
    stats_br = {}
    stats_factures = {}
    
    # ASTEN
    try:
        filtres_asten = {}
        if date_debut:
            filtres_asten['date_commande__gte'] = date_debut
        if date_fin:
            filtres_asten['date_commande__lte'] = date_fin
        
        total_asten = CommandeAsten.objects.filter(**filtres_asten).count()
        
        filtres_ecarts_asten = {}
        if date_debut:
            filtres_ecarts_asten['commande_asten__date_commande__gte'] = date_debut
        if date_fin:
            filtres_ecarts_asten['commande_asten__date_commande__lte'] = date_fin
        
        total_ecarts_ouverts_asten = EcartCommande.objects.filter(**filtres_ecarts_asten).filter(statut='ouvert').count()
        total_ecarts_quantite_0_asten = EcartCommande.objects.filter(**filtres_ecarts_asten).filter(statut='quantite_0').count()
        total_asten_pour_stats = total_asten - total_ecarts_quantite_0_asten
        commandes_integres_asten = total_asten - total_ecarts_ouverts_asten - total_ecarts_quantite_0_asten
        commandes_non_integres_asten = total_ecarts_ouverts_asten
        taux_integration_asten = round((commandes_integres_asten / total_asten_pour_stats * 100) if total_asten_pour_stats > 0 else 0, 2)
        taux_non_integration_asten = round((commandes_non_integres_asten / total_asten_pour_stats * 100) if total_asten_pour_stats > 0 else 0, 2)
        stats_asten = {
            'total': total_asten_pour_stats,
            'integres': commandes_integres_asten,
            'non_integres': commandes_non_integres_asten,
            'taux_integration': taux_integration_asten,
            'taux_non_integration': taux_non_integration_asten,
        }
    except:
        stats_asten = {'total': 0, 'integres': 0, 'non_integres': 0, 'taux_integration': 0, 'taux_non_integration': 0}
    
    # GPV
    try:
        filtres_gpv = {'statut__iexact': 'Transmise'}
        if date_debut:
            filtres_gpv['date_creation__gte'] = date_debut
        if date_fin:
            filtres_gpv['date_creation__lte'] = date_fin
        
        total_gpv_transmise = CommandeGPV.objects.filter(**filtres_gpv).count()
        
        filtres_ecarts_gpv = {}
        if date_debut:
            filtres_ecarts_gpv['commande_gpv__date_creation__gte'] = date_debut
        if date_fin:
            filtres_ecarts_gpv['commande_gpv__date_creation__lte'] = date_fin
        
        total_ecarts_ouverts_gpv = EcartGPV.objects.filter(**filtres_ecarts_gpv).filter(statut='ouvert').count()
        total_ecarts_quantite_0_gpv = EcartGPV.objects.filter(**filtres_ecarts_gpv).filter(statut='quantite_0').count()
        total_gpv_pour_stats = total_gpv_transmise - total_ecarts_quantite_0_gpv
        commandes_integres_gpv = total_gpv_transmise - total_ecarts_ouverts_gpv - total_ecarts_quantite_0_gpv
        commandes_non_integres_gpv = total_ecarts_ouverts_gpv
        taux_integration_gpv = round((commandes_integres_gpv / total_gpv_pour_stats * 100) if total_gpv_pour_stats > 0 else 0, 2)
        taux_non_integration_gpv = round((commandes_non_integres_gpv / total_gpv_pour_stats * 100) if total_gpv_pour_stats > 0 else 0, 2)
        stats_gpv = {
            'total': total_gpv_pour_stats,
            'integres': commandes_integres_gpv,
            'non_integres': commandes_non_integres_gpv,
            'taux_integration': taux_integration_gpv,
            'taux_non_integration': taux_non_integration_gpv,
        }
    except:
        stats_gpv = {'total': 0, 'integres': 0, 'non_integres': 0, 'taux_integration': 0, 'taux_non_integration': 0}
    
    # LEGEND
    try:
        filtres_legend = {'exportee': True}
        if date_debut:
            filtres_legend['date_commande__gte'] = date_debut
        if date_fin:
            filtres_legend['date_commande__lte'] = date_fin
        
        total_legend_exportee = CommandeLegend.objects.filter(**filtres_legend).count()
        
        filtres_ecarts_legend = {'commande_legend__exportee': True}
        if date_debut:
            filtres_ecarts_legend['commande_legend__date_commande__gte'] = date_debut
        if date_fin:
            filtres_ecarts_legend['commande_legend__date_commande__lte'] = date_fin
        
        total_ecarts_ouverts_legend = EcartLegend.objects.filter(**filtres_ecarts_legend).filter(statut='ouvert').count()
        total_ecarts_quantite_0_legend = EcartLegend.objects.filter(**filtres_ecarts_legend).filter(statut='quantite_0').count()
        total_legend_pour_stats = total_legend_exportee - total_ecarts_quantite_0_legend
        commandes_integres_legend = total_legend_exportee - total_ecarts_ouverts_legend - total_ecarts_quantite_0_legend
        commandes_non_integres_legend = total_ecarts_ouverts_legend
        taux_integration_legend = round((commandes_integres_legend / total_legend_pour_stats * 100) if total_legend_pour_stats > 0 else 0, 2)
        taux_non_integration_legend = round((commandes_non_integres_legend / total_legend_pour_stats * 100) if total_legend_pour_stats > 0 else 0, 2)
        stats_legend = {
            'total': total_legend_pour_stats,
            'integres': commandes_integres_legend,
            'non_integres': commandes_non_integres_legend,
            'taux_integration': taux_integration_legend,
            'taux_non_integration': taux_non_integration_legend,
        }
    except:
        stats_legend = {'total': 0, 'integres': 0, 'non_integres': 0, 'taux_integration': 0, 'taux_non_integration': 0}
    
    # BR
    try:
        filtres_br = {}
        if date_debut:
            filtres_br['date_br__gte'] = date_debut
        if date_fin:
            filtres_br['date_br__lte'] = date_fin
        
        br_quantite_0 = BRAsten.objects.filter(**filtres_br).filter(
            Q(statut_ic__icontains='Quantité 0') | 
            Q(statut_ic__icontains='quantite_0') |
            Q(statut_ic__icontains='Quantite 0')
        ).count()
        total_br = BRAsten.objects.filter(**filtres_br).count()
        total_br_pour_stats = total_br - br_quantite_0
        br_trouvees = BRAsten.objects.filter(**filtres_br, ic_integre=True).exclude(
            Q(statut_ic__icontains='Quantité 0') | 
            Q(statut_ic__icontains='quantite_0') |
            Q(statut_ic__icontains='Quantite 0')
        ).count()
        br_non_trouvees = BRAsten.objects.filter(**filtres_br, ic_integre=False).exclude(
            Q(statut_ic__icontains='Quantité 0') | 
            Q(statut_ic__icontains='quantite_0') |
            Q(statut_ic__icontains='Quantite 0')
        ).count()
        taux_integration_br = round((br_trouvees / total_br_pour_stats * 100) if total_br_pour_stats > 0 else 0, 2)
        taux_non_integration_br = round((br_non_trouvees / total_br_pour_stats * 100) if total_br_pour_stats > 0 else 0, 2)
        stats_br = {
            'total': total_br_pour_stats,
            'integres': br_trouvees,
            'non_integres': br_non_trouvees,
            'taux_integration': taux_integration_br,
            'taux_non_integration': taux_non_integration_br,
        }
    except:
        stats_br = {'total': 0, 'integres': 0, 'non_integres': 0, 'taux_integration': 0, 'taux_non_integration': 0}
    
    # FACTURES (pour l'instant vide, à implémenter plus tard)
    stats_factures = {'total': 0, 'integres': 0, 'non_integres': 0, 'taux_integration': 0, 'taux_non_integration': 0}
    
    # REMONTÉES (Tickets)
    try:
        filtres_remontees = {}
        if date_debut:
            filtres_remontees['date_creation__date__gte'] = date_debut
        if date_fin:
            filtres_remontees['date_creation__date__lte'] = date_fin
        
        total_remontees = Ticket.objects.filter(**filtres_remontees).count()
        resolu_remontees = Ticket.objects.filter(**filtres_remontees, statut=Ticket.STATUT_RESOLU).count()
        en_cours_remontees = Ticket.objects.filter(**filtres_remontees, statut=Ticket.STATUT_EN_COURS).count()
        en_attente_remontees = Ticket.objects.filter(**filtres_remontees, statut=Ticket.STATUT_EN_ATTENTE).count()
        ferme_remontees = Ticket.objects.filter(**filtres_remontees, statut=Ticket.STATUT_FERME).count()
        non_resolu_remontees = total_remontees - resolu_remontees - ferme_remontees
        taux_resolu_remontees = round((resolu_remontees / total_remontees * 100) if total_remontees > 0 else 0, 2)
        stats_remontees = {
            'total': total_remontees,
            'resolu': resolu_remontees,
            'en_cours': en_cours_remontees,
            'en_attente': en_attente_remontees,
            'ferme': ferme_remontees,
            'non_resolu': non_resolu_remontees,
            'taux_resolu': taux_resolu_remontees,
        }
    except:
        stats_remontees = {
            'total': 0, 
            'resolu': 0, 
            'en_cours': 0, 
            'en_attente': 0, 
            'ferme': 0, 
            'non_resolu': 0, 
            'taux_resolu': 0,
            'taux_non_resolu': 0
        }
    
    context = {
        'stats_asten': stats_asten,
        'stats_gpv': stats_gpv,
        'stats_legend': stats_legend,
        'stats_br': stats_br,
        'stats_factures': stats_factures,
        'stats_remontees': stats_remontees,
        'periode': periode,
        'date_debut': date_debut.strftime('%Y-%m-%d') if date_debut else '',
        'date_fin': date_fin.strftime('%Y-%m-%d') if date_fin else '',
    }
    
    return render(request, 'dashboard/accueil.html', context)


@require_http_methods(["POST"])
def actualiser_donnees(request):
    """Actualise TOUTES les données globalement : importe les fichiers et recalcule les écarts pour tous les types"""
    try:
        # ACTUALISATION GLOBALE : Scanner et importer les nouveaux fichiers pour TOUS les types
        # (Asten, GPV, Legend, Factures, BR - quand ils seront implémentés)
        fichiers_importes = scanner_et_importer_fichiers()
        
        # Recalculer les écarts pour TOUS les types
        resultat_ecarts = recalculer_ecarts()
        
        # recalculer_ecarts() retourne maintenant un dictionnaire
        if isinstance(resultat_ecarts, dict):
            nombre_ecarts_crees = resultat_ecarts.get('ecarts_crees', 0)
            nombre_ecarts_resolus = resultat_ecarts.get('ecarts_resolus', 0)
        else:
            # Compatibilité avec l'ancien format
            nombre_ecarts_crees = resultat_ecarts if isinstance(resultat_ecarts, int) else 0
            nombre_ecarts_resolus = 0
        
        # TODO: Quand les autres types seront implémentés, ajouter ici :
        # - scanner_et_importer_fichiers_gpv()
        # - scanner_et_importer_fichiers_legend()
        # - scanner_et_importer_fichiers_factures()
        # - scanner_et_importer_fichiers_br()
        # - recalculer_ecarts_gpv()
        # - recalculer_ecarts_legend()
        # - etc.
        
        # Les données sont maintenant en base de données et restent PERMANENTES
        # même si on change de type (Asten, GPV, Legend, Factures, BR)
        # Réinitialiser le flag de session pour permettre une nouvelle actualisation automatique
        request.session['donnees_actualisees'] = False
        
        message = f"Actualisation globale réussie ! {len(fichiers_importes)} fichier(s) importé(s)."
        if nombre_ecarts_crees > 0:
            message += f" {nombre_ecarts_crees} nouvel(le)(s) écart(s) détecté(s)."
        if nombre_ecarts_resolus > 0:
            message += f" {nombre_ecarts_resolus} écart(s) résolu(s) automatiquement."
        message += " Toutes les données sont maintenant à jour et permanentes."
        
        messages.success(request, message)
    except Exception as e:
        messages.error(request, f"Erreur lors de l'actualisation : {str(e)}")
    
    # Préserver le type de données dans la redirection
    type_donnees = request.POST.get('type_donnees', 'commandes_asten')
    redirect_url = f"{reverse('dashboard:dashboard')}?type_donnees={type_donnees}"
    
    return redirect(redirect_url)


def detail_ecart(request, ecart_id):
    """Affiche le détail d'un écart et permet de modifier son statut"""
    from ecarts.models import EcartCommande
    from cyrus.models import CommandeCyrus
    
    try:
        ecart = EcartCommande.objects.select_related('commande_asten__code_magasin').get(pk=ecart_id)
        
        # Vérifier si la commande existe maintenant dans Cyrus
        existe_cyrus = CommandeCyrus.objects.filter(
            date_commande=ecart.commande_asten.date_commande,
            numero_commande=ecart.commande_asten.numero_commande,
            code_magasin=ecart.commande_asten.code_magasin
        ).first()
        
        # Gérer la modification du statut
        if request.method == 'POST':
            nouveau_statut = request.POST.get('statut')
            commentaire = request.POST.get('commentaire', '').strip()
            
            if nouveau_statut in ['ouvert', 'resolu', 'ignore', 'quantite_0']:
                # Ne PAS supprimer l'écart, mais le garder avec le statut modifié
                # Cela permet de préserver les modifications manuelles lors du recalcul
                ecart.statut = nouveau_statut
                if commentaire:
                    ecart.commentaire = commentaire
                ecart.save()
                
                if nouveau_statut == 'resolu':
                    messages.success(request, "L'écart a été marqué comme résolu. La commande sera comptée comme intégrée. Les pourcentages seront mis à jour sur le dashboard.")
                elif nouveau_statut == 'ignore':
                    messages.info(request, "L'écart a été marqué comme ignoré. Les pourcentages seront mis à jour sur le dashboard.")
                elif nouveau_statut == 'quantite_0':
                    messages.info(request, "L'écart a été marqué comme 'Quantité 0'. La commande ne sera comptée ni comme intégrée ni comme non intégrée. Les pourcentages seront mis à jour sur le dashboard.")
                else:
                    messages.info(request, "L'écart a été remis à ouvert. Les pourcentages seront mis à jour sur le dashboard.")
                
                # Rediriger vers le dashboard pour que les pourcentages soient recalculés
                type_donnees = request.GET.get('type_donnees', 'commandes_asten')
                return redirect(f"{reverse('dashboard:dashboard')}?type_donnees={type_donnees}")
        
        context = {
            'ecart': ecart,
            'existe_cyrus': existe_cyrus,
        }
        return render(request, 'dashboard/detail_ecart.html', context)
    except EcartCommande.DoesNotExist:
        messages.error(request, "Écart introuvable.")
        return redirect('dashboard:dashboard')


def liste_ecarts(request):
    """Affiche la liste des écarts (Asten, GPV et Legend)"""
    # Filtres
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    code_magasin = request.GET.get('magasin')
    statut = request.GET.get('statut', '')  # Par défaut, afficher tous les statuts
    type_ecart = request.GET.get('type_ecart', '')  # Filtre par type : asten, gpv, legend, br, factures
    
    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed = parse_date(date_fin) if date_fin else None
    
    # Construire les filtres pour Asten
    filtres_asten = {}
    filtres_gpv = {}
    filtres_legend = {}
    
    # Filtrer par statut seulement si un statut spécifique est sélectionné
    if statut and statut != '':
        filtres_asten['statut'] = statut
        filtres_gpv['statut'] = statut
        filtres_legend['statut'] = statut
    
    if date_debut_parsed:
        filtres_asten['commande_asten__date_commande__gte'] = date_debut_parsed
        filtres_gpv['commande_gpv__date_creation__gte'] = date_debut_parsed
        filtres_legend['commande_legend__date_commande__gte'] = date_debut_parsed
    if date_fin_parsed:
        filtres_asten['commande_asten__date_commande__lte'] = date_fin_parsed
        filtres_gpv['commande_gpv__date_creation__lte'] = date_fin_parsed
        filtres_legend['commande_legend__date_commande__lte'] = date_fin_parsed
    if code_magasin:
        filtres_asten['commande_asten__code_magasin__code'] = code_magasin
        filtres_gpv['commande_gpv__code_magasin__code'] = code_magasin
    
    # Récupérer les écarts Asten (exclure les résolus)
    filtres_asten_exclus = filtres_asten.copy()
    if not statut or statut == '':
        # Par défaut, exclure les écarts résolus
        filtres_asten_exclus['statut__in'] = ['ouvert', 'ignore']
    ecarts_asten = EcartCommande.objects.filter(**filtres_asten_exclus).select_related(
        'commande_asten__code_magasin'
    ).order_by('-date_creation')
    
    # Récupérer les écarts GPV (exclure les résolus)
    filtres_gpv_exclus = filtres_gpv.copy()
    if not statut or statut == '':
        # Par défaut, exclure les écarts résolus
        filtres_gpv_exclus['statut__in'] = ['ouvert', 'ignore']
    ecarts_gpv = EcartGPV.objects.filter(**filtres_gpv_exclus).select_related(
        'commande_gpv__code_magasin'
    ).order_by('-date_creation')

    # Récupérer les écarts Legend (exclure les résolus)
    filtres_legend_exclus = filtres_legend.copy()
    if not statut or statut == '':
        # Par défaut, exclure les écarts résolus
        filtres_legend_exclus['statut__in'] = ['ouvert', 'ignore']
    ecarts_legend = EcartLegend.objects.filter(**filtres_legend_exclus).select_related(
        'commande_legend'
    ).order_by('-date_creation')
    
    
    # Combiner les écarts avec un indicateur de type
    # Exclure les écarts résolus automatiquement (statut "resolu" ET commande existe dans Cyrus)
    ecarts_combined = []
    
    for ecart in ecarts_asten:
        # Vérifier si la commande existe dans Cyrus
        existe_cyrus = CommandeCyrus.objects.filter(
            date_commande=ecart.commande_asten.date_commande,
            numero_commande=ecart.commande_asten.numero_commande,
            code_magasin=ecart.commande_asten.code_magasin
        ).exists()
        
        # Ne pas afficher les écarts résolus automatiquement (résolu ET existe dans Cyrus)
        # Afficher seulement : ouverts, ignorés, et résolus manuellement (résolu mais n'existe pas dans Cyrus)
        if ecart.statut == 'resolu' and existe_cyrus:
            # Écart résolu automatiquement, ne pas l'afficher
            continue
        
        ecarts_combined.append({
            'type': 'asten',
            'ecart': ecart,
            'id': ecart.id,
            'date_commande': ecart.commande_asten.date_commande,
            'numero_commande': ecart.commande_asten.numero_commande,
            'code_magasin': ecart.commande_asten.code_magasin,
            'montant': ecart.commande_asten.montant,
            'date_creation': ecart.date_creation,
            'statut': ecart.statut,
        })
    
    for ecart in ecarts_gpv:
        # Vérifier si la commande existe dans Cyrus
        existe_cyrus = CommandeCyrus.objects.filter(
            Q(date_commande=ecart.commande_gpv.date_creation) |
            Q(numero_commande=ecart.commande_gpv.numero_commande, code_magasin=ecart.commande_gpv.code_magasin),
            numero_commande=ecart.commande_gpv.numero_commande,
            code_magasin=ecart.commande_gpv.code_magasin
        ).exists()
        
        # Ne pas afficher les écarts résolus automatiquement (résolu ET existe dans Cyrus)
        # Afficher seulement : ouverts, ignorés, et résolus manuellement (résolu mais n'existe pas dans Cyrus)
        if ecart.statut == 'resolu' and existe_cyrus:
            # Écart résolu automatiquement, ne pas l'afficher
            continue
        
        ecarts_combined.append({
            'type': 'gpv',
            'ecart': ecart,
            'id': ecart.id,
            'date_commande': ecart.commande_gpv.date_creation,  # Utiliser date_creation pour GPV
            'numero_commande': ecart.commande_gpv.numero_commande,
            'code_magasin': ecart.commande_gpv.code_magasin,
            'montant': None,  # GPV n'a pas de montant
            'date_creation': ecart.date_creation,
            'statut': ecart.statut,
        })

    # Fonction de normalisation pour comparer les numéros
    def normalize_numero(numero):
        if not numero:
            return ''
        numero_str = str(numero).strip()
        digits = ''.join(ch for ch in numero_str if ch.isdigit())
        if digits:
            return digits.lstrip('0') or '0'
        return numero_str

    for ecart in ecarts_legend:
        # Normaliser le numéro Legend
        numero_legend_normalise = normalize_numero(ecart.commande_legend.numero_commande)
        
        # Vérifier si la commande existe dans Cyrus avec normalisation
        existe_cyrus = False
        for cyrus_cmd in CommandeCyrus.objects.filter(date_commande=ecart.commande_legend.date_commande):
            if normalize_numero(cyrus_cmd.numero_commande) == numero_legend_normalise:
                existe_cyrus = True
                break
        
        if not existe_cyrus:
            # Fallback: chercher par numéro seulement (sans date)
            for cyrus_cmd in CommandeCyrus.objects.all():
                if normalize_numero(cyrus_cmd.numero_commande) == numero_legend_normalise:
                    existe_cyrus = True
                    break

        # Ne pas afficher les écarts résolus automatiquement (résolu ET existe dans Cyrus)
        # Afficher seulement : ouverts, ignorés, et résolus manuellement (résolu mais n'existe pas dans Cyrus)
        if ecart.statut == 'resolu' and existe_cyrus:
            continue

        ecarts_combined.append({
            'type': 'legend',
            'ecart': ecart,
            'id': ecart.id,
            'date_commande': ecart.commande_legend.date_commande,
            'numero_commande': ecart.commande_legend.numero_commande,
            'depot_origine': ecart.commande_legend.depot_origine,
            'depot_destination': ecart.commande_legend.depot_destination,
            'montant': None,
            'date_creation': ecart.date_creation,
            'statut': ecart.statut,
        })
    
    # Filtrer par type si spécifié
    if type_ecart and type_ecart != '':
        ecarts_combined = [e for e in ecarts_combined if e['type'] == type_ecart]
    
    # Trier : non intégrés (statut 'ouvert') en premier, puis les autres
    # Priorité : 1. statut 'ouvert' (non intégré), 2. date de création (plus récent en premier)
    from django.utils import timezone
    def sort_key(ecart):
        # Si statut 'ouvert', priorité 0 (en premier), sinon priorité 1
        priority = 0 if ecart['statut'] == 'ouvert' else 1
        # Convertir la date en timestamp pour le tri
        date_creation = ecart['date_creation']
        try:
            if isinstance(date_creation, datetime):
                if timezone.is_aware(date_creation):
                    date_timestamp = date_creation.timestamp()
                else:
                    date_timestamp = timezone.make_aware(date_creation).timestamp()
            elif hasattr(date_creation, 'timestamp'):
                date_timestamp = date_creation.timestamp()
            else:
                # Si c'est une date naive, la convertir
                if isinstance(date_creation, datetime):
                    date_timestamp = timezone.make_aware(date_creation).timestamp()
                else:
                    date_timestamp = 0
        except:
            date_timestamp = 0
        return (priority, -date_timestamp)
    
    ecarts_combined.sort(key=sort_key)

    paginator = Paginator(ecarts_combined, 50)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    magasins = Magasin.objects.all().order_by('code')
    
    context = {
        'ecarts': page_obj,
        'ecarts_count': paginator.count,
        'page_obj': page_obj,
        'titre': "Liste des Écarts",
        'magasins': magasins,
        'filtres': {
            'date_debut': date_debut or '',
            'date_fin': date_fin or '',
            'magasin': code_magasin or '',
            'statut': statut or '',
            'type_ecart': type_ecart or '',
        }
    }
    
    return render(request, 'dashboard/liste_ecarts.html', context)




def liste_commandes_asten(request):
    """Affiche la liste des commandes Asten"""
    # Filtres
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    codes_magasins = request.GET.getlist('magasin')  # Récupérer plusieurs valeurs
    numero_commande = request.GET.get('numero_commande', '').strip()
    recherche_magasin = request.GET.get('recherche_magasin', '').strip()  # Recherche par code ou nom
    
    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed = parse_date(date_fin) if date_fin else None
    
    filtres = {}
    if date_debut_parsed:
        filtres['date_commande__gte'] = date_debut_parsed
    if date_fin_parsed:
        filtres['date_commande__lte'] = date_fin_parsed
    if codes_magasins:
        filtres['code_magasin__code__in'] = codes_magasins
    if numero_commande:
        filtres['numero_commande__icontains'] = numero_commande
    
    commandes = CommandeAsten.objects.filter(**filtres).select_related(
        'code_magasin'
    ).order_by('-date_commande', 'numero_commande')
    
    # Pagination pour améliorer les performances
    paginator = Paginator(commandes, 50)  # 50 commandes par page
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # Charger tous les magasins pour le select (le filtrage se fait côté client)
    magasins = Magasin.objects.all().order_by('code')
    
    context = {
        'commandes': page_obj,
        'page_obj': page_obj,
        'magasins': magasins,
        'filtres': {
            'date_debut': date_debut,
            'date_fin': date_fin,
            'magasin': codes_magasins,
            'numero_commande': numero_commande,
            'recherche_magasin': recherche_magasin,
        },
        'total': paginator.count,
    }
    
    return render(request, 'dashboard/liste_commandes_asten.html', context)


def liste_commandes_cyrus(request):
    """Affiche la liste des commandes Cyrus"""
    # Filtres
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    codes_magasins = request.GET.getlist('magasin')  # Récupérer plusieurs valeurs
    numero_commande = request.GET.get('numero_commande', '').strip()
    recherche_magasin = request.GET.get('recherche_magasin', '').strip()  # Recherche par code ou nom
    
    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed = parse_date(date_fin) if date_fin else None
    
    filtres = {}
    if date_debut_parsed:
        filtres['date_commande__gte'] = date_debut_parsed
    if date_fin_parsed:
        filtres['date_commande__lte'] = date_fin_parsed
    if codes_magasins:
        filtres['code_magasin__code__in'] = codes_magasins
    if numero_commande:
        filtres['numero_commande__icontains'] = numero_commande
    
    commandes = CommandeCyrus.objects.filter(**filtres).select_related(
        'code_magasin'
    ).order_by('-date_commande', 'numero_commande')
    
    # Pagination pour améliorer les performances
    paginator = Paginator(commandes, 50)  # 50 commandes par page
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # Charger tous les magasins pour le select (le filtrage se fait côté client)
    magasins = Magasin.objects.all().order_by('code')
    
    context = {
        'commandes': page_obj,
        'page_obj': page_obj,
        'magasins': magasins,
        'filtres': {
            'date_debut': date_debut,
            'date_fin': date_fin,
            'magasin': codes_magasins,
            'numero_commande': numero_commande,
            'recherche_magasin': recherche_magasin,
        },
        'total': paginator.count,
    }
    
    return render(request, 'dashboard/liste_commandes_cyrus.html', context)


def liste_br_asten(request):
    """Affiche la liste des BR Asten"""
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    codes_magasins = request.GET.getlist('magasin')
    numero_br = request.GET.get('numero_br', '').strip()
    statut_ic = request.GET.get('statut_ic', '')

    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed = parse_date(date_fin) if date_fin else None

    filtres = {}
    if date_debut_parsed:
        filtres['date_br__gte'] = date_debut_parsed
    if date_fin_parsed:
        filtres['date_br__lte'] = date_fin_parsed
    if codes_magasins:
        filtres['code_magasin__code__in'] = codes_magasins
    if numero_br:
        filtres['numero_br__icontains'] = numero_br
    if statut_ic == 'integre':
        filtres['ic_integre'] = True
    elif statut_ic == 'non_integre':
        filtres['ic_integre'] = False

    brs = BRAsten.objects.filter(**filtres).select_related('code_magasin').order_by('-date_br', 'numero_br')

    paginator = Paginator(brs, 50)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    magasins = Magasin.objects.all().order_by('code')

    context = {
        'brs': page_obj,
        'page_obj': page_obj,
        'magasins': magasins,
        'filtres': {
            'date_debut': date_debut,
            'date_fin': date_fin,
            'magasin': codes_magasins,
            'numero_br': numero_br,
            'statut_ic': statut_ic,
        },
        'total': paginator.count,
        'titre': "Liste BR",
    }

    return render(request, 'dashboard/liste_br_asten.html', context)


def liste_br_ecart(request):
    """Affiche les BR non trouvés (écarts)"""
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    codes_magasins = request.GET.getlist('magasin')
    numero_br = request.GET.get('numero_br', '').strip()

    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed = parse_date(date_fin) if date_fin else None

    filtres = {'ic_integre': False}
    if date_debut_parsed:
        filtres['date_br__gte'] = date_debut_parsed
    if date_fin_parsed:
        filtres['date_br__lte'] = date_fin_parsed
    if codes_magasins:
        filtres['code_magasin__code__in'] = codes_magasins
    if numero_br:
        filtres['numero_br__icontains'] = numero_br

    brs = BRAsten.objects.filter(**filtres).select_related('code_magasin').order_by('-date_br', 'numero_br')
    paginator = Paginator(brs, 50)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    magasins = Magasin.objects.all().order_by('code')

    context = {
        'brs': page_obj,
        'page_obj': page_obj,
        'magasins': magasins,
        'filtres': {
            'date_debut': date_debut,
            'date_fin': date_fin,
            'magasin': codes_magasins,
            'numero_br': numero_br,
            'statut_ic': 'non_integre',
        },
        'total': paginator.count,
        'titre': "BR en écart",
    }

    return render(request, 'dashboard/liste_br_asten.html', context)


def liste_br_ic(request):
    """Affiche la liste des BR IC"""
    return redirect('dashboard:liste_br_asten')


def detail_br_asten(request, br_id):
    """Affiche le détail d'un BR et permet de modifier son statut et ajouter un avis"""
    from django.contrib import messages
    
    try:
        br = BRAsten.objects.select_related('code_magasin').get(pk=br_id)
        
        # Gérer la modification du statut
        if request.method == 'POST':
            nouveau_statut_ic = request.POST.get('statut_ic', '').strip()
            ic_integre = request.POST.get('ic_integre') == 'on'
            avis = request.POST.get('avis', '').strip()
            
            # Mettre à jour le statut IC
            if nouveau_statut_ic:
                br.statut_ic = nouveau_statut_ic
            br.ic_integre = ic_integre
            if avis:
                br.avis = avis
            br.save()
            
            messages.success(request, f"Le statut du BR {br.numero_br} a été mis à jour avec succès. Les statistiques ont été recalculées.")
            
            # Récupérer les paramètres de filtres depuis la requête pour préserver les filtres
            date_debut = request.GET.get('date_debut', '')
            date_fin = request.GET.get('date_fin', '')
            magasin = request.GET.getlist('magasin')
            numero_br = request.GET.get('numero_br', '')
            from_dashboard = request.GET.get('from_dashboard', '')
            
            # Si on vient du dashboard, rediriger vers le dashboard pour mettre à jour les stats
            if from_dashboard == '1':
                redirect_url = reverse('dashboard:dashboard')
                params = ['type_donnees=br']
                if date_debut:
                    params.append(f'date_debut={date_debut}')
                if date_fin:
                    params.append(f'date_fin={date_fin}')
                for m in magasin:
                    params.append(f'magasin={m}')
                if params:
                    redirect_url += '?' + '&'.join(params)
                return redirect(redirect_url)
            
            # Sinon, rediriger vers la liste des BR en écart
            redirect_url = reverse('dashboard:liste_br_ecart')
            params = []
            if date_debut:
                params.append(f'date_debut={date_debut}')
            if date_fin:
                params.append(f'date_fin={date_fin}')
            for m in magasin:
                params.append(f'magasin={m}')
            if numero_br:
                params.append(f'numero_br={numero_br}')
            
            if params:
                redirect_url += '?' + '&'.join(params)
            
            return redirect(redirect_url)
        
        context = {
            'br': br,
        }
        return render(request, 'dashboard/detail_br_asten.html', context)
    except BRAsten.DoesNotExist:
        messages.error(request, "BR introuvable.")
        return redirect('dashboard:liste_br_ecart')


def detail_commande_asten(request, commande_id):
    """Affiche le détail d'une commande Asten"""
    try:
        commande = CommandeAsten.objects.select_related('code_magasin').get(pk=commande_id)
        
        # Vérifier si la commande existe dans Cyrus avec plusieurs critères
        commande_cyrus = CommandeCyrus.objects.filter(
            date_commande=commande.date_commande,
            numero_commande=commande.numero_commande,
            code_magasin=commande.code_magasin
        ).first()
        
        # Recherche alternative : même numéro et magasin mais date différente
        commande_cyrus_alt = None
        if not commande_cyrus:
            commande_cyrus_alt = CommandeCyrus.objects.filter(
                numero_commande=commande.numero_commande,
                code_magasin=commande.code_magasin
            ).exclude(date_commande=commande.date_commande).first()
        
        # Recherche par numéro seulement (sans magasin)
        commande_cyrus_numero = None
        if not commande_cyrus and not commande_cyrus_alt:
            commande_cyrus_numero = CommandeCyrus.objects.filter(
                numero_commande=commande.numero_commande
            ).first()
        
        # Vérifier si un écart existe
        try:
            ecart = commande.ecart
        except:
            ecart = None
        
        # Analyser pourquoi la commande est absente
        raisons_absence = []
        if not commande_cyrus:
            raisons_absence.append("La commande n'existe pas dans Cyrus avec les mêmes critères (date, numéro, magasin)")
            if commande_cyrus_alt:
                raisons_absence.append(f"⚠ Une commande avec le même numéro et magasin existe dans Cyrus mais avec une date différente: {commande_cyrus_alt.date_commande}")
            elif commande_cyrus_numero:
                raisons_absence.append(f"⚠ Une commande avec le même numéro existe dans Cyrus mais pour un autre magasin: {commande_cyrus_numero.code_magasin.code}")
            else:
                raisons_absence.append("Aucune commande avec ce numéro n'a été trouvée dans Cyrus")
        
        context = {
            'commande': commande,
            'commande_cyrus': commande_cyrus,
            'commande_cyrus_alt': commande_cyrus_alt,
            'commande_cyrus_numero': commande_cyrus_numero,
            'ecart': ecart,
            'raisons_absence': raisons_absence,
        }
        return render(request, 'dashboard/detail_commande_asten.html', context)
    except CommandeAsten.DoesNotExist:
        messages.error(request, "Commande introuvable.")
        return redirect('dashboard:liste_commandes_asten')


def detail_commande_cyrus(request, commande_id):
    """Affiche le détail d'une commande Cyrus"""
    try:
        commande = CommandeCyrus.objects.select_related('code_magasin').get(pk=commande_id)
        
        # Vérifier si la commande existe dans Asten
        commande_asten = CommandeAsten.objects.filter(
            date_commande=commande.date_commande,
            numero_commande=commande.numero_commande,
            code_magasin=commande.code_magasin
        ).first()
        
        context = {
            'commande': commande,
            'commande_asten': commande_asten,
        }
        return render(request, 'dashboard/detail_commande_cyrus.html', context)
    except CommandeCyrus.DoesNotExist:
        messages.error(request, "Commande introuvable.")
        return redirect('dashboard:liste_commandes_cyrus')


def detail_commande_legend(request, commande_id):
    """Affiche le détail d'une commande Legend"""
    from legend.models import CommandeLegend
    from gpv.models import CommandeGPV
    from cyrus.models import CommandeCyrus

    try:
        commande = CommandeLegend.objects.get(pk=commande_id)

        # Vérifier si la commande existe dans GPV et Cyrus (comparaison sans code magasin)
        commande_gpv = CommandeGPV.objects.filter(
            date_creation=commande.date_commande,
            numero_commande=commande.numero_commande
        ).first()

        # Fonction de normalisation pour comparer les numéros
        def normalize_numero(numero):
            if not numero:
                return ''
            numero_str = str(numero).strip()
            digits = ''.join(ch for ch in numero_str if ch.isdigit())
            if digits:
                return digits.lstrip('0') or '0'
            return numero_str

        numero_legend_normalise = normalize_numero(commande.numero_commande)
        
        # Chercher dans Cyrus avec normalisation
        commande_cyrus = None
        for cyrus_cmd in CommandeCyrus.objects.filter(date_commande=commande.date_commande):
            if normalize_numero(cyrus_cmd.numero_commande) == numero_legend_normalise:
                commande_cyrus = cyrus_cmd
                break
        
        if commande_cyrus is None:
            # Fallback: chercher par numéro seulement (sans date)
            for cyrus_cmd in CommandeCyrus.objects.all():
                if normalize_numero(cyrus_cmd.numero_commande) == numero_legend_normalise:
                    commande_cyrus = cyrus_cmd
                    break

        # Vérifier s'il y a un écart
        try:
            ecart = commande.ecart
        except Exception:
            ecart = None

        context = {
            'commande': commande,
            'commande_gpv': commande_gpv,
            'commande_cyrus': commande_cyrus,
            'ecart': ecart,
        }
        return render(request, 'dashboard/detail_commande_legend.html', context)
    except CommandeLegend.DoesNotExist:
        messages.error(request, "Commande introuvable.")
        return redirect('dashboard:dashboard')


def detail_ecart_gpv(request, ecart_id):
    """Affiche le détail d'un écart GPV et permet de modifier son statut"""
    from ecarts.models import EcartGPV
    from cyrus.models import CommandeCyrus
    
    try:
        ecart = EcartGPV.objects.select_related('commande_gpv__code_magasin').get(pk=ecart_id)
        
        # Vérifier si la commande existe maintenant dans Cyrus
        existe_cyrus = CommandeCyrus.objects.filter(
            Q(date_commande=ecart.commande_gpv.date_creation) |
            Q(numero_commande=ecart.commande_gpv.numero_commande, code_magasin=ecart.commande_gpv.code_magasin),
            numero_commande=ecart.commande_gpv.numero_commande,
            code_magasin=ecart.commande_gpv.code_magasin
        ).first()
        
        # Gérer la modification du statut
        if request.method == 'POST':
            nouveau_statut = request.POST.get('statut')
            commentaire = request.POST.get('commentaire', '').strip()
            
            if nouveau_statut in ['ouvert', 'resolu', 'ignore', 'quantite_0']:
                # Ne PAS supprimer l'écart, mais le garder avec le statut modifié
                # Cela permet de préserver les modifications manuelles lors du recalcul
                ecart.statut = nouveau_statut
                if commentaire:
                    ecart.commentaire = commentaire
                ecart.save()
                
                if nouveau_statut == 'resolu':
                    messages.success(request, "L'écart a été marqué comme résolu. La commande sera comptée comme intégrée. Les pourcentages seront mis à jour sur le dashboard.")
                elif nouveau_statut == 'ignore':
                    messages.info(request, "L'écart a été marqué comme ignoré. Les pourcentages seront mis à jour sur le dashboard.")
                elif nouveau_statut == 'quantite_0':
                    messages.info(request, "L'écart a été marqué comme 'Quantité 0'. La commande ne sera comptée ni comme intégrée ni comme non intégrée. Les pourcentages seront mis à jour sur le dashboard.")
                else:
                    messages.info(request, "L'écart a été remis à ouvert. Les pourcentages seront mis à jour sur le dashboard.")
                
                # Rediriger vers le dashboard pour que les pourcentages soient recalculés
                type_donnees = request.GET.get('type_donnees', 'commandes_gpv')
                return redirect(f"{reverse('dashboard:dashboard')}?type_donnees={type_donnees}")
        
        context = {
            'ecart': ecart,
            'existe_cyrus': existe_cyrus,
        }
        return render(request, 'dashboard/detail_ecart_gpv.html', context)
    except EcartGPV.DoesNotExist:
        messages.error(request, "Écart introuvable.")
        return redirect('dashboard:dashboard')


def detail_ecart_legend(request, ecart_id):
    """Affiche le détail d'un écart Legend et permet de modifier son statut"""
    from ecarts.models import EcartLegend
    from gpv.models import CommandeGPV
    from cyrus.models import CommandeCyrus

    try:
        ecart = EcartLegend.objects.select_related('commande_legend').get(pk=ecart_id)

        # Vérifier si la commande existe dans Cyrus
        existe_cyrus = CommandeCyrus.objects.filter(
            date_commande=ecart.commande_legend.date_commande,
            numero_commande=ecart.commande_legend.numero_commande
        ).first()
        if existe_cyrus is None:
            existe_cyrus = CommandeCyrus.objects.filter(
                numero_commande=ecart.commande_legend.numero_commande
            ).first()

        if request.method == 'POST':
            nouveau_statut = request.POST.get('statut')
            commentaire = request.POST.get('commentaire', '').strip()

            if nouveau_statut in ['ouvert', 'resolu', 'ignore', 'quantite_0']:
                # Ne PAS supprimer l'écart, mais le garder avec le statut modifié
                # Cela permet de préserver les modifications manuelles lors du recalcul
                ecart.statut = nouveau_statut
                if commentaire:
                    ecart.commentaire = commentaire
                ecart.save()
                
                if nouveau_statut == 'resolu':
                    messages.success(request, "L'écart a été marqué comme résolu. La commande sera comptée comme intégrée. Les pourcentages seront mis à jour sur le dashboard.")
                elif nouveau_statut == 'ignore':
                    messages.info(request, "L'écart a été marqué comme ignoré. Les pourcentages seront mis à jour sur le dashboard.")
                elif nouveau_statut == 'quantite_0':
                    messages.info(request, "L'écart a été marqué comme 'Quantité 0'. La commande ne sera comptée ni comme intégrée ni comme non intégrée. Les pourcentages seront mis à jour sur le dashboard.")
                else:
                    messages.info(request, "L'écart a été remis à ouvert. Les pourcentages seront mis à jour sur le dashboard.")

                type_donnees = request.GET.get('type_donnees', 'commandes_legend')
                return redirect(f"{reverse('dashboard:dashboard')}?type_donnees={type_donnees}")

        context = {
            'ecart': ecart,
            'existe_cyrus': existe_cyrus,
        }
        return render(request, 'dashboard/detail_ecart_legend.html', context)
    except EcartLegend.DoesNotExist:
        messages.error(request, "Écart introuvable.")
        return redirect('dashboard:dashboard')




def liste_commandes_gpv(request):
    """Affiche la liste des commandes GPV"""
    # Filtres
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    codes_magasins = request.GET.getlist('magasin')  # Récupérer plusieurs valeurs
    numero_commande = request.GET.get('numero_commande', '').strip()
    recherche_magasin = request.GET.get('recherche_magasin', '').strip()  # Recherche par code ou nom
    
    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed = parse_date(date_fin) if date_fin else None
    
    filtres = {}
    if date_debut_parsed:
        filtres['date_creation__gte'] = date_debut_parsed
    if date_fin_parsed:
        filtres['date_creation__lte'] = date_fin_parsed
    if codes_magasins:
        filtres['code_magasin__code__in'] = codes_magasins
    if numero_commande:
        filtres['numero_commande__icontains'] = numero_commande
    
    commandes = CommandeGPV.objects.filter(**filtres).select_related(
        'code_magasin'
    ).order_by('-date_creation', 'numero_commande')
    
    # Pagination pour améliorer les performances
    paginator = Paginator(commandes, 50)  # 50 commandes par page
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    
    # Charger tous les magasins pour le select (le filtrage se fait côté client)
    magasins = Magasin.objects.all().order_by('code')
    
    context = {
        'commandes': page_obj,
        'page_obj': page_obj,
        'magasins': magasins,
        'filtres': {
            'date_debut': date_debut,
            'date_fin': date_fin,
            'magasin': codes_magasins,
            'numero_commande': numero_commande,
            'recherche_magasin': recherche_magasin,
        },
        'total': paginator.count,
    }
    
    return render(request, 'dashboard/liste_commandes_gpv.html', context)


def liste_commandes_legend(request):
    """Affiche la liste des commandes Legend"""
    # Filtres
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    numero_commande = request.GET.get('numero_commande', '').strip()
    depot_recherche = request.GET.get('depot', '').strip()
    exportee = request.GET.get('exportee', '')

    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed = parse_date(date_fin) if date_fin else None

    filtres = {}
    if date_debut_parsed:
        filtres['date_commande__gte'] = date_debut_parsed
    if date_fin_parsed:
        filtres['date_commande__lte'] = date_fin_parsed
    if numero_commande:
        filtres['numero_commande__icontains'] = numero_commande
    if exportee == 'oui':
        filtres['exportee'] = True
    elif exportee == 'non':
        filtres['exportee'] = False
    if depot_recherche:
        filtres['depot_origine__icontains'] = depot_recherche

    commandes = CommandeLegend.objects.filter(**filtres).order_by('-date_commande', 'numero_commande')

    # Pagination pour améliorer les performances
    paginator = Paginator(commandes, 50)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    # Fonction de normalisation pour comparer les numéros de commande
    def normalize_numero(numero):
        """Normalise un numéro de commande pour la comparaison (enlève les zéros en tête)"""
        if not numero:
            return ''
        numero_str = str(numero).strip()
        digits = ''.join(ch for ch in numero_str if ch.isdigit())
        if digits:
            return digits.lstrip('0') or '0'
        return numero_str

    # Préparer les données de comparaison Cyrus avec normalisation
    legend_keys = [(cmd.date_commande, normalize_numero(cmd.numero_commande)) for cmd in page_obj.object_list]
    cyrus_lookup = set()
    cyrus_numero_lookup = set()

    # Récupérer toutes les commandes Cyrus de la période et les normaliser
    filtres_cyrus_lookup = {}
    if date_debut_parsed:
        filtres_cyrus_lookup['date_commande__gte'] = date_debut_parsed
    if date_fin_parsed:
        filtres_cyrus_lookup['date_commande__lte'] = date_fin_parsed
    
    for cyrus_cmd in CommandeCyrus.objects.filter(**filtres_cyrus_lookup):
        numero_normalise = normalize_numero(cyrus_cmd.numero_commande)
        cyrus_lookup.add((cyrus_cmd.date_commande, numero_normalise))
        cyrus_numero_lookup.add(numero_normalise)

    # Annoter les objets du page_obj pour l'affichage
    for cmd in page_obj.object_list:
        numero_normalise = normalize_numero(cmd.numero_commande)
        key = (cmd.date_commande, numero_normalise)
        cyrus_present = key in cyrus_lookup or numero_normalise in cyrus_numero_lookup
        cmd.cyrus_present = cyrus_present

    context = {
        'commandes': page_obj,
        'page_obj': page_obj,
        'filtres': {
            'date_debut': date_debut,
            'date_fin': date_fin,
            'numero_commande': numero_commande,
            'depot': depot_recherche,
            'exportee': exportee,
        },
        'total': paginator.count,
    }

    return render(request, 'dashboard/liste_commandes_legend.html', context)


def detail_commande_gpv(request, commande_id):
    """Affiche le détail d'une commande GPV"""
    from cyrus.models import CommandeCyrus
    
    try:
        commande = CommandeGPV.objects.select_related('code_magasin').get(pk=commande_id)
        
        # Vérifier si la commande existe dans Cyrus
        commande_cyrus = CommandeCyrus.objects.filter(
            Q(date_commande=commande.date_creation) |
            Q(numero_commande=commande.numero_commande, code_magasin=commande.code_magasin),
            numero_commande=commande.numero_commande,
            code_magasin=commande.code_magasin
        ).first()
        
        # Recherche alternative : même numéro et magasin mais date différente
        commande_cyrus_alt = None
        if not commande_cyrus:
            commande_cyrus_alt = CommandeCyrus.objects.filter(
                numero_commande=commande.numero_commande,
                code_magasin=commande.code_magasin
            ).exclude(date_commande=commande.date_creation).first()
        
        # Recherche par numéro seulement (sans magasin)
        commande_cyrus_numero = None
        if not commande_cyrus and not commande_cyrus_alt:
            commande_cyrus_numero = CommandeCyrus.objects.filter(
                numero_commande=commande.numero_commande
            ).first()
        
        # Vérifier s'il y a un écart
        ecart = None
        try:
            ecart = commande.ecart
        except:
            pass
        
        # Analyser pourquoi la commande est absente
        raisons_absence = []
        statut_gpv = (commande.statut or '').strip().upper()
        doit_etre_dans_cyrus = (statut_gpv == 'TRANSMISE' or statut_gpv == 'TRANSMIS')
        
        if not commande_cyrus:
            if doit_etre_dans_cyrus:
                raisons_absence.append("La commande n'existe pas dans Cyrus avec les mêmes critères (date, numéro, magasin)")
                if commande_cyrus_alt:
                    raisons_absence.append(f"⚠ Une commande avec le même numéro et magasin existe dans Cyrus mais avec une date différente: {commande_cyrus_alt.date_commande}")
                elif commande_cyrus_numero:
                    raisons_absence.append(f"⚠ Une commande avec le même numéro existe dans Cyrus mais pour un autre magasin: {commande_cyrus_numero.code_magasin.code}")
                else:
                    raisons_absence.append("Aucune commande avec ce numéro n'a été trouvée dans Cyrus")
            else:
                raisons_absence.append(f"Le statut de la commande GPV est '{commande.statut}', donc elle ne doit pas être dans Cyrus (seules les commandes 'Transmise' doivent être dans Cyrus)")
        
        context = {
            'commande': commande,
            'commande_cyrus': commande_cyrus,
            'commande_cyrus_alt': commande_cyrus_alt,
            'commande_cyrus_numero': commande_cyrus_numero,
            'ecart': ecart,
            'raisons_absence': raisons_absence,
            'doit_etre_dans_cyrus': doit_etre_dans_cyrus,
        }
        return render(request, 'dashboard/detail_commande_gpv.html', context)
    except CommandeGPV.DoesNotExist:
        messages.error(request, "Commande introuvable.")
        return redirect('dashboard:liste_commandes_gpv')


def historique_imports(request):
    """Affiche l'historique des imports de fichiers de commandes"""
    # Filtrer par type de fichier si demandé
    type_fichier = request.GET.get('type_fichier', '')
    statut = request.GET.get('statut', '')
    
    queryset = ImportFichier.objects.all()
    
    if type_fichier:
        queryset = queryset.filter(type_fichier=type_fichier)
    if statut:
        queryset = queryset.filter(statut=statut)
    
    # Pagination
    paginator = Paginator(queryset, 50)
    page_obj = paginator.get_page(request.GET.get('page'))
    
    # Statistiques globales
    total_imports = ImportFichier.objects.count()
    imports_termines = ImportFichier.objects.filter(statut='termine').count()
    imports_erreur = ImportFichier.objects.filter(statut='erreur').count()
    imports_en_cours = ImportFichier.objects.filter(statut='en_cours').count()
    
    # Derniers imports par type
    derniers_imports = {}
    for type_f in ['asten', 'cyrus', 'gpv', 'legend', 'br_asten']:
        dernier = ImportFichier.objects.filter(type_fichier=type_f).first()
        if dernier:
            derniers_imports[type_f] = dernier
    
    context = {
        'page_obj': page_obj,
        'type_fichier': type_fichier,
        'statut': statut,
        'stats': {
            'total': total_imports,
            'termines': imports_termines,
            'erreur': imports_erreur,
            'en_cours': imports_en_cours,
        },
        'derniers_imports': derniers_imports,
        'type_choices': ImportFichier.TYPE_CHOICES,
        'statut_choices': [
            ('en_cours', 'En cours'),
            ('termine', 'Terminé'),
            ('erreur', 'Erreur'),
        ],
    }
    return render(request, 'dashboard/historique_imports.html', context)


def configuration_systeme(request):
    """
    Page de configuration générale de l'application (place‑holder).
    Permettra plus tard de gérer les paramètres globaux (chemins, options, etc.).
    """
    return render(request, 'dashboard/configuration_systeme.html', {})


def gestion_magasins(request):
    """
    Page de gestion des magasins.
    Permet d'ajouter / modifier / supprimer un magasin.
    Les magasins sont ensuite visibles dans tous les filtres (commandes, BR, remontées, etc.).
    """
    # Code du magasin en édition (pour pré‑remplir le formulaire)
    edit_code = request.GET.get('edit')
    magasin_edit = None
    if edit_code:
        magasin_edit = Magasin.objects.filter(code=edit_code).first()

    if request.method == 'POST':
        action = request.POST.get('action') or 'create'
        code = (request.POST.get('code') or '').strip()
        nom = (request.POST.get('nom') or '').strip()

        if action == 'delete':
            # Suppression d'un magasin
            if not code:
                messages.error(request, "Code magasin manquant pour la suppression.")
            else:
                try:
                    Magasin.objects.get(code=code).delete()
                    messages.success(request, f"Magasin {code} supprimé avec succès.")
                    return redirect('dashboard:gestion_magasins')
                except Magasin.DoesNotExist:
                    messages.error(request, "Magasin introuvable.")
                except ProtectedError:
                    messages.error(
                        request,
                        "Impossible de supprimer ce magasin car il est déjà utilisé dans des commandes, BR ou tickets."
                    )
        elif action == 'update':
            # Mise à jour du nom du magasin (on ne touche pas au code car il est utilisé comme clé)
            original_code = (request.POST.get('original_code') or '').strip()
            if not original_code or not nom:
                messages.error(request, "Le nom du magasin est obligatoire pour la modification.")
            else:
                try:
                    magasin = Magasin.objects.get(code=original_code)
                    magasin.nom = nom
                    magasin.save()
                    messages.success(request, f"Magasin {original_code} mis à jour avec succès.")
                    return redirect('dashboard:gestion_magasins')
                except Magasin.DoesNotExist:
                    messages.error(request, "Magasin introuvable pour la modification.")
        else:
            # Création d'un nouveau magasin
            if not code or not nom:
                messages.error(request, "Le code et le nom du magasin sont obligatoires.")
            elif len(code) > 10:
                messages.error(request, "Le code du magasin ne doit pas dépasser 10 caractères.")
            else:
                try:
                    Magasin.objects.create(code=code, nom=nom)
                    messages.success(request, f"Magasin {code} - {nom} ajouté avec succès.")
                    return redirect('dashboard:gestion_magasins')
                except IntegrityError:
                    messages.error(request, f"Un magasin avec le code {code} existe déjà.")

    magasins = Magasin.objects.all().order_by('code')
    return render(
        request,
        'dashboard/gestion_magasins.html',
        {
            'magasins': magasins,
            'magasin_edit': magasin_edit,
        },
    )


def gestion_utilisateurs(request):
    """
    Page de gestion des utilisateurs (lecture seule).
    Utilise le modèle User standard de Django.
    """
    from django.contrib.auth.models import User
    users = User.objects.all().order_by('username')
    return render(request, 'dashboard/gestion_utilisateurs.html', {'users': users})


def preferences_utilisateur(request):
    """
    Page de préférences utilisateur (place‑holder).
    Pourra accueillir des réglages personnels par utilisateur.
    """
    return render(request, 'dashboard/preferences_utilisateur.html', {})
