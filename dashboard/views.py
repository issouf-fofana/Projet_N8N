from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils.dateparse import parse_date
from django.urls import reverse
from django.core.paginator import Paginator
from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Q, Prefetch, Exists, OuterRef, Count, Sum
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
from imports.services import (
    scanner_et_importer_fichiers,
    scanner_factures_sage,
    get_factures_sage_prefixes,
    scanner_factures_backup,
)
from imports.models import ImportFichier, FactureSage, FactureBackupCyrus
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


def _get_semaine_comparison(periode='semaine'):
    """Retourne les stats comparées période courante vs période précédente pour tous les types."""
    from datetime import date, timedelta
    from django.db.models import Q

    today = date.today()

    nb = {'semaine': 7, 'mois': 30, '3mois': 90, '6mois': 180, 'annee': 365}.get(periode, 7)

    fin_s  = today
    debut_s  = today - timedelta(days=nb - 1)
    fin_sp   = debut_s - timedelta(days=1)
    debut_sp = fin_sp - timedelta(days=nb - 1)

    q0_br = Q(statut_ic__icontains='Quantité 0') | Q(statut_ic__icontains='quantite_0') | Q(statut_ic__icontains='Quantite 0')

    def _asten(d1, d2):
        try:
            total = CommandeAsten.objects.filter(date_commande__gte=d1, date_commande__lte=d2).count()
            q0 = EcartCommande.objects.filter(commande_asten__date_commande__gte=d1, commande_asten__date_commande__lte=d2, statut='quantite_0').count()
            ni = EcartCommande.objects.filter(commande_asten__date_commande__gte=d1, commande_asten__date_commande__lte=d2, statut='ouvert').count()
            t = max(0, total - q0)
            i = max(0, t - ni)
            return {'total': t, 'integres': i, 'non_integres': ni}
        except Exception:
            return {'total': 0, 'integres': 0, 'non_integres': 0}

    def _br(d1, d2):
        try:
            total = BRAsten.objects.filter(date_br__gte=d1, date_br__lte=d2).count()
            q0 = BRAsten.objects.filter(date_br__gte=d1, date_br__lte=d2).filter(q0_br).count()
            t = max(0, total - q0)
            i = BRAsten.objects.filter(date_br__gte=d1, date_br__lte=d2, ic_integre=True).exclude(q0_br).count()
            ni = BRAsten.objects.filter(date_br__gte=d1, date_br__lte=d2, ic_integre=False).exclude(q0_br).count()
            return {'total': t, 'integres': i, 'non_integres': ni}
        except Exception:
            return {'total': 0, 'integres': 0, 'non_integres': 0}

    def _tickets(d1, d2):
        try:
            total = Ticket.objects.filter(date_creation__date__gte=d1, date_creation__date__lte=d2).count()
            resolus = Ticket.objects.filter(date_creation__date__gte=d1, date_creation__date__lte=d2, statut__in=['resolu', 'ferme']).count()
            return {'total': total, 'integres': resolus, 'non_integres': total - resolus}
        except Exception:
            return {'total': 0, 'integres': 0, 'non_integres': 0}

    def _gpv(d1, d2):
        try:
            total = CommandeGPV.objects.filter(date_creation__gte=d1, date_creation__lte=d2, statut__iexact='Transmise').count()
            ni = EcartGPV.objects.filter(commande_gpv__date_creation__gte=d1, commande_gpv__date_creation__lte=d2, statut='ouvert').count()
            q0 = EcartGPV.objects.filter(commande_gpv__date_creation__gte=d1, commande_gpv__date_creation__lte=d2, statut='quantite_0').count()
            t = max(0, total - q0)
            i = max(0, t - ni)
            return {'total': t, 'integres': i, 'non_integres': ni}
        except Exception:
            return {'total': 0, 'integres': 0, 'non_integres': 0}

    def _legend(d1, d2):
        try:
            total = CommandeLegend.objects.filter(date_commande__gte=d1, date_commande__lte=d2, exportee=True).count()
            ni = EcartLegend.objects.filter(commande_legend__date_commande__gte=d1, commande_legend__date_commande__lte=d2, statut='ouvert').count()
            q0 = EcartLegend.objects.filter(commande_legend__date_commande__gte=d1, commande_legend__date_commande__lte=d2, statut='quantite_0').count()
            t = max(0, total - q0)
            i = max(0, t - ni)
            return {'total': t, 'integres': i, 'non_integres': ni}
        except Exception:
            return {'total': 0, 'integres': 0, 'non_integres': 0}

    def _row(curr, prev):
        di = curr['integres'] - prev['integres']
        dn = curr['non_integres'] - prev['non_integres']
        dt = curr['total'] - prev['total']
        return {'current': curr, 'previous': prev, 'delta_total': dt, 'delta_integres': di, 'delta_non': dn}

    return {
        'debut_s': debut_s, 'fin_s': fin_s,
        'debut_sp': debut_sp, 'fin_sp': fin_sp,
        'periode': periode,
        'asten':    _row(_asten(debut_s, fin_s),    _asten(debut_sp, fin_sp)),
        'gpv':      _row(_gpv(debut_s, fin_s),      _gpv(debut_sp, fin_sp)),
        'legend':   _row(_legend(debut_s, fin_s),   _legend(debut_sp, fin_sp)),
        'br':       _row(_br(debut_s, fin_s),        _br(debut_sp, fin_sp)),
        'remontees':_row(_tickets(debut_s, fin_s),   _tickets(debut_sp, fin_sp)),
    }


def _get_top5_magasins(debut=None, fin=None, n=5):
    """Retourne le top N magasins par source sur la période donnée (défaut : 30 derniers jours)."""
    from datetime import date, timedelta
    from django.db.models import Count, Q
    today = date.today()
    if debut is None:
        debut = today - timedelta(days=29)
    if fin is None:
        fin = today
    n = max(1, min(int(n), 100))

    try:
        asten = list(
            CommandeAsten.objects.filter(date_commande__gte=debut, date_commande__lte=fin)
            .values('code_magasin__code', 'code_magasin__nom')
            .annotate(total=Count('id')).order_by('-total')[:n]
        )
    except Exception:
        asten = []

    try:
        gpv = list(
            CommandeGPV.objects.filter(date_creation__gte=debut, date_creation__lte=fin, statut__iexact='Transmise')
            .values('code_magasin__code', 'code_magasin__nom')
            .annotate(total=Count('id')).order_by('-total')[:n]
        )
    except Exception:
        gpv = []

    try:
        legend = list(
            CommandeLegend.objects.filter(date_commande__gte=debut, date_commande__lte=fin)
            .values('depot_destination')
            .annotate(total=Count('id')).order_by('-total')[:n]
        )
    except Exception:
        legend = []

    try:
        br = list(
            BRAsten.objects.filter(date_br__gte=debut, date_br__lte=fin)
            .values('code_magasin__code', 'code_magasin__nom')
            .annotate(
                total=Count('id'),
                integrees=Count('id', filter=Q(ic_integre=True)),
            ).order_by('-total')[:n]
        )
    except Exception:
        br = []

    return {'asten': asten, 'gpv': gpv, 'legend': legend, 'br': br, 'debut': debut, 'fin': fin, 'n': n}


def dashboard(request):
    """Vue principale du dashboard"""
    # Les données existantes en base sont TOUJOURS chargées et affichées
    # Vérifier s'il y a de nouveaux fichiers à importer (même si déjà actualisé dans la session)
    # Les données restent en base de données donc elles persistent même si on change de type
    # Le scanner d'import est déclenché uniquement lors de l'upload de fichiers,
    # pas à chaque visite du dashboard (trop lent à cause du fichier BR IC Excel).
    # Recalculer les écarts seulement si explicitement demandé (?recalculer=1).
    if request.GET.get('recalculer') == '1':
        try:
            scanner_et_importer_fichiers()
            recalculer_ecarts()
        except Exception:
            pass
    
    # Récupérer les filtres (gérer les valeurs "None" en string et la sélection multiple)
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    code_magasin = request.GET.getlist('magasin')  # Récupérer plusieurs valeurs pour la sélection multiple
    type_donnees = request.GET.get('type_donnees', 'commandes_asten')  # Par défaut: commandes Asten
    periode = request.GET.get('periode', '')
    show = request.GET.get('show', '')  # 'non_integres' pour afficher uniquement les écarts ouverts
    
    # Nettoyer les valeurs "None" en string
    if date_debut == 'None' or date_debut == '':
        date_debut = None
    if date_fin == 'None' or date_fin == '':
        date_fin = None
    # Nettoyer la liste des magasins
    if code_magasin:
        code_magasin = [m for m in code_magasin if m and m != 'None' and m != '']
        if not code_magasin:
            code_magasin = None
        elif len(code_magasin) == 1:
            # Si un seul magasin est sélectionné, garder comme liste pour cohérence
            pass
    
    # Convertir les dates
    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed = parse_date(date_fin) if date_fin else None
    
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
    sage_files = None
    sage_days = None
    sage_error = None
    backup_files = None
    backup_error = None


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
        sage_error = None
        try:
            if FactureSage.objects.count() == 0:
                scanner_factures_sage()
        except Exception as e:
            sage_error = f"Erreur scan Facture Sage: {e}"

        factures_qs = FactureSage.objects.all()
        prefixes = get_factures_sage_prefixes()
        if prefixes != ['']:
            prefix_filter = Q()
            for prefix in prefixes:
                prefix_filter |= Q(nom_fichier__startswith=prefix)
            factures_qs = factures_qs.filter(prefix_filter)
        if date_debut_parsed:
            factures_qs = factures_qs.filter(date_depot__gte=date_debut_parsed)
        if date_fin_parsed:
            factures_qs = factures_qs.filter(date_depot__lte=date_fin_parsed)

        sage_files = factures_qs.order_by('-date_depot', '-date_modif', 'nom_fichier')
        total_files = factures_qs.count()
        total_lines = factures_qs.aggregate(total=Sum('nombre_lignes'))['total'] or 0

        depots = {
            item['date_depot']: item['count']
            for item in factures_qs.values('date_depot').annotate(count=Count('id'))
        }

        if date_debut_parsed and date_fin_parsed:
            start_date = date_debut_parsed
            end_date = date_fin_parsed
        elif date_debut_parsed and not date_fin_parsed:
            start_date = date_debut_parsed
            end_date = date_debut_parsed
        elif date_fin_parsed and not date_debut_parsed:
            start_date = date_fin_parsed
            end_date = date_fin_parsed
        else:
            if depots:
                start_date = min(depots.keys())
                end_date = max(depots.keys())
            else:
                start_date = None
                end_date = None

        sage_days = []
        expected_days = 0
        days_with_depot = 0
        days_without_depot = 0
        if start_date and end_date:
            current = start_date
            while current <= end_date:
                attendu = current.weekday() in {1, 2, 3, 4, 5}  # Mardi à samedi
                count = depots.get(current, 0)
                has_files = count > 0
                if attendu:
                    expected_days += 1
                    if has_files:
                        days_with_depot += 1
                    else:
                        days_without_depot += 1
                sage_days.append({
                    'date': current,
                    'count': count,
                    'attendu': attendu,
                    'has_files': has_files,
                })
                current += timedelta(days=1)

        taux_integration = round((days_with_depot / expected_days * 100) if expected_days > 0 else 0, 2)
        taux_non_integration = round((days_without_depot / expected_days * 100) if expected_days > 0 else 0, 2)

        stats = {
            'total_source': total_files,
            'total_target': total_lines,
            'integres': days_with_depot,
            'non_integres': days_without_depot,
            'taux_integration': taux_integration,
            'taux_non_integration': taux_non_integration,
        }
        titre_tableau = "Fichiers Facture Sage"
        
    elif type_donnees == 'factures_backup':
        backup_error = None
        try:
            if FactureBackupCyrus.objects.count() == 0:
                scanner_factures_backup()
        except Exception as e:
            backup_error = f"Erreur scan Facture Backup: {e}"

        factures_qs = FactureBackupCyrus.objects.all()
        if code_magasin:
            factures_qs = factures_qs.filter(code_magasin__in=code_magasin)
        if date_debut_parsed:
            factures_qs = factures_qs.filter(date_modif__date__gte=date_debut_parsed)
        if date_fin_parsed:
            factures_qs = factures_qs.filter(date_modif__date__lte=date_fin_parsed)

        backup_files = factures_qs.order_by('-date_modif', 'nom_fichier')[:1000]

        # Vraies stats depuis la jointure Cyrus/Asten
        try:
            from imports.services import get_factures_verification
            fv = get_factures_verification()
            fv_stats = fv['stats']
            # Filtrer par magasin si demandé
            if code_magasin:
                joined_filtre = [r for r in fv['joined'] if r['cidc'] in code_magasin]
                nb_int  = sum(1 for r in joined_filtre if r['integree'])
                nb_eca  = sum(1 for r in joined_filtre if not r['integree'])
                nb_tot  = len(joined_filtre)
            else:
                nb_int  = fv_stats['integrees']
                nb_eca  = fv_stats['ecarts']
                nb_tot  = fv_stats['total']
            taux = round(nb_int / nb_tot * 100, 1) if nb_tot else 0
            # Stats par magasin pour le graphique
            stats_par_magasin = {}
            for cidc, s in fv_stats['par_magasin'].items():
                if not code_magasin or cidc in code_magasin:
                    stats_par_magasin[cidc] = s
        except Exception:
            nb_int = nb_eca = nb_tot = 0
            taux = 0
            stats_par_magasin = {}

        stats = {
            'total_source':        nb_tot,
            'total_target':        nb_tot,
            'integres':            nb_int,
            'non_integres':        nb_eca,
            'taux_integration':    taux,
            'taux_non_integration': round(100 - taux, 1) if nb_tot else 0,
            'par_magasin':         stats_par_magasin,
        }
        titre_tableau = "Factures Backup (Cyrus)"
        
    elif type_donnees == 'br':
        # BR ASTEN (statut IC fourni dans le fichier)
        # IMPORTANT: Les statistiques en haut affichent TOUJOURS le total global (sans filtre de date)
        # Par défaut, on affiche tous les BR non intégrés (sans filtre de date)
        
        # Calculer les statistiques selon les filtres sélectionnés (période/magasin)
        filtres_br_global = {}
        if code_magasin:
            filtres_br_global['code_magasin__code__in'] = code_magasin
        if date_debut_parsed:
            filtres_br_global['date_br__gte'] = date_debut_parsed
        if date_fin_parsed:
            filtres_br_global['date_br__lte'] = date_fin_parsed
        
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
        taux_integration_global = round((br_trouvees_count_global / total_asten_pour_stats_global * 100) if total_asten_pour_stats_global > 0 else 0, 2)
        taux_non_integration_global = round((br_non_trouvees_count_global / total_asten_pour_stats_global * 100) if total_asten_pour_stats_global > 0 else 0, 2)

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

        # Pour les tableaux : non intégrés toujours en premier (sans filtre de période),
        # intégrés selon la période sélectionnée.
        filtres_br_base = {}
        if code_magasin:
            filtres_br_base['code_magasin__code__in'] = code_magasin

        filtres_br_date = dict(filtres_br_base)
        if date_debut_parsed:
            filtres_br_date['date_br__gte'] = date_debut_parsed
        if date_fin_parsed:
            filtres_br_date['date_br__lte'] = date_fin_parsed

        statut_ic = request.GET.get('statut_ic')
        exclude_quantite_0 = (
            Q(statut_ic__icontains='Quantité 0') |
            Q(statut_ic__icontains='quantite_0') |
            Q(statut_ic__icontains='Quantite 0')
        )

        if statut_ic == 'integre':
            br_non_trouvees = BRAsten.objects.none()
            br_trouvees = BRAsten.objects.filter(ic_integre=True, **filtres_br_date).exclude(
                exclude_quantite_0
            ).select_related('code_magasin').order_by('-date_br', 'numero_br')[:200]
        elif statut_ic == 'non_integre':
            br_trouvees = BRAsten.objects.none()
            br_non_trouvees = BRAsten.objects.filter(ic_integre=False, **filtres_br_date).exclude(
                exclude_quantite_0
            ).select_related('code_magasin').order_by('-date_br', 'numero_br')[:200]
        else:
            br_non_trouvees = BRAsten.objects.filter(ic_integre=False, **filtres_br_base).exclude(
                exclude_quantite_0
            ).select_related('code_magasin').order_by('-date_br', 'numero_br')[:200]
            br_trouvees = BRAsten.objects.filter(ic_integre=True, **filtres_br_date).exclude(
                exclude_quantite_0
            ).select_related('code_magasin').order_by('-date_br', 'numero_br')[:200]
        commandes_data = []
        titre_tableau = "BR ASTEN (Statut IC)"
    
    # ── Évolution journalière (dashboard) ────────────────────────────────
    try:
        evo_fin   = date_fin_parsed   or timezone.now().date()
        evo_debut = date_debut_parsed or (evo_fin - timedelta(days=13))
        if (evo_fin - evo_debut).days > 29:
            evo_debut = evo_fin - timedelta(days=29)
        dates_evo  = [evo_debut + timedelta(days=i) for i in range((evo_fin - evo_debut).days + 1)]
        labels_evo = [d.strftime('%d/%m') for d in dates_evo]

        asten_d  = {i['date_commande']: i['count'] for i in CommandeAsten.objects.filter(date_commande__gte=evo_debut, date_commande__lte=evo_fin).values('date_commande').annotate(count=Count('id'))}
        gpv_d    = {i['date_creation']: i['count'] for i in CommandeGPV.objects.filter(date_creation__gte=evo_debut, date_creation__lte=evo_fin).values('date_creation').annotate(count=Count('id'))}
        legend_d = {i['date_commande']: i['count'] for i in CommandeLegend.objects.filter(date_commande__gte=evo_debut, date_commande__lte=evo_fin).values('date_commande').annotate(count=Count('id'))}
        br_d     = {i['date_br']: i['count'] for i in BRAsten.objects.filter(date_br__gte=evo_debut, date_br__lte=evo_fin).values('date_br').annotate(count=Count('id'))}

        evolution_journaliere = {
            'labels': labels_evo,
            'asten':  [asten_d.get(d, 0)  for d in dates_evo],
            'gpv':    [gpv_d.get(d, 0)    for d in dates_evo],
            'legend': [legend_d.get(d, 0) for d in dates_evo],
            'br':     [br_d.get(d, 0)     for d in dates_evo],
        }
    except Exception:
        evolution_journaliere = {'labels': [], 'asten': [], 'gpv': [], 'legend': [], 'br': []}

    context = {
        'stats': stats,
        'evolution_journaliere': evolution_journaliere,
        'commandes': commandes_data,
        'br_trouvees': br_trouvees if type_donnees == 'br' else None,
        'br_non_trouvees': br_non_trouvees if type_donnees == 'br' else None,
        'sage_files': sage_files if type_donnees == 'factures' else None,
        'sage_days': sage_days if type_donnees == 'factures' else None,
        'sage_error': sage_error if type_donnees == 'factures' else None,
        'backup_files': backup_files if type_donnees == 'factures_backup' else None,
        'backup_error': backup_error if type_donnees == 'factures_backup' else None,
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
        'semaine_comparison': _get_semaine_comparison(request.GET.get('periode_cmp', 'semaine')),
        'periode_cmp': request.GET.get('periode_cmp', 'semaine'),
        'top5': _get_top5_magasins(debut=date_debut_parsed, fin=date_fin_parsed, n=request.GET.get('top_n', 5)),
        'top_url_base': f"?type_donnees={request.GET.get('type_donnees','commandes_asten')}&periode={periode}&date_debut={date_debut or ''}&date_fin={date_fin or ''}",
    }

    # Widget / données Version Asten
    from datetime import date as _date_cls, timedelta as _td
    _today_v = _date_cls.today()

    if type_donnees == 'version':
        context['today'] = _today_v
        context['yesterday'] = _today_v - _td(days=1)
        context['week_ago'] = _today_v - _td(days=6)
        context['month_ago'] = _today_v - _td(days=29)

        # ── Backup (SMB) – indépendant du RPOS ───────────────────────────────
        try:
            from imports.services import get_versions_asten
            if date_debut_parsed or date_fin_parsed:
                va_data = get_versions_asten(date_debut=date_debut_parsed, date_fin=date_fin_parsed)
            else:
                va_data = get_versions_asten(filtre_jours=30)
            context['version_asten_data'] = va_data
            context['version_asten_widget'] = va_data['versions'][0] if va_data.get('versions') else None
            context['version_asten_disponible'] = va_data.get('disponible', False)
        except Exception:
            context['version_asten_widget'] = None
            context['version_asten_disponible'] = False
            context['version_asten_data'] = None

        # ── Intégration RPOS (base de données) – toujours chargé ─────────────
        # Cherche la nuit la plus récente avec données :
        # - dans la plage [date_debut, date_fin] si un filtre est actif
        # - sinon depuis aujourd'hui en remontant (max 4 jours)
        try:
            from entree_journal.services import get_integration_asten
            ia_data  = None
            _start   = date_fin_parsed or _today_v
            _stop    = date_debut_parsed  # None = pas de limite basse
            _max_days = (_start - _stop).days + 1 if _stop else 4
            _max_days = min(_max_days, 60)   # sécurité : ne pas boucler à l'infini
            for _delta in range(_max_days):
                _candidate = _start - _td(days=_delta)
                if _stop and _candidate < _stop:
                    break
                _result = get_integration_asten(run_date=_candidate)
                if _result.get('nb_entrees_total', 0) >= 200:
                    ia_data = _result
                    break
            if ia_data is None:
                ia_data = get_integration_asten(run_date=_start)
            context['integration_asten_data'] = ia_data
            # Calcul rapide stats par type pour le dashboard
            from entree_journal.services import TYPES_OBLIGATOIRES
            _ia_stats_type = {}
            for _tk in TYPES_OBLIGATOIRES:
                _ia_stats_type[_tk] = {'label': TYPES_OBLIGATOIRES[_tk], 'nb_ok': 0, 'nb_ok_erreurs': 0, 'nb_absent': 0, 'nb_total_errors': 0, 'shops_erreurs': []}
            for _pos in ia_data.get('pos', {}).values():
                for _shop_ref, _shop in _pos.get('shops', {}).items():
                    for _tk in TYPES_OBLIGATOIRES:
                        _t = _shop.get('types', {}).get(_tk, {})
                        _nb_err = _t.get('nb_erreurs', 0)
                        if _t.get('integre') and _nb_err == 0:
                            _ia_stats_type[_tk]['nb_ok'] += 1
                        elif _t.get('integre') and _nb_err > 0:
                            _ia_stats_type[_tk]['nb_ok_erreurs'] += 1
                            _ia_stats_type[_tk]['shops_erreurs'].append({'shop_ref': _shop_ref, 'nb_erreurs': _nb_err})
                        else:
                            _ia_stats_type[_tk]['nb_absent'] += 1
                        _ia_stats_type[_tk]['nb_total_errors'] += _nb_err
            context['ia_stats_type'] = _ia_stats_type
            context['ia_nb_fichiers_ok']      = sum(v['nb_ok']          for v in _ia_stats_type.values())
            context['ia_nb_fichiers_erreurs'] = sum(v['nb_ok_erreurs']  for v in _ia_stats_type.values())
            context['ia_nb_fichiers_absents'] = sum(v['nb_absent']      for v in _ia_stats_type.values())
        except Exception as _e:
            context['integration_asten_data'] = None
            context['integration_asten_error'] = str(_e)

    else:
        try:
            from imports.services import get_versions_asten
            va_data = get_versions_asten(filtre_jours=7)
            context['version_asten_data'] = None
            if va_data['disponible'] and va_data['versions']:
                context['version_asten_widget'] = va_data['versions'][0]
                context['version_asten_disponible'] = True
            else:
                context['version_asten_widget'] = None
                context['version_asten_disponible'] = va_data.get('disponible', False)
        except Exception:
            context['version_asten_widget'] = None
            context['version_asten_disponible'] = False
            context['version_asten_data'] = None

    return render(request, 'dashboard/dashboard.html', context)


def accueil(request):
    """Vue d'accueil affichant toutes les statistiques en un coup d'œil"""
    from django.db.models import Q, Exists, OuterRef
    from datetime import datetime, timedelta
    from django.utils import timezone
    
    # Gérer les filtres de période
    periode = request.GET.get('periode', '')
    date_debut = None
    date_fin = None
    
    if periode == 'aujourdhui':
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
    
    # FACTURES (Facture Sage)
    try:
        if FactureSage.objects.count() == 0:
            scanner_factures_sage()
    except Exception:
        pass

    try:
        factures_qs = FactureSage.objects.all()
        prefixes = get_factures_sage_prefixes()
        if prefixes != ['']:
            prefix_filter = Q()
            for prefix in prefixes:
                prefix_filter |= Q(nom_fichier__startswith=prefix)
            factures_qs = factures_qs.filter(prefix_filter)
        if date_debut:
            factures_qs = factures_qs.filter(date_depot__gte=date_debut)
        if date_fin:
            factures_qs = factures_qs.filter(date_depot__lte=date_fin)

        depots = {
            item['date_depot']: item['count']
            for item in factures_qs.values('date_depot').annotate(count=Count('id'))
        }

        if date_debut and date_fin:
            start_date = date_debut
            end_date = date_fin
        elif date_debut and not date_fin:
            start_date = date_debut
            end_date = date_debut
        elif date_fin and not date_debut:
            start_date = date_fin
            end_date = date_fin
        else:
            if depots:
                start_date = min(depots.keys())
                end_date = max(depots.keys())
            else:
                start_date = None
                end_date = None

        expected_days = 0
        days_with_depot = 0
        days_without_depot = 0
        if start_date and end_date:
            current = start_date
            while current <= end_date:
                attendu = current.weekday() in {1, 2, 3, 4, 5}  # Mardi à samedi
                count = depots.get(current, 0)
                if attendu:
                    expected_days += 1
                    if count > 0:
                        days_with_depot += 1
                    else:
                        days_without_depot += 1
                current += timedelta(days=1)

        taux_integration = round((days_with_depot / expected_days * 100) if expected_days > 0 else 0, 2)
        taux_non_integration = round((days_without_depot / expected_days * 100) if expected_days > 0 else 0, 2)

        stats_factures = {
            'total': factures_qs.count(),
            'integres': days_with_depot,
            'non_integres': days_without_depot,
            'taux_integration': taux_integration,
            'taux_non_integration': taux_non_integration,
        }
    except Exception:
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
    
    # ── Évolution journalière (tous champs DateField) ──────────────────────
    try:
        evo_fin   = date_fin   or timezone.now().date()
        evo_debut = date_debut or (evo_fin - timedelta(days=13))
        if (evo_fin - evo_debut).days > 29:
            evo_debut = evo_fin - timedelta(days=29)
        dates_evo  = [evo_debut + timedelta(days=i) for i in range((evo_fin - evo_debut).days + 1)]
        labels_evo = [d.strftime('%d/%m') for d in dates_evo]

        asten_daily = {
            item['date_commande']: item['count']
            for item in CommandeAsten.objects
            .filter(date_commande__gte=evo_debut, date_commande__lte=evo_fin)
            .values('date_commande').annotate(count=Count('id'))
        }
        gpv_daily = {
            item['date_creation']: item['count']
            for item in CommandeGPV.objects
            .filter(date_creation__gte=evo_debut, date_creation__lte=evo_fin)
            .values('date_creation').annotate(count=Count('id'))
        }
        legend_daily = {
            item['date_commande']: item['count']
            for item in CommandeLegend.objects
            .filter(date_commande__gte=evo_debut, date_commande__lte=evo_fin)
            .values('date_commande').annotate(count=Count('id'))
        }
        br_daily = {
            item['date_br']: item['count']
            for item in BRAsten.objects
            .filter(date_br__gte=evo_debut, date_br__lte=evo_fin)
            .values('date_br').annotate(count=Count('id'))
        }
        evolution_journaliere = {
            'labels': labels_evo,
            'asten':  [asten_daily.get(d, 0)  for d in dates_evo],
            'gpv':    [gpv_daily.get(d, 0)    for d in dates_evo],
            'legend': [legend_daily.get(d, 0) for d in dates_evo],
            'br':     [br_daily.get(d, 0)     for d in dates_evo],
        }
    except Exception:
        evolution_journaliere = {'labels': [], 'asten': [], 'gpv': [], 'legend': [], 'br': []}

    # Stats intégration RPOS (nuit dernière)
    ia_rpos = None
    try:
        from entree_journal.services import get_integration_asten, TYPES_OBLIGATOIRES
        from datetime import date as _date, timedelta as _td
        _today = _date.today()
        for _d in range(5):
            _candidate = _today - _td(days=_d)
            _ia = get_integration_asten(run_date=_candidate)
            if _ia.get('nb_entrees_total', 0) >= 200:
                _s = _ia.get('summary', {})
                _types = {}
                for _tk, _tlbl in TYPES_OBLIGATOIRES.items():
                    _types[_tk] = {'label': _tlbl, 'nb_ok': 0, 'nb_ok_erreurs': 0, 'nb_absent': 0}
                for _pos in _ia.get('pos', {}).values():
                    for _shop in _pos.get('shops', {}).values():
                        for _tk in TYPES_OBLIGATOIRES:
                            _t = _shop.get('types', {}).get(_tk, {})
                            if _t.get('integre') and _t.get('nb_erreurs', 0) == 0:
                                _types[_tk]['nb_ok'] += 1
                            elif _t.get('integre'):
                                _types[_tk]['nb_ok_erreurs'] += 1
                            else:
                                _types[_tk]['nb_absent'] += 1
                ia_rpos = {
                    'run_date':    str(_candidate),
                    'date_nuit':   _ia.get('date_nuit', ''),
                    'fenetre':     _ia.get('fenetre', ''),
                    'total_shops': _s.get('total_shops', 0),
                    'nb_ok':       _s.get('ok', 0),
                    'nb_erreurs':  sum(v['nb_ok_erreurs'] for v in _types.values()),
                    'nb_absent':   sum(v['nb_absent'] for v in _types.values()),
                    'types':       _types,
                    'art_err':     sum(
                        shop.get('nb_erreurs_articles', 0)
                        for _pos in _ia.get('pos', {}).values()
                        for shop in _pos.get('shops', {}).values()
                    ),
                }
                break
    except Exception:
        ia_rpos = None

    context = {
        'stats_asten': stats_asten,
        'stats_gpv': stats_gpv,
        'stats_legend': stats_legend,
        'stats_br': stats_br,
        'stats_factures': stats_factures,
        'stats_remontees': stats_remontees,
        'ia_rpos': ia_rpos,
        'evolution_journaliere': evolution_journaliere,
        'periode': periode,
        'date_debut': date_debut.strftime('%Y-%m-%d') if date_debut else '',
        'date_fin': date_fin.strftime('%Y-%m-%d') if date_fin else '',
        'semaine_comparison': _get_semaine_comparison(request.GET.get('periode_cmp', 'semaine')),
        'periode_cmp': request.GET.get('periode_cmp', 'semaine'),
        'top5': _get_top5_magasins(debut=date_debut, fin=date_fin, n=request.GET.get('top_n', 5)),
        'top_url_base': f"?periode={request.GET.get('periode','')}&date_debut={date_debut or ''}&date_fin={date_fin or ''}&periode_cmp={request.GET.get('periode_cmp','semaine')}",
    }

    return render(request, 'dashboard/accueil.html', context)


@require_http_methods(["POST"])
def actualiser_donnees(request):
    """Actualise TOUTES les données globalement : importe les fichiers et recalcule les écarts pour tous les types"""
    from core.permissions import user_has_perm
    if not user_has_perm(request.user, 'actualiser_importer'):
        messages.error(request, "Action non autorisée.")
        return redirect('dashboard:dashboard')
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
    if request.method == 'POST':
        selected = request.POST.getlist('selected_ecarts')
        new_status = request.POST.get('bulk_status', '').strip()
        allowed_statuses = {'ouvert', 'resolu', 'ignore', 'quantite_0'}

        if not selected:
            messages.warning(request, "Aucun écart sélectionné.")
        elif new_status not in allowed_statuses:
            messages.error(request, "Statut invalide.")
        else:
            updated = 0
            for item in selected:
                try:
                    type_ecart, ecart_id = item.split(':', 1)
                except ValueError:
                    continue
                if type_ecart == 'asten':
                    updated += EcartCommande.objects.filter(id=ecart_id).update(statut=new_status)
                elif type_ecart == 'gpv':
                    updated += EcartGPV.objects.filter(id=ecart_id).update(statut=new_status)
                elif type_ecart == 'legend':
                    updated += EcartLegend.objects.filter(id=ecart_id).update(statut=new_status)

            if updated > 0:
                messages.success(request, f"{updated} écart(s) mis à jour.")
            else:
                messages.warning(request, "Aucun écart n'a été mis à jour.")

        # Préserver les filtres lors de la redirection
        params = []
        for key in ['date_debut', 'date_fin', 'magasin', 'statut', 'type_ecart', 'page']:
            value = request.POST.get(key, '').strip()
            if value:
                params.append(f"{key}={value}")
        query = ('?' + '&'.join(params)) if params else ''
        return redirect(f"{reverse('dashboard:liste_ecarts')}{query}")

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
        ecarts_combined.append({
            'type': 'gpv',
            'ecart': ecart,
            'id': ecart.id,
            'date_commande': ecart.commande_gpv.date_creation,
            'numero_commande': ecart.commande_gpv.numero_commande,
            'code_magasin': ecart.commande_gpv.code_magasin,
            'montant': None,
            'date_creation': ecart.date_creation,
            'statut': ecart.statut,
        })

    for ecart in ecarts_legend:
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
    if request.method == 'POST':
        selected = request.POST.getlist('selected_brs')
        if not selected:
            messages.warning(request, "Aucun BR sélectionné.")
        else:
            updated = BRAsten.objects.filter(id__in=selected).update(
                ic_integre=True,
                statut_ic='Intégré',
                override_statut_ic=True
            )
            if updated > 0:
                messages.success(request, f"{updated} BR marqué(s) comme intégré(s).")
            else:
                messages.warning(request, "Aucun BR n'a été mis à jour.")

        params = []
        for key in ['date_debut', 'date_fin', 'numero_br', 'page']:
            value = request.POST.get(key, '').strip()
            if value:
                params.append(f"{key}={value}")
        for magasin in request.POST.getlist('magasin'):
            if magasin:
                params.append(f"magasin={magasin}")
        query = ('?' + '&'.join(params)) if params else ''
        return redirect(f"{reverse('dashboard:liste_br_ecart')}{query}")

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
            br.override_statut_ic = True
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
    depot_dest_recherche = request.GET.get('depot_dest', '').strip()
    if depot_recherche:
        filtres['depot_origine__icontains'] = depot_recherche
    if depot_dest_recherche:
        filtres['depot_destination__icontains'] = depot_dest_recherche

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
    from core.permissions import user_has_perm
    if not user_has_perm(request.user, 'configurer_systeme'):
        messages.error(request, "Accès non autorisé.")
        return redirect('dashboard:dashboard')
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
    from core.permissions import user_has_perm
    if not user_has_perm(request.user, 'configurer_systeme'):
        messages.error(request, "Accès non autorisé.")
        return redirect('dashboard:dashboard')
    return render(request, 'dashboard/configuration_systeme.html', {})


def gestion_magasins(request):
    """
    Page de gestion des magasins.
    Permet d'ajouter / modifier / supprimer un magasin.
    Les magasins sont ensuite visibles dans tous les filtres (commandes, BR, remontées, etc.).
    """
    from core.permissions import user_has_perm
    if not user_has_perm(request.user, 'gerer_magasins'):
        messages.error(request, "Accès non autorisé.")
        return redirect('dashboard:dashboard')
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
    """Liste des utilisateurs."""
    from django.contrib.auth.models import User
    from core.permissions import get_user_role, user_has_perm

    if not user_has_perm(request.user, 'gerer_utilisateurs'):
        messages.error(request, "Accès réservé aux administrateurs.")
        return redirect('dashboard:dashboard')

    role = get_user_role(request.user)

    # Suppression
    if request.method == 'POST' and request.POST.get('action') == 'delete' and role == 'superadmin':
        user_id = request.POST.get('user_id')
        try:
            u = User.objects.get(pk=user_id)
            if u == request.user:
                messages.error(request, "Impossible de supprimer votre propre compte.")
            else:
                nom = u.username
                u.delete()
                messages.success(request, f"Utilisateur '{nom}' supprimé.")
        except User.DoesNotExist:
            messages.error(request, "Utilisateur introuvable.")
        return redirect('dashboard:gestion_utilisateurs')

    users = User.objects.select_related('profile').prefetch_related('profile__permissions').order_by('username')
    return render(request, 'dashboard/gestion_utilisateurs.html', {
        'users': users,
        'current_role': role,
    })


def creer_utilisateur(request):
    """Page de création d'un nouvel utilisateur."""
    from django.contrib.auth.models import User
    from core.permissions import get_user_role, user_has_perm

    if not user_has_perm(request.user, 'gerer_utilisateurs'):
        messages.error(request, "Accès réservé aux administrateurs.")
        return redirect('dashboard:dashboard')

    role = get_user_role(request.user)

    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '').strip()
        new_role = request.POST.get('role', 'user')
        if role != 'superadmin' and new_role != 'user':
            messages.error(request, "Vous ne pouvez créer que des comptes 'Utilisateur'.")
        elif not username or not password:
            messages.error(request, "Nom d'utilisateur et mot de passe obligatoires.")
        elif User.objects.filter(username=username).exists():
            messages.error(request, f"L'utilisateur '{username}' existe déjà.")
        else:
            user = User.objects.create_user(username=username, password=password)
            profile = user.profile
            profile.role = new_role
            profile.save()
            messages.success(request, f"Utilisateur '{username}' créé.")
            return redirect('dashboard:permissions_utilisateur', user_id=user.pk)

    return render(request, 'dashboard/creer_utilisateur.html', {
        'current_role': role,
    })


def permissions_utilisateur(request, user_id):
    """Page de gestion des permissions d'un utilisateur."""
    from django.contrib.auth.models import User
    from core.models import UserProfile, AppPermission
    from core.permissions import get_user_role, user_has_perm

    if not user_has_perm(request.user, 'gerer_utilisateurs'):
        messages.error(request, "Accès réservé aux administrateurs.")
        return redirect('dashboard:dashboard')

    role = get_user_role(request.user)

    try:
        u = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        messages.error(request, "Utilisateur introuvable.")
        return redirect('dashboard:gestion_utilisateurs')

    profile, _ = UserProfile.objects.get_or_create(user=u)

    if request.method == 'POST':
        if role != 'superadmin':
            messages.error(request, "Action réservée au Super-Admin.")
            return redirect('dashboard:permissions_utilisateur', user_id=user_id)

        # Réinitialisation mot de passe
        if request.POST.get('action') == 'reset_password':
            nouveau_mdp = request.POST.get('nouveau_mdp', '').strip()
            if len(nouveau_mdp) < 6:
                messages.error(request, "Le mot de passe doit contenir au moins 6 caractères.")
            else:
                u.set_password(nouveau_mdp)
                u.save()
                messages.success(request, f"Mot de passe de '{u.username}' réinitialisé.")
            return redirect('dashboard:permissions_utilisateur', user_id=user_id)

        new_role = request.POST.get('role', 'user')
        profile.role = new_role
        profile.save()
        if new_role != 'superadmin':
            codes = request.POST.getlist('permissions')
            perms = AppPermission.objects.filter(code__in=codes)
            profile.permissions.set(perms)
        messages.success(request, f"Permissions de '{u.username}' mises à jour.")
        return redirect('dashboard:gestion_utilisateurs')

    all_permissions = AppPermission.objects.all()
    user_perm_codes = set(profile.permissions.values_list('code', flat=True))
    return render(request, 'dashboard/permissions_utilisateur.html', {
        'u': u,
        'profile': profile,
        'current_role': role,
        'all_permissions': all_permissions,
        'user_perm_codes': user_perm_codes,
    })


def preferences_utilisateur(request):
    """Préférences utilisateur — changement de mot de passe."""
    if request.method == 'POST':
        ancien = request.POST.get('ancien_mdp', '')
        nouveau = request.POST.get('nouveau_mdp', '')
        confirm = request.POST.get('confirm_mdp', '')
        if not request.user.check_password(ancien):
            messages.error(request, "Mot de passe actuel incorrect.")
        elif len(nouveau) < 6:
            messages.error(request, "Le nouveau mot de passe doit contenir au moins 6 caractères.")
        elif nouveau != confirm:
            messages.error(request, "Les deux mots de passe ne correspondent pas.")
        else:
            request.user.set_password(nouveau)
            request.user.save()
            from django.contrib.auth import update_session_auth_hash
            update_session_auth_hash(request, request.user)
            messages.success(request, "Mot de passe modifié avec succès.")
            return redirect('dashboard:preferences_utilisateur')
    return render(request, 'dashboard/preferences_utilisateur.html', {})


def rapport_global(request):
    """Page de rapports avec stats, récaps et exports CSV par période."""
    import json
    import csv
    from datetime import date, timedelta
    from django.http import HttpResponse
    from django.db.models import Count, Q
    from tickets.models import Ticket

    today = date.today()
    periode = request.GET.get('periode', 'mois')
    section = request.GET.get('section', 'commandes')
    date_debut_str = request.GET.get('date_debut', '')
    date_fin_str = request.GET.get('date_fin', '')

    if periode == 'aujourd_hui':
        date_debut = date_fin = today
    elif periode == 'semaine':
        date_debut = today - timedelta(days=today.weekday())
        date_fin = today
    elif periode == '3mois':
        date_debut = today - timedelta(days=90)
        date_fin = today
    elif periode == 'annee':
        date_debut = date(today.year, 1, 1)
        date_fin = today
    elif periode == 'custom':
        date_debut = parse_date(date_debut_str) or today - timedelta(days=30)
        date_fin = parse_date(date_fin_str) or today
    else:  # mois (default)
        date_debut = today - timedelta(days=30)
        date_fin = today

    # ---- COMMANDES ----
    asten_qs = CommandeAsten.objects.filter(date_commande__gte=date_debut, date_commande__lte=date_fin)
    cyrus_qs = CommandeCyrus.objects.filter(date_commande__gte=date_debut, date_commande__lte=date_fin)
    gpv_qs = CommandeGPV.objects.filter(date_creation__gte=date_debut, date_creation__lte=date_fin)
    legend_qs = CommandeLegend.objects.filter(date_commande__gte=date_debut, date_commande__lte=date_fin)

    cmd_totaux = {
        'asten': asten_qs.count(),
        'cyrus': cyrus_qs.count(),
        'gpv': gpv_qs.count(),
        'legend': legend_qs.count(),
    }
    cmd_total = sum(cmd_totaux.values())

    cmd_par_magasin = list(
        asten_qs.values('code_magasin__code', 'code_magasin__nom')
        .annotate(total=Count('id')).order_by('-total')[:10]
    )
    gpv_par_magasin = list(
        gpv_qs.values('code_magasin__code', 'code_magasin__nom')
        .annotate(total=Count('id')).order_by('-total')[:10]
    )
    legend_par_depot = list(
        legend_qs.values('depot_destination')
        .annotate(total=Count('id')).order_by('-total')[:10]
    )

    cmd_par_jour = list(
        asten_qs.values('date_commande').annotate(total=Count('id')).order_by('date_commande')
    )

    # ---- BR ----
    br_qs = BRAsten.objects.filter(date_br__gte=date_debut, date_br__lte=date_fin)
    br_total = br_qs.count()
    br_integrees = br_qs.filter(ic_integre=True).count()
    br_non_integrees = br_total - br_integrees

    br_par_magasin = list(
        br_qs.values('code_magasin__code', 'code_magasin__nom')
        .annotate(
            total=Count('id'),
            integrees=Count('id', filter=Q(ic_integre=True)),
            non_integrees=Count('id', filter=Q(ic_integre=False)),
        ).order_by('-total')[:10]
    )

    br_par_jour = list(
        br_qs.values('date_br').annotate(total=Count('id')).order_by('date_br')
    )

    # ---- REMONTÉES ----
    tickets_qs = Ticket.objects.filter(
        date_creation__date__gte=date_debut, date_creation__date__lte=date_fin
    )
    tickets_total = tickets_qs.count()
    tickets_ouverts = tickets_qs.filter(statut__in=['nouveau', 'en_cours', 'en_attente']).count()
    tickets_resolus = tickets_qs.filter(statut__in=['resolu', 'ferme']).count()

    tickets_par_statut = list(tickets_qs.values('statut').annotate(total=Count('id')).order_by('statut'))
    tickets_par_type = list(tickets_qs.values('type_demande').annotate(total=Count('id')))
    tickets_par_urgence = list(tickets_qs.values('urgence').annotate(total=Count('id')).order_by('urgence'))
    tickets_par_magasin = list(
        tickets_qs.values('magasin__code', 'magasin__nom')
        .annotate(total=Count('id')).order_by('-total')[:10]
    )
    tickets_par_jour = list(
        tickets_qs.values('date_creation__date').annotate(total=Count('id')).order_by('date_creation__date')
    )

    # ---- EXPORT CSV ----
    export = request.GET.get('export')
    top_n_export = int(request.GET.get('top_n', 10))
    if export == 'csv':
        response = HttpResponse(content_type='text/csv; charset=utf-8-sig')
        filename = f"rapport_{section}_{date_debut}_{date_fin}.csv"
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        writer = csv.writer(response, delimiter=';')

        if section == 'commandes':
            writer.writerow(['Source', 'Total'])
            for src, total in cmd_totaux.items():
                writer.writerow([src.capitalize(), total])
            writer.writerow([])
            # Asten
            writer.writerow([f'Top {top_n_export} Asten', '', ''])
            writer.writerow(['Magasin', 'Code', 'Total'])
            asten_export = list(
                asten_qs.values('code_magasin__code', 'code_magasin__nom')
                .annotate(total=Count('id')).order_by('-total')[:top_n_export]
            )
            for row in asten_export:
                writer.writerow([row['code_magasin__nom'] or '', row['code_magasin__code'], row['total']])
            writer.writerow([])
            # GPV
            writer.writerow([f'Top {top_n_export} GPV', '', ''])
            writer.writerow(['Magasin', 'Code', 'Total'])
            gpv_export = list(
                gpv_qs.values('code_magasin__code', 'code_magasin__nom')
                .annotate(total=Count('id')).order_by('-total')[:top_n_export]
            )
            for row in gpv_export:
                writer.writerow([row['code_magasin__nom'] or '', row['code_magasin__code'], row['total']])
            writer.writerow([])
            # Legend
            writer.writerow([f'Top {top_n_export} Legend', ''])
            writer.writerow(['Magasin (dépôt)', 'Total'])
            legend_export = list(
                legend_qs.values('depot_destination')
                .annotate(total=Count('id')).order_by('-total')[:top_n_export]
            )
            for row in legend_export:
                writer.writerow([row['depot_destination'] or '', row['total']])
        elif section == 'br':
            writer.writerow(['Total BR', 'Intégrées', 'Non intégrées'])
            writer.writerow([br_total, br_integrees, br_non_integrees])
            writer.writerow([])
            writer.writerow(['Magasin (Code)', 'Nom', 'Total', 'Intégrées', 'Non intégrées'])
            for row in br_par_magasin:
                writer.writerow([row['code_magasin__code'], row['code_magasin__nom'] or '', row['total'], row['integrees'], row['non_integrees']])
        elif section == 'remontees':
            writer.writerow(['Total', 'Ouverts', 'Résolus/Fermés'])
            writer.writerow([tickets_total, tickets_ouverts, tickets_resolus])
            writer.writerow([])
            writer.writerow(['Statut', 'Total'])
            for row in tickets_par_statut:
                writer.writerow([row['statut'], row['total']])
            writer.writerow([])
            writer.writerow(['Magasin (Code)', 'Nom', 'Total'])
            for row in tickets_par_magasin:
                writer.writerow([row['magasin__code'], row['magasin__nom'] or '', row['total']])
        return response

    # ---- CHART JSON ----
    chart_cmd = {
        'labels': [str(r['date_commande']) for r in cmd_par_jour],
        'values': [r['total'] for r in cmd_par_jour],
    }
    chart_br = {
        'labels': [str(r['date_br']) for r in br_par_jour],
        'values': [r['total'] for r in br_par_jour],
    }
    chart_tickets = {
        'labels': [str(r['date_creation__date']) for r in tickets_par_jour],
        'values': [r['total'] for r in tickets_par_jour],
    }

    _sl = {'nouveau': 'Nouveau', 'en_cours': 'En cours', 'en_attente': 'En attente', 'resolu': 'Résolu', 'ferme': 'Fermé'}
    _ul = {'tres_basse': 'Très basse', 'basse': 'Basse', 'moyenne': 'Moyenne', 'haute': 'Haute'}

    tickets_par_statut = [
        {'statut': r['statut'], 'label': _sl.get(r['statut'], r['statut']), 'total': r['total']}
        for r in tickets_par_statut
    ]
    tickets_par_urgence = [
        {'urgence': r['urgence'], 'label': _ul.get(r['urgence'], r['urgence']), 'total': r['total']}
        for r in tickets_par_urgence
    ]

    context = {
        'section': section,
        'periode': periode,
        'date_debut': date_debut,
        'date_fin': date_fin,
        # Commandes
        'cmd_total': cmd_total,
        'cmd_totaux': cmd_totaux,
        'cmd_par_magasin': cmd_par_magasin,
        'gpv_par_magasin': gpv_par_magasin,
        'legend_par_depot': legend_par_depot,
        'chart_cmd_json': json.dumps(chart_cmd),
        # BR
        'br_total': br_total,
        'br_integrees': br_integrees,
        'br_non_integrees': br_non_integrees,
        'br_par_magasin': br_par_magasin,
        'chart_br_json': json.dumps(chart_br),
        # Remontées
        'tickets_total': tickets_total,
        'tickets_ouverts': tickets_ouverts,
        'tickets_resolus': tickets_resolus,
        'tickets_par_statut': tickets_par_statut,
        'tickets_par_type': tickets_par_type,
        'tickets_par_urgence': tickets_par_urgence,
        'tickets_par_magasin': tickets_par_magasin,
        'chart_tickets_json': json.dumps(chart_tickets),
        'semaine_comparison': _get_semaine_comparison(request.GET.get('periode_cmp', 'semaine')),
        'periode_cmp': request.GET.get('periode_cmp', 'semaine'),
        'top5': _get_top5_magasins(debut=date_debut, fin=date_fin, n=request.GET.get('top_n', 5)),
        'top_url_base': f"?section={section}&periode={periode}&date_debut={date_debut}&date_fin={date_fin}",
    }
    return render(request, 'dashboard/rapport_global.html', context)


def remontee_asten(request):
    return render(request, 'dashboard/en_cours.html', {
        'page_title': 'Remontée Asten',
        'page_icon': 'bi-arrow-up-circle',
        'page_color': '#ef4444',
        'page_color_light': '#fff1f2',
    })


def version_asten(request):
    from imports.services import get_versions_asten
    filtre = request.GET.get('periode', '30')
    try:
        filtre_jours = None if filtre == 'tout' else int(filtre)
    except ValueError:
        filtre, filtre_jours = '30', 30
    data = get_versions_asten(filtre_jours=filtre_jours)
    filtre_choices = [('7','7 jours'),('30','30 jours'),('90','3 mois'),('365','1 an'),('tout','Tout')]
    return render(request, 'dashboard/version_asten.html', {
        'disponible':     data['disponible'],
        'versions':       data['versions'],
        'stats':          data['stats'],
        'filtre':         filtre,
        'filtre_choices': filtre_choices,
    })


def version_asten_detail(request, version_name):
    from imports.services import get_version_asten_detail
    from django.http import Http404
    detail = get_version_asten_detail(version_name)
    if detail is None:
        raise Http404("Version introuvable")
    return render(request, 'dashboard/version_asten_detail.html', {'detail': detail})


def version_asten_fichier(request, version_name, assortiment, file_type):
    from imports.services import get_fichier_content
    from django.http import Http404
    search   = request.GET.get('q', '').strip()
    page     = int(request.GET.get('page', 1))
    per_page = int(request.GET.get('per_page', 50))
    if per_page not in (20, 50, 100, 200):
        per_page = 50
    data = get_fichier_content(version_name, assortiment, file_type,
                               search=search, page=page, per_page=per_page)
    if data is None:
        raise Http404("Fichier introuvable")
    return render(request, 'dashboard/version_asten_fichier.html', {
        'data':         data,
        'version_name': version_name,
        'assortiment':  assortiment,
        'file_type':    file_type,
        'per_page':     per_page,
        'pp_choices':   [20, 50, 100, 200],
    })



def version_asten_sync(request):
    """Déclenche la synchronisation SMB → DB (POST uniquement)."""
    from imports.services import sync_versions_to_db
    from django.http import JsonResponse
    from core.permissions import user_has_perm
    if not user_has_perm(request.user, 'actualiser_importer'):
        return JsonResponse({'error': 'Accès non autorisé.'}, status=403)
    if request.method != 'POST':
        return JsonResponse({'error': 'POST requis'}, status=405)
    force = request.POST.get('force', '0') == '1'
    result = sync_versions_to_db(force=force)
    return JsonResponse(result)


def integration_asten(request):
    from entree_journal.services import get_integration_asten
    from datetime import date, timedelta
    date_str = request.GET.get('date', '')
    today    = date.today()
    if date_str:
        try:
            run_date = date.fromisoformat(date_str)
        except ValueError:
            run_date = today
        data = get_integration_asten(run_date)
    else:
        # Pas de date précisée → chercher la nuit la plus récente avec données
        data = None
        run_date = today
        for _delta in range(3):
            _candidate = today - timedelta(days=_delta)
            _result    = get_integration_asten(run_date=_candidate)
            if _result.get('nb_entrees_total', 0) > 0:
                data     = _result
                run_date = _candidate
                break
        if data is None:
            data = get_integration_asten(run_date=today)
    yesterday = today - timedelta(days=1)
    total_art_err = sum(
        shop.get('nb_erreurs_articles', 0)
        for pos in data.get('pos', {}).values()
        for shop in pos.get('shops', {}).values()
    )
    # Agréger les 4 types obligatoires pour le bandeau résumé
    from entree_journal.services import TYPES_OBLIGATOIRES
    ia_types = {}
    for tk, tlabel in TYPES_OBLIGATOIRES.items():
        ia_types[tk] = {'label': tlabel, 'nb_ok': 0, 'nb_ok_erreurs': 0, 'nb_absent': 0}
    for _pos in data.get('pos', {}).values():
        for _shop in _pos.get('shops', {}).values():
            for tk in TYPES_OBLIGATOIRES:
                _t = _shop.get('types', {}).get(tk, {})
                if _t.get('integre') and _t.get('nb_erreurs', 0) == 0:
                    ia_types[tk]['nb_ok'] += 1
                elif _t.get('integre'):
                    ia_types[tk]['nb_ok_erreurs'] += 1
                else:
                    ia_types[tk]['nb_absent'] += 1
    return render(request, 'dashboard/integration_asten.html', {
        'data':          data,
        'run_date':      run_date,
        'date_str':      str(run_date),
        'total_art_err': total_art_err,
        'ia_types':      ia_types,
        'today':         str(today),
        'yesterday':     str(yesterday),
    })


def integration_asten_detail_type(request, pos_id, shop_ref, type_key):
    from entree_journal.services import get_integration_asten
    from datetime import date
    from django.http import Http404
    date_str = request.GET.get('date', '')
    try:
        run_date = date.fromisoformat(date_str) if date_str else date.today()
    except ValueError:
        run_date = date.today()
    data = get_integration_asten(run_date)
    pos = data['pos'].get(pos_id)
    if not pos:
        raise Http404("POS introuvable")
    shop = pos['shops'].get(shop_ref)
    if not shop:
        raise Http404("Magasin introuvable")
    type_data = shop['types'].get(type_key)
    if not type_data:
        raise Http404("Type introuvable")
    return render(request, 'dashboard/integration_asten_detail_type.html', {
        'data': data,
        'pos_id': pos_id,
        'pos': pos,
        'shop_ref': shop_ref,
        'shop': shop,
        'type_key': type_key,
        'type_data': type_data,
        'run_date': run_date,
        'date_str': str(run_date),
    })


def integration_asten_import(request):
    from core.permissions import user_has_perm
    if not user_has_perm(request.user, 'actualiser_importer'):
        messages.error(request, "Action non autorisée.")
        return redirect('dashboard:dashboard')
    from entree_journal.services import importer_fichiers
    from django.contrib import messages
    from django.shortcuts import redirect

    result = importer_fichiers()

    if result['nouveaux'] == 0 and not result['erreurs']:
        messages.info(request, "Aucun nouveau fichier à importer.")
    elif result['nouveaux'] > 0:
        messages.success(
            request,
            f"{result['nouveaux']} fichier(s) importé(s) — {result['lignes_inserees']} entrées ajoutées."
        )

    for err in result['erreurs']:
        messages.error(request, f"Erreur : {err['fichier']} — {err['erreur']}")

    return redirect('dashboard:integration_asten')


def _load_assortiments():
    """Lit assortiment.txt et retourne la liste + le nombre d'assortiments."""
    from pathlib import Path
    path = Path(settings.BASE_DIR) / 'assortiment.txt'
    try:
        codes = [l.strip() for l in path.read_text(encoding='utf-8').splitlines() if l.strip()]
        return codes
    except Exception:
        return []


def integration_asten_stats(request):
    """Page de synthèse : fichiers intégrés par type et par magasin."""
    from entree_journal.services import get_integration_asten, TYPES_OBLIGATOIRES
    from datetime import date, timedelta

    date_str = request.GET.get('date', '')
    today    = date.today()
    if date_str:
        try:
            run_date = date.fromisoformat(date_str)
        except ValueError:
            run_date = today
        data = get_integration_asten(run_date)
    else:
        data = None
        run_date = today
        for _delta in range(3):
            _candidate = today - timedelta(days=_delta)
            _result    = get_integration_asten(run_date=_candidate)
            if _result.get('nb_entrees_total', 0) > 0:
                data     = _result
                run_date = _candidate
                break
        if data is None:
            data = get_integration_asten(run_date=today)

    # ── Calcul stats par type de fichier ──────────────────────────────
    types_order = list(TYPES_OBLIGATOIRES.keys())
    stats_par_type = {}
    for tk in types_order:
        stats_par_type[tk] = {
            'label':             TYPES_OBLIGATOIRES[tk],
            'nb_ok':             0,
            'nb_ok_erreurs':     0,
            'nb_absent':         0,
            'nb_articles_ok':    0,   # articles créés + MAJ (rapport)
            'nb_articles_erreurs': 0, # articles en erreur (rapport)
            'shops_erreurs':     [],
        }

    def _sum_report(entrees):
        """Agrège nb_crees + nb_maj et nb_erreurs depuis les report_summary."""
        art_ok = art_err = 0
        for e in entrees:
            s = e.get('report_summary') or {}
            art_ok  += (s.get('nb_crees') or 0) + (s.get('nb_maj') or 0)
            art_err += (s.get('nb_erreurs') or 0)
        return art_ok, art_err

    # ── Calcul stats par magasin (liste plate) ────────────────────────
    shops_list = []

    for pos_id, pos in data.get('pos', {}).items():
        for shop_ref, shop in pos.get('shops', {}).items():
            types_status = {}
            for tk in types_order:
                t = shop.get('types', {}).get(tk, {})
                nb_run_err = t.get('nb_erreurs', 0)
                integre    = t.get('integre', False)
                art_ok, art_err = _sum_report(t.get('entrees', []))
                types_status[tk] = {
                    'label':        TYPES_OBLIGATOIRES[tk],
                    'statut':       t.get('statut', 'absent'),
                    'nb_succes':    t.get('nb_succes', 0),
                    'nb_erreurs':   nb_run_err,
                    'integre':      integre,
                    'art_ok':       art_ok,
                    'art_err':      art_err,
                }
                # Agréger dans stats_par_type
                if integre and nb_run_err == 0:
                    stats_par_type[tk]['nb_ok'] += 1
                elif integre and nb_run_err > 0:
                    stats_par_type[tk]['nb_ok_erreurs'] += 1
                    stats_par_type[tk]['shops_erreurs'].append({
                        'shop_ref':  shop_ref,
                        'shop_name': shop.get('name', shop_ref),
                        'nb_erreurs': nb_run_err,
                    })
                else:
                    stats_par_type[tk]['nb_absent'] += 1
                stats_par_type[tk]['nb_articles_ok']      += art_ok
                stats_par_type[tk]['nb_articles_erreurs'] += art_err

            shops_list.append({
                'ref':    shop_ref,
                'name':   shop.get('name', shop_ref),
                'pos_id': pos_id,
                'statut': shop.get('statut', 'absent'),
                'nb_ok':  shop.get('nb_ok', 0),
                'nb_total': shop.get('nb_total', 4),
                'nb_erreurs_articles': shop.get('nb_erreurs_articles', 0),
                'types':  types_status,
            })

    # Trier les magasins : absents/partiels d'abord, puis avec erreurs, puis ok
    statut_order = {'absent': 0, 'partial': 1, 'ok_erreurs': 2, 'ok': 3}
    shops_list.sort(key=lambda s: (statut_order.get(s['statut'], 9), s['ref']))

    # Totaux globaux
    total_ok_strict   = sum(1 for s in shops_list if s['statut'] == 'ok')
    total_ok_erreurs  = sum(1 for s in shops_list if s['statut'] == 'ok_erreurs')
    total_partial     = sum(1 for s in shops_list if s['statut'] == 'partial')
    total_absent      = sum(1 for s in shops_list if s['statut'] == 'absent')
    total_art_erreurs = sum(s['nb_erreurs_articles'] for s in shops_list)

    # Convertir en liste ordonnée pour le template (évite {% with %} imbriqués)
    ICONS = {
        'price_updater':     'bi-tag-fill',
        'linked_code':       'bi-link-45deg',
        'product':           'bi-box-seam',
        'product_suppliers': 'bi-truck',
    }
    stats_par_type_list = []
    for tk in types_order:
        td = stats_par_type[tk]
        stats_par_type_list.append({
            'key':                 tk,
            'label':               td['label'],
            'icon':                ICONS.get(tk, 'bi-file-earmark'),
            'nb_ok':               td['nb_ok'],
            'nb_ok_erreurs':       td['nb_ok_erreurs'],
            'nb_absent':           td['nb_absent'],
            'nb_articles_ok':      td['nb_articles_ok'],
            'nb_articles_erreurs': td['nb_articles_erreurs'],
            'shops_erreurs':       td['shops_erreurs'],
            'nb_integres':         td['nb_ok'] + td['nb_ok_erreurs'],
        })

    assortiments = _load_assortiments()

    return render(request, 'dashboard/integration_asten_stats.html', {
        'data':                 data,
        'run_date':             run_date,
        'date_str':             str(run_date),
        'today':                str(today),
        'stats_par_type_list':  stats_par_type_list,
        'shops_list':           shops_list,
        'assortiments':         assortiments,
        'nb_assortiments':      len(assortiments),
        'totaux': {
            'ok':           total_ok_strict,
            'ok_erreurs':   total_ok_erreurs,
            'partial':      total_partial,
            'absent':       total_absent,
            'total':        len(shops_list),
            'art_erreurs':  total_art_erreurs,
        },
    })


def integration_asten_type_errors(request, type_key):
    """Détail des erreurs pour un type de fichier donné."""
    from entree_journal.services import get_integration_asten, TYPES_OBLIGATOIRES
    from datetime import date, timedelta
    from django.http import Http404

    if type_key not in TYPES_OBLIGATOIRES:
        raise Http404("Type inconnu")

    date_str = request.GET.get('date', '')
    today    = date.today()
    if date_str:
        try:
            run_date = date.fromisoformat(date_str)
        except ValueError:
            run_date = today
        data = get_integration_asten(run_date)
    else:
        data = None
        run_date = today
        for _delta in range(3):
            _candidate = today - timedelta(days=_delta)
            _result    = get_integration_asten(run_date=_candidate)
            if _result.get('nb_entrees_total', 0) > 0:
                data     = _result
                run_date = _candidate
                break
        if data is None:
            data = get_integration_asten(run_date=today)

    def _build_shop(shop_ref, shop, pos_id, t):
        entrees = t.get('entrees', [])
        # art_ok : depuis le run le plus récent
        art_ok = 0
        if entrees:
            s = entrees[0].get('report_summary') or {}
            art_ok = (s.get('nb_crees') or 0) + (s.get('nb_maj') or 0)

        # Construire d'abord entrees_err (uniquement les runs avec erreurs articles)
        entrees_err = []
        for e in entrees:
            rs = e.get('report_summary') or {}
            nb_art_err = rs.get('nb_erreurs') or 0
            re_list    = e.get('report_errors') or []
            rl_list    = e.get('report_logs') or []
            if nb_art_err > 0 or re_list:
                # Priorité : report_errors → report_logs → rapport brut
                if re_list:
                    msg_src    = re_list
                    report_raw = ''
                elif nb_art_err > 0 and rl_list:
                    msg_src    = rl_list
                    report_raw = ''
                else:
                    msg_src    = []
                    report_raw = (e.get('report') or '')[:2000]
                entrees_err.append({
                    'created_at':   e.get('created_at', ''),
                    'status_value': e.get('status_value', 3),
                    'status_label': e.get('status_label', ''),
                    'type_detail':  e.get('type_detail', ''),
                    'nb_art_err':   nb_art_err,
                    'nb_crees':     rs.get('nb_crees') or 0,
                    'nb_maj':       rs.get('nb_maj') or 0,
                    'nb_formatees': rs.get('nb_formatees') or 0,
                    'messages':     [{'msg': er.get('message', ''), 'ts': er.get('timestamp', '')}
                                     for er in msg_src if er.get('message')],
                    'report_raw':   report_raw,
                })

        # art_err = somme des erreurs articles des runs en erreur (cohérent avec ce qui est affiché)
        art_err = sum(r['nb_art_err'] for r in entrees_err)

        return {
            'shop_ref':    shop_ref,
            'shop_name':   shop.get('name', shop_ref),
            'pos_id':      pos_id,
            'art_ok':      art_ok,
            'art_err':     art_err,
            'entrees_err': entrees_err,
            'entrees_all': entrees,
        }

    shops_with_errors = []
    shops_ok          = []
    total_shops = 0
    for pos_id, pos in data.get('pos', {}).items():
        for shop_ref, shop in pos.get('shops', {}).items():
            t = shop.get('types', {}).get(type_key, {})
            total_shops += 1
            if not t.get('integre'):
                continue
            built = _build_shop(shop_ref, shop, pos_id, t)
            if built['entrees_err']:  # a des runs avec erreurs articles réelles
                shops_with_errors.append(built)
            else:
                shops_ok.append(built)

    shops_with_errors.sort(key=lambda s: -s['art_err'])
    shops_ok.sort(key=lambda s: s['shop_ref'])

    total_art_ok  = sum(s['art_ok']  for s in shops_with_errors + shops_ok)
    total_art_err = sum(s['art_err'] for s in shops_with_errors)
    nb_shops_ok   = len(shops_ok)

    return render(request, 'dashboard/integration_asten_type_errors.html', {
        'data':              data,
        'run_date':          run_date,
        'date_str':          str(run_date),
        'type_key':          type_key,
        'type_label':        TYPES_OBLIGATOIRES[type_key],
        'shops_with_errors': shops_with_errors,
        'shops_ok':          shops_ok,
        'total_shops':       total_shops,
        'nb_shops_ok':       nb_shops_ok,
        'total_art_ok':      total_art_ok,
        'total_art_err':     total_art_err,
    })


# ---------------------------------------------------------------------------
# Vues Factures (Asten / Cyrus / Backup)
# ---------------------------------------------------------------------------

def _factures_parse_date_filter(date_str):
    """Parse une date filtre (YYYY-MM-DD) → date ou None."""
    if not date_str:
        return None
    try:
        from datetime import date as _date
        return datetime.strptime(date_str.strip(), '%Y-%m-%d').date()
    except (ValueError, AttributeError):
        return None


def _paginate(request, rows, per_page=200):
    """Pagine une liste. Retourne (page_obj, paginator)."""
    from django.core.paginator import Paginator
    paginator = Paginator(rows, per_page)
    page_num  = request.GET.get('page', 1)
    try:
        page_obj = paginator.page(page_num)
    except Exception:
        page_obj = paginator.page(1)
    return page_obj, paginator


def vue_factures_asten(request):
    from imports.services import get_factures_verification
    result = get_factures_verification()
    rows   = result['asten']
    error  = result['error']

    # Dédoublonner Asten sur (n_bon_livraison, date, magasin)
    seen = set()
    dedup = []
    for r in rows:
        key = (r['n_bon_livraison'], r['date_reception_date'], r['magasin'])
        if key not in seen:
            seen.add(key)
            dedup.append(r)
    rows = dedup

    # Filtres
    f_magasin    = request.GET.get('magasin', '').strip()
    f_date_debut = _factures_parse_date_filter(request.GET.get('date_debut', ''))
    f_date_fin   = _factures_parse_date_filter(request.GET.get('date_fin', ''))

    magasins_list = sorted({r['magasin'] for r in rows if r['magasin']})

    if f_magasin:
        rows = [r for r in rows if r['magasin'] == f_magasin]
    if f_date_debut:
        rows = [r for r in rows if r['date_reception_date'] and r['date_reception_date'] >= f_date_debut]
    if f_date_fin:
        rows = [r for r in rows if r['date_reception_date'] and r['date_reception_date'] <= f_date_fin]

    total    = len(rows)
    page_obj, paginator = _paginate(request, rows)

    return render(request, 'dashboard/factures_asten.html', {
        'rows':          page_obj,
        'page_obj':      page_obj,
        'paginator':     paginator,
        'error':         error,
        'magasins_list': magasins_list,
        'f_magasin':     f_magasin,
        'f_date_debut':  request.GET.get('date_debut', ''),
        'f_date_fin':    request.GET.get('date_fin', ''),
        'total':         total,
    })


def vue_factures_cyrus(request):
    from imports.services import get_factures_verification
    result = get_factures_verification()
    error  = result['error']

    # Utiliser les factures dédupliquées depuis joined
    rows = result['joined']

    # Filtres
    f_magasin    = request.GET.get('magasin', '').strip()
    f_nsee       = request.GET.get('nsee', '').strip()
    f_date_debut = _factures_parse_date_filter(request.GET.get('date_debut', ''))
    f_date_fin   = _factures_parse_date_filter(request.GET.get('date_fin', ''))

    magasins_list = sorted({r['cidc'] for r in rows if r['cidc']})
    nsee_list     = sorted({r['nsee'] for r in rows if r['nsee']})

    if f_magasin:
        rows = [r for r in rows if r['cidc'] == f_magasin]
    if f_nsee:
        rows = [r for r in rows if r['nsee'] == f_nsee]
    if f_date_debut:
        rows = [r for r in rows if r['dfac_date'] and r['dfac_date'] >= f_date_debut]
    if f_date_fin:
        rows = [r for r in rows if r['dfac_date'] and r['dfac_date'] <= f_date_fin]

    total    = len(rows)
    page_obj, paginator = _paginate(request, rows)

    return render(request, 'dashboard/factures_cyrus.html', {
        'rows':          page_obj,
        'page_obj':      page_obj,
        'paginator':     paginator,
        'error':         error,
        'magasins_list': magasins_list,
        'nsee_list':     nsee_list,
        'f_magasin':     f_magasin,
        'f_nsee':        f_nsee,
        'f_date_debut':  request.GET.get('date_debut', ''),
        'f_date_fin':    request.GET.get('date_fin', ''),
        'total':         total,
    })


def vue_factures_backup(request):
    from imports.services import get_factures_verification
    result = get_factures_verification()
    rows   = result['joined']
    stats  = result['stats']
    error  = result['error']

    # Filtres
    f_statut     = request.GET.get('statut', 'ecart').strip()  # défaut = écarts seulement
    f_magasin    = request.GET.get('magasin', '').strip()
    f_nsee       = request.GET.get('nsee', '').strip()
    f_date_debut = _factures_parse_date_filter(request.GET.get('date_debut', ''))
    f_date_fin   = _factures_parse_date_filter(request.GET.get('date_fin', ''))

    magasins_list = sorted({r['cidc'] for r in rows if r['cidc']})
    nsee_list     = sorted({r['nsee'] for r in rows if r['nsee']})

    if f_statut == 'integree':
        rows = [r for r in rows if r['integree']]
    elif f_statut == 'ecart':
        rows = [r for r in rows if not r['integree']]
    elif f_statut == 'manuel_integre':
        rows = [r for r in rows if r.get('statut_manuel') == 'integre']
    # 'tous' → pas de filtre
    if f_magasin:
        rows = [r for r in rows if r['cidc'] == f_magasin]
    if f_nsee:
        rows = [r for r in rows if r['nsee'] == f_nsee]
    if f_date_debut:
        rows = [r for r in rows if r['dfac_date'] and r['dfac_date'] >= f_date_debut]
    if f_date_fin:
        rows = [r for r in rows if r['dfac_date'] and r['dfac_date'] <= f_date_fin]

    total    = len(rows)
    page_obj, paginator = _paginate(request, rows, per_page=100)

    return render(request, 'dashboard/factures_backup.html', {
        'rows':          page_obj,
        'page_obj':      page_obj,
        'paginator':     paginator,
        'stats':         stats,
        'error':         error,
        'magasins_list': magasins_list,
        'nsee_list':     nsee_list,
        'f_statut':      f_statut,
        'f_magasin':     f_magasin,
        'f_nsee':        f_nsee,
        'f_date_debut':  request.GET.get('date_debut', ''),
        'f_date_fin':    request.GET.get('date_fin', ''),
        'total':         total,
    })


def detail_facture_cyrus(request):
    """Détail d'une facture Cyrus : tous les articles de (cle_facture, dfac_date, cidc)."""
    from imports.services import get_factures_verification
    cle   = request.GET.get('cle', '').strip()
    cidc  = request.GET.get('cidc', '').strip()
    dfac  = request.GET.get('dfac', '').strip()

    if not cle or not cidc:
        return render(request, 'dashboard/facture_detail_cyrus.html', {'error': 'Paramètres manquants.'})

    result = get_factures_verification()
    facture = None
    for r in result['joined']:
        if r['cle_facture'] == cle and r['cidc'] == cidc and str(r.get('dfac_str', '')) == dfac:
            facture = r
            break
    if facture is None:
        return render(request, 'dashboard/facture_detail_cyrus.html', {'error': f'Facture {cle} introuvable.'})

    return render(request, 'dashboard/facture_detail_cyrus.html', {
        'facture': facture,
        'error':   None,
    })


def detail_facture_asten(request):
    """Détail d'une réception Asten : toutes les lignes de (n_bon_livraison, magasin)."""
    from imports.services import _lire_asten_factures
    from django.conf import settings as _settings
    n_bon   = request.GET.get('n_bon', '').strip()
    magasin = request.GET.get('magasin', '').strip()

    if not n_bon or not magasin:
        return render(request, 'dashboard/facture_detail_asten.html', {'error': 'Paramètres manquants.'})

    asten_path = _settings.DOSSIER_FACTURES_ASTEN_CSV_PATH
    all_rows = _lire_asten_factures(asten_path)
    lignes = [r for r in all_rows if r['n_bon_livraison'] == n_bon and r['magasin'] == magasin]

    if not lignes:
        return render(request, 'dashboard/facture_detail_asten.html', {'error': f'Réception {n_bon} introuvable.'})

    premier = lignes[0]
    return render(request, 'dashboard/facture_detail_asten.html', {
        'premier': premier,
        'lignes':  lignes,
        'error':   None,
    })


def set_statut_facture_ecart(request):
    """API POST : enregistre le statut manuel d'une facture en écart."""
    from core.permissions import user_has_perm
    from django.http import JsonResponse
    if not user_has_perm(request.user, 'modifier_statuts'):
        return JsonResponse({'error': 'Non autorisé'}, status=403)
    import json
    from imports.models import FactureEcartStatut
    if request.method != 'POST':
        return JsonResponse({'ok': False, 'error': 'Méthode non autorisée'}, status=405)
    try:
        data        = json.loads(request.body)
        cle         = data.get('cle_facture', '').strip()
        dfac_str    = data.get('dfac_str', '').strip()
        cidc        = data.get('cidc', '').strip()
        statut      = data.get('statut', '').strip()
        note        = data.get('note', '').strip()
        if not cle or not cidc or statut not in ('non_integre', 'integre', 'ignore'):
            return JsonResponse({'ok': False, 'error': 'Paramètres invalides'}, status=400)
        obj, created = FactureEcartStatut.objects.update_or_create(
            cle_facture=cle,
            dfac_str=dfac_str,
            cidc=cidc,
            defaults={'statut': statut, 'note': note},
        )
        return JsonResponse({'ok': True, 'statut': statut, 'created': created})
    except Exception as e:
        return JsonResponse({'ok': False, 'error': str(e)}, status=500)
