from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse, HttpResponse
from django.views.decorators.http import require_http_methods
from django.utils.dateparse import parse_date
from django.urls import reverse
from django.core.paginator import Paginator
from datetime import datetime, timedelta
from django.utils import timezone
from django.db.models import Q, Prefetch, Exists, OuterRef, Count, Sum
from django.db import IntegrityError
from django.db.models.deletion import ProtectedError
import threading

# ── État global de l'import asynchrone ────────────────────────────────────────
_import_lock = threading.Lock()
_import_state = {
    'running': False,
    'started_at': None,
    'finished_at': None,
    'imported': 0,
    'ecarts_crees': 0,
    'ecarts_resolus': 0,
    'error': None,
}

def _run_import_background():
    """Lance scanner_et_importer_fichiers + recalculer_ecarts dans un thread."""
    global _import_state
    import time as _time
    with _import_lock:
        if _import_state['running']:
            return  # déjà en cours, ne pas relancer
        _import_state.update({'running': True, 'started_at': _time.time(),
                               'finished_at': None, 'imported': 0,
                               'ecarts_crees': 0, 'ecarts_resolus': 0, 'error': None})

    try:
        from imports.services import scanner_et_importer_fichiers as _scan
        from ecarts.services import recalculer_ecarts as _recalc
        fichiers = _scan()
        res = _recalc()
        crees   = res.get('ecarts_crees', 0)   if isinstance(res, dict) else (res or 0)
        resolus = res.get('ecarts_resolus', 0) if isinstance(res, dict) else 0
        with _import_lock:
            _import_state.update({'running': False, 'finished_at': _time.time(),
                                   'imported': len(fichiers),
                                   'ecarts_crees': crees, 'ecarts_resolus': resolus})
    except Exception as _e:
        with _import_lock:
            _import_state.update({'running': False, 'finished_at': _time.time(),
                                   'error': str(_e)})
# ───────────────────────────────────────────────────────────────────────────────
from imports.services import (
    scanner_et_importer_fichiers,
    scanner_factures_sage,
    get_factures_sage_prefixes,
    scanner_factures_backup,
)
from imports.models import ImportFichier, FactureSage, FactureBackupCyrus
from ecarts.services import recalculer_ecarts
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
            total = CommandeGPV.objects.filter(date_creation__date__gte=d1, date_creation__date__lte=d2, statut__iexact='Transmise').count()
            ni = EcartGPV.objects.filter(commande_gpv__date_creation__date__gte=d1, commande_gpv__date_creation__date__lte=d2, statut='ouvert').count()
            q0 = EcartGPV.objects.filter(commande_gpv__date_creation__date__gte=d1, commande_gpv__date_creation__date__lte=d2, statut='quantite_0').count()
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


def _get_top5_magasins(debut=None, fin=None, n=5, source=None):
    """Retourne le top N magasins par source sur la période donnée (défaut : 30 derniers jours)."""
    from datetime import date, timedelta
    from django.db.models import Count, Q
    today = date.today()
    if debut is None:
        debut = today - timedelta(days=29)
    if fin is None:
        fin = today
    n = max(1, min(int(n), 100))

    # Mapping source → flags
    ALL = (None, '')
    want_asten       = source in ALL or source == 'commandes_asten'
    want_gpv         = source in ALL or source == 'commandes_gpv'
    want_legend      = source in ALL or source == 'commandes_legend'
    want_br          = source in ALL or source == 'br'
    want_sage        = source in ALL or source == 'facture_sage'
    want_backup      = source in ALL or source == 'facture_backup'
    want_remontees   = source == 'remontees'
    # Sources qui désactivent commandes
    if source in ('remontees', 'factures', 'versions', 'facture_sage', 'facture_backup'):
        want_asten = want_gpv = want_legend = want_br = False
    if source not in (None, '', 'facture_sage', 'facture_backup'):
        want_sage = want_backup = False

    try:
        asten = list(
            CommandeAsten.objects.filter(date_commande__gte=debut, date_commande__lte=fin)
            .values('code_magasin__code', 'code_magasin__nom')
            .annotate(total=Count('id')).order_by('-total')[:n]
        ) if want_asten else []
    except Exception:
        asten = []

    try:
        gpv = list(
            CommandeGPV.objects.filter(date_creation__date__gte=debut, date_creation__date__lte=fin, statut__iexact='Transmise')
            .values('code_magasin__code', 'code_magasin__nom')
            .annotate(total=Count('id')).order_by('-total')[:n]
        ) if want_gpv else []
    except Exception:
        gpv = []

    try:
        legend = list(
            CommandeLegend.objects.filter(date_commande__gte=debut, date_commande__lte=fin)
            .values('depot_destination')
            .annotate(total=Count('id')).order_by('-total')[:n]
        ) if want_legend else []
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
        ) if want_br else []
    except Exception:
        br = []

    # Top remontées par magasin
    remontees = []
    if want_remontees:
        try:
            from tickets.models import Ticket
            remontees = list(
                Ticket.objects.filter(date_creation__date__gte=debut, date_creation__date__lte=fin)
                .values('magasin__code', 'magasin__nom')
                .annotate(total=Count('id')).order_by('-total')[:n]
            )
        except Exception:
            remontees = []

    # Top Facture Sage par prefix (pas de magasin FK)
    sage = []
    if want_sage:
        try:
            from imports.models import FactureSage
            sage = list(
                FactureSage.objects.filter(date_depot__gte=debut, date_depot__lte=fin)
                .values('prefix')
                .annotate(total=Count('id')).order_by('-total')[:n]
            )
        except Exception:
            sage = []

    # Top Facture Backup par magasin (nom_fichier prefix ou magasin_code si dispo)
    backup = []
    if want_backup:
        try:
            from imports.models import FactureBackupCyrus
            backup = list(
                FactureBackupCyrus.objects.filter(date_modif__date__gte=debut, date_modif__date__lte=fin)
                .values('magasin_code')
                .annotate(total=Count('id')).order_by('-total')[:n]
            )
        except Exception:
            backup = []

    return {'asten': asten, 'gpv': gpv, 'legend': legend, 'br': br,
            'remontees': remontees, 'sage': sage, 'backup': backup,
            'debut': debut, 'fin': fin, 'n': n}


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
    # Pour GPV sans filtre de date : défaut = non_integres (évite de charger les 61k résolus)
    # Avec un filtre de date actif : afficher tout (sinon les nouvelles commandes sans écart calculé sont invisibles)
    _has_date_filter = bool(date_debut or date_fin or periode)
    _default_show = 'non_integres' if (type_donnees == 'commandes_gpv' and not _has_date_filter) else ''
    show = request.GET.get('show', _default_show)
    
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
    br_trouvees = BRAsten.objects.none()
    br_non_trouvees = BRAsten.objects.none()
    statut_ic = ''
    from django.core.paginator import Paginator as _Pag
    from django.db.models import Case, When, IntegerField as _IntField
    _dashboard_page_obj = None
    _dashboard_per_page = int(request.GET.get('per_page', 30))
    if _dashboard_per_page not in [30, 50, 100, 200]:
        _dashboard_per_page = 30


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
        
        # Queryset ordonné : non-intégrés (écart ouvert) d'abord, puis intégrés
        from django.db.models import Case, When, IntegerField as _IntField
        commandes_asten_qs = CommandeAsten.objects.filter(**filtres_asten)\
            .select_related('code_magasin')\
            .prefetch_related(Prefetch('ecart', queryset=EcartCommande.objects.all()))\
            .exclude(ecart__statut='quantite_0')\
            .annotate(_prio=Case(
                When(ecart__statut='ouvert', then=0),
                default=1, output_field=_IntField()
            ))\
            .order_by('_prio', '-date_commande', 'numero_commande')

        if show == 'non_integres':
            commandes_asten_qs = commandes_asten_qs.filter(ecart__statut='ouvert')

        # Paginer le queryset directement
        from django.core.paginator import Paginator as _Pag
        _per_page_asten = int(request.GET.get('per_page', 30))
        if _per_page_asten not in [30, 50, 100, 200]:
            _per_page_asten = 30
        _pag_asten = _Pag(commandes_asten_qs, _per_page_asten)
        _page_asten = _pag_asten.get_page(request.GET.get('page', 1))

        # Lookup Cyrus uniquement sur la page courante
        cyrus_lookup = {}
        page_cmds = list(_page_asten)
        if page_cmds:
            asten_keys = [(cmd.date_commande, cmd.numero_commande, cmd.code_magasin.code) for cmd in page_cmds]
            cyrus_commands_list = []
            for i in range(0, len(asten_keys), 50):
                batch = asten_keys[i:i+50]
                q_objects = Q()
                for date, numero, code in batch:
                    q_objects |= Q(date_commande=date, numero_commande=numero, code_magasin__code=code)
                cyrus_commands_list.extend(CommandeCyrus.objects.filter(q_objects).select_related('code_magasin'))
            for cyrus_cmd in cyrus_commands_list:
                key = (cyrus_cmd.date_commande, cyrus_cmd.numero_commande, cyrus_cmd.code_magasin.code)
                cyrus_lookup[key] = cyrus_cmd

        commandes_data = []
        for cmd_asten in page_cmds:
            key = (cmd_asten.date_commande, cmd_asten.numero_commande, cmd_asten.code_magasin.code)
            cmd_cyrus = cyrus_lookup.get(key)
            try:
                ecart = cmd_asten.ecart
            except Exception:
                ecart = None
            is_integre = False
            if ecart:
                if ecart.statut in ('resolu', 'ignore'):
                    is_integre = True
                elif ecart.statut == 'ouvert':
                    is_integre = cmd_cyrus is not None
            else:
                is_integre = cmd_cyrus is not None
            commandes_data.append({'asten': cmd_asten, 'cyrus': cmd_cyrus, 'integre': is_integre, 'ecart': ecart})

        # Stocker la page pour la pagination dans le contexte
        _dashboard_page_obj = _page_asten
        _dashboard_per_page = _per_page_asten
        titre_tableau = "Comparaison Asten vs Cyrus"
        
    elif type_donnees == 'commandes_gpv':
        # Récupérer les commandes GPV avec leurs statuts d'intégration
        # IMPORTANT: Seules les commandes avec statut "Transmise" doivent être dans Cyrus
        filtres_gpv = {}
        filtres_cyrus = {}
        if date_debut_parsed:
            filtres_gpv['date_creation__date__gte'] = date_debut_parsed
            filtres_cyrus['date_commande__gte'] = date_debut_parsed
        if date_fin_parsed:
            filtres_gpv['date_creation__date__lte'] = date_fin_parsed
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
            filtres_ecarts['commande_gpv__date_creation__date__gte'] = date_debut_parsed
        if date_fin_parsed:
            filtres_ecarts['commande_gpv__date_creation__date__lte'] = date_fin_parsed
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
        
        # Queryset GPV ordonné : non-intégrés (écart ouvert) d'abord
        commandes_gpv_qs = CommandeGPV.objects.filter(**filtres_gpv)\
            .select_related('code_magasin')\
            .prefetch_related(Prefetch('ecart', queryset=EcartGPV.objects.all()))\
            .exclude(ecart__statut='quantite_0')\
            .annotate(_prio=Case(
                When(ecart__statut='ouvert', then=0),
                default=1, output_field=_IntField()
            ))\
            .order_by('_prio', '-date_creation', 'numero_commande')

        if show == 'non_integres':
            commandes_gpv_qs = commandes_gpv_qs.filter(ecart__statut='ouvert')

        _per_page_gpv = int(request.GET.get('per_page', 30))
        if _per_page_gpv not in [30, 50, 100, 200]:
            _per_page_gpv = 30
        _pag_gpv = _Pag(commandes_gpv_qs, _per_page_gpv)
        _page_gpv = _pag_gpv.get_page(request.GET.get('page', 1))

        cyrus_lookup = {}
        cyrus_pair_lookup = set()
        page_gpv_cmds = list(_page_gpv)
        commandes_transmise = [cmd for cmd in page_gpv_cmds if (cmd.statut or '').strip().upper() in ('TRANSMISE', 'TRANSMIS')]
        if commandes_transmise:
            gpv_keys = [(cmd.date_creation, cmd.numero_commande, cmd.code_magasin.code) for cmd in commandes_transmise]
            gpv_pairs = list({(cmd.numero_commande, cmd.code_magasin.code) for cmd in commandes_transmise})
            cyrus_commands_list = []
            for i in range(0, len(gpv_keys), 50):
                batch = gpv_keys[i:i+50]
                q_objects = Q()
                for date, numero, code in batch:
                    q_objects |= Q(date_commande=date, numero_commande=numero, code_magasin__code=code)
                cyrus_commands_list.extend(CommandeCyrus.objects.filter(q_objects).select_related('code_magasin'))
            for cyrus_cmd in cyrus_commands_list:
                key = (cyrus_cmd.date_commande, cyrus_cmd.numero_commande, cyrus_cmd.code_magasin.code)
                cyrus_lookup[key] = cyrus_cmd
            for i in range(0, len(gpv_pairs), 50):
                batch_pairs = gpv_pairs[i:i+50]
                q_objects = Q()
                for numero, code in batch_pairs:
                    q_objects |= Q(numero_commande=numero, code_magasin__code=code)
                for cyrus_cmd in CommandeCyrus.objects.filter(q_objects).select_related('code_magasin'):
                    cyrus_pair_lookup.add((cyrus_cmd.numero_commande, cyrus_cmd.code_magasin.code))

        commandes_data = []
        for cmd_gpv in page_gpv_cmds:
            statut_gpv = (cmd_gpv.statut or '').strip().upper()
            doit_etre_dans_cyrus = statut_gpv in ('TRANSMISE', 'TRANSMIS')
            cmd_cyrus = None
            if doit_etre_dans_cyrus:
                key = (cmd_gpv.date_creation, cmd_gpv.numero_commande, cmd_gpv.code_magasin.code)
                cmd_cyrus = cyrus_lookup.get(key)
                if cmd_cyrus is None and (cmd_gpv.numero_commande, cmd_gpv.code_magasin.code) in cyrus_pair_lookup:
                    cmd_cyrus = True
            try:
                ecart = cmd_gpv.ecart if doit_etre_dans_cyrus else None
            except Exception:
                ecart = None
            if doit_etre_dans_cyrus:
                if ecart:
                    if ecart.statut in ('resolu', 'ignore'):
                        is_integre = True
                    else:
                        is_integre = cmd_cyrus is not None
                else:
                    is_integre = cmd_cyrus is not None
            else:
                is_integre = True
            commandes_data.append({'gpv': cmd_gpv, 'cyrus': cmd_cyrus, 'integre': is_integre, 'ecart': ecart, 'doit_etre_dans_cyrus': doit_etre_dans_cyrus})

        _dashboard_page_obj = _page_gpv
        _dashboard_per_page = _per_page_gpv
        titre_tableau = "Comparaison GPV vs Cyrus"
        
    elif type_donnees == 'commandes_legend':
        # Flux : Legend (exportée) → GPV → Cyrus
        # Stats via jointure SQL directe Legend → GPV (sans dépendre des écarts calculés)
        filtres_legend = {}
        if date_debut_parsed:
            filtres_legend['date_commande__gte'] = date_debut_parsed
        if date_fin_parsed:
            filtres_legend['date_commande__lte'] = date_fin_parsed

        from django.db import connection as _conn2
        _date_cond = ""
        _p = []
        if date_debut_parsed:
            _date_cond += " AND l.date_commande >= %s"; _p.append(str(date_debut_parsed))
        if date_fin_parsed:
            _date_cond += " AND l.date_commande <= %s"; _p.append(str(date_fin_parsed))
        with _conn2.cursor() as _cur2:
            _cur2.execute(f"""                SELECT
                    COUNT(*) AS total,
                    COUNT(*) FILTER (WHERE g.numero_commande IS NOT NULL) AS integrees,
                    COUNT(*) FILTER (WHERE g.numero_commande IS NULL)     AS non_integrees
                FROM legend_commandelegend l
                LEFT JOIN gpv_commandegpv g
                    ON REGEXP_REPLACE(g.numero_commande,'[^0-9]','','g') =
                       LTRIM(REGEXP_REPLACE(l.numero_commande,'[^0-9]','','g'),'0')
                WHERE l.exportee = TRUE {_date_cond}
            """, _p)
            _r = _cur2.fetchone()
        total_legend_exportee  = _r[0] or 0
        commandes_integres     = _r[1] or 0
        commandes_non_integres = _r[2] or 0
        total_legend_pour_stats = total_legend_exportee
        taux_integration    = round(commandes_integres     / total_legend_pour_stats * 100, 2) if total_legend_pour_stats else 0
        taux_non_integration = round(commandes_non_integres / total_legend_pour_stats * 100, 2) if total_legend_pour_stats else 0

                stats = {
            'total_source': total_legend_pour_stats,
            'total_target': CommandeGPV.objects.count(),
            'integres': commandes_integres,
            'non_integres': commandes_non_integres,
            'taux_integration': taux_integration,
            'taux_non_integration': taux_non_integration,
        }

        def normalize_numero(numero):
            if not numero:
                return ''
            digits = ''.join(ch for ch in str(numero).strip() if ch.isdigit())
            return digits.lstrip('0') or '0' if digits else str(numero).strip()

        # Queryset Legend ordonné : non-intégrés (écart ouvert) d'abord
        commandes_legend_qs = CommandeLegend.objects.filter(**filtres_legend)\
            .prefetch_related(Prefetch('ecart', queryset=EcartLegend.objects.all()))\
            .exclude(ecart__statut='quantite_0')\
            .annotate(_prio=Case(
                When(ecart__statut='ouvert', then=0),
                default=1, output_field=_IntField()
            ))\
            .order_by('_prio', '-date_commande', 'numero_commande')

        if show == 'non_integres':
            commandes_legend_qs = commandes_legend_qs.filter(ecart__statut='ouvert')

        _per_page_legend = int(request.GET.get('per_page', 30))
        if _per_page_legend not in [30, 50, 100, 200]:
            _per_page_legend = 30
        _pag_legend = _Pag(commandes_legend_qs, _per_page_legend)
        _page_legend = _pag_legend.get_page(request.GET.get('page', 1))

        # Lookup GPV uniquement sur les numéros de la page courante
        # Flux : Legend (exportée) → GPV → Cyrus
        gpv_numero_lookup = set()
        page_legend_cmds = list(_page_legend)
        if page_legend_cmds:
            numeros_page = [normalize_numero(cmd.numero_commande) for cmd in page_legend_cmds]
            for gpv_num in CommandeGPV.objects.filter(
                numero_commande__in=numeros_page
            ).values_list('numero_commande', flat=True):
                gpv_numero_lookup.add(normalize_numero(gpv_num))

        commandes_data = []
        for cmd_legend in page_legend_cmds:
            numero_normalise = normalize_numero(cmd_legend.numero_commande)
            gpv_present = numero_normalise in gpv_numero_lookup
            try:
                ecart = cmd_legend.ecart
            except Exception:
                ecart = None
            integre = True
            etape_blocage = None
            if cmd_legend.exportee:
                if ecart and ecart.statut in ('resolu', 'ignore'):
                    integre = True
                elif not gpv_present:
                    integre = False
                    etape_blocage = "Absente dans GPV"
            commandes_data.append({
                'legend': cmd_legend,
                'cyrus_present': gpv_present,  # conserve le nom pour le template
                'integre': integre,
                'etape_blocage': etape_blocage,
                'ecart': ecart,
            })

        _dashboard_page_obj = _page_legend
        _dashboard_per_page = _per_page_legend
        titre_tableau = "Comparaison Legend vs GPV"
        
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
        from django.db import connection as _conn

        backup_error = None
        filtre_full_asten = request.GET.get('full_asten') == '1'
        filtre_statut     = request.GET.get('statut_fv', 'non_integre')

        try:
            from imports.services import get_factures_stats_sql
            fv_sql = get_factures_stats_sql()
        except Exception:
            fv_sql = {}

        # ── Conditions WHERE communes ──────────────────────────────────────
        _where = ["m.statut_effectif != 'ignore'"]
        _params = []
        if filtre_full_asten:
            _where.append("COALESCE(fa.full_asten, false) = true")
        if code_magasin:
            _where.append("m.cidc = ANY(%s)")
            _params.append(list(code_magasin))
        if date_debut_parsed:
            _where.append("m.dfac_date >= %s")
            _params.append(date_debut_parsed)
        if date_fin_parsed:
            _where.append("m.dfac_date <= %s")
            _params.append(date_fin_parsed)
        _where_sql = "WHERE " + " AND ".join(_where)

        backup_files = None

        try:
            with _conn.cursor() as _cur:
                # ── Agrégats globaux (1 requête) ───────────────────────────
                _cur.execute(f"""
                    SELECT
                        COUNT(*)                                                          AS nb_tot,
                        SUM(CASE WHEN m.statut_effectif = 'integre'       THEN 1 ELSE 0 END) AS nb_int,
                        SUM(CASE WHEN m.statut_effectif = 'integre_vide'  THEN 1 ELSE 0 END) AS nb_vide,
                        SUM(CASE WHEN m.statut_effectif = 'non_integre'   THEN 1 ELSE 0 END) AS nb_eca,
                        SUM(CASE WHEN m.statut_effectif = 'non_full_asten' THEN 1 ELSE 0 END) AS nb_nfa
                    FROM mv_factures_joined m
                    LEFT JOIN core_magasin fa ON fa.code = m.cidc
                    {_where_sql}
                """, _params)
                _r = _cur.fetchone()
                nb_tot, nb_int, nb_vide, nb_eca, nb_nfa = (int(v or 0) for v in _r)

                # ── Stats par magasin (1 requête) ──────────────────────────
                _cur.execute(f"""
                    SELECT
                        m.cidc,
                        COALESCE(fa.full_asten, false)                                    AS full_asten,
                        COUNT(*)                                                          AS total,
                        SUM(CASE WHEN m.statut_effectif = 'integre'        THEN 1 ELSE 0 END) AS integrees,
                        SUM(CASE WHEN m.statut_effectif = 'integre_vide'   THEN 1 ELSE 0 END) AS integrees_vide,
                        SUM(CASE WHEN m.statut_effectif = 'non_integre'    THEN 1 ELSE 0 END) AS ecarts,
                        SUM(CASE WHEN m.statut_effectif = 'non_full_asten' THEN 1 ELSE 0 END) AS non_full_asten,
                        SUM(CASE WHEN m.has_ecart_valo                     THEN 1 ELSE 0 END) AS ecarts_valo
                    FROM mv_factures_joined m
                    LEFT JOIN core_magasin fa ON fa.code = m.cidc
                    {_where_sql}
                    GROUP BY m.cidc, fa.full_asten
                    ORDER BY m.cidc
                """, _params)
                stats_par_magasin = {
                    row[0]: {
                        'full_asten': bool(row[1]),
                        'total': int(row[2] or 0),
                        'integrees': int(row[3] or 0),
                        'integrees_vide': int(row[4] or 0),
                        'ecarts': int(row[5] or 0),
                        'non_full_asten': int(row[6] or 0),
                        'ecarts_valo': int(row[7] or 0),
                    }
                    for row in _cur.fetchall()
                }

                # ── Filtre statut pour le tableau (LIMIT/OFFSET) ───────────
                _statut_where = _where_sql
                _statut_params = list(_params)
                if filtre_statut == 'integre':
                    _statut_where += " AND m.statut_effectif = 'integre'"
                elif filtre_statut == 'integre_vide':
                    _statut_where += " AND m.statut_effectif = 'integre_vide'"
                elif filtre_statut == 'non_integre':
                    _statut_where += " AND m.statut_effectif = 'non_integre'"
                elif filtre_statut == 'ecart_valo':
                    _statut_where += " AND m.has_ecart_valo = true"

                _cur.execute(f"SELECT COUNT(*) FROM mv_factures_joined m LEFT JOIN core_magasin fa ON fa.code = m.cidc {_statut_where}", _statut_params)
                _total_filtre = _cur.fetchone()[0]

                _dpp = _dashboard_per_page
                _pnum = max(1, int(request.GET.get('page', 1)))
                _npages = max(1, (_total_filtre + _dpp - 1) // _dpp)
                _pnum = min(_pnum, _npages)
                _offset = (_pnum - 1) * _dpp

                _cur.execute(f"""
                    SELECT
                        m.cle_facture, m.nfac, m.nsee, m.dfac_str, m.dfac_date,
                        m.cidc, m.pfth_total AS pfth_cyrus, m.valo_ttc AS valo_asten,
                        m.qt_asten, m.nb_articles, m.ecart_valo, m.has_ecart_valo,
                        m.statut_effectif, m.statut_manuel, m.integree AS integree_csv
                    FROM mv_factures_joined m
                    LEFT JOIN core_magasin fa ON fa.code = m.cidc
                    {_statut_where}
                    ORDER BY
                        CASE m.statut_effectif
                            WHEN 'non_integre'    THEN 0
                            WHEN 'non_full_asten' THEN 1
                            WHEN 'integre_vide'   THEN 2
                            ELSE 3
                        END,
                        m.dfac_date DESC
                    LIMIT %s OFFSET %s
                """, _statut_params + [_dpp, _offset])
                _cols = [d[0] for d in _cur.description]
                _joined_page = [dict(zip(_cols, row)) for row in _cur.fetchall()]

        except Exception as _e:
            backup_error = f"Erreur lecture vue matérialisée : {_e}"
            nb_tot = nb_int = nb_vide = nb_eca = nb_nfa = 0
            stats_par_magasin = {}
            _joined_page = []
            _total_filtre = 0
            _pnum = 1
            _npages = 1

        taux = round((nb_int + nb_vide) / nb_tot * 100, 1) if nb_tot else 0
        stats = {
            'total_source':         nb_tot,
            'total_target':         nb_tot,
            'integres':             nb_int,
            'integres_vide':        nb_vide,
            'non_integres':         nb_eca,
            'taux_integration':     taux,
            'taux_non_integration': round(100 - taux, 1) if nb_tot else 0,
            'par_magasin':          stats_par_magasin,
        }

        # Paginator-like object pour compatibilité template
        from django.core.paginator import Page, Paginator as _Pag
        _dummy_pag = _Pag(_joined_page, _dpp)
        _dashboard_page_obj = _dummy_pag.page(1)
        # On remplace object_list par les vraies données de la page
        _dashboard_page_obj.object_list     = _joined_page
        _dashboard_page_obj.number          = _pnum
        _dashboard_page_obj.paginator.count = _total_filtre
        _dashboard_page_obj.paginator.num_pages = _npages

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

        _per_page_br = int(request.GET.get('per_page', 30))
        if _per_page_br not in [30, 50, 100, 200]:
            _per_page_br = 30

        if statut_ic == 'integre':
            br_non_trouvees_qs = BRAsten.objects.none()
            br_trouvees_qs = BRAsten.objects.filter(ic_integre=True, **filtres_br_date).exclude(exclude_quantite_0).select_related('code_magasin').order_by('-date_br', 'numero_br')
        elif statut_ic == 'non_integre':
            br_trouvees_qs = BRAsten.objects.none()
            br_non_trouvees_qs = BRAsten.objects.filter(ic_integre=False, **filtres_br_date).exclude(exclude_quantite_0).select_related('code_magasin').order_by('-date_br', 'numero_br')
        else:
            br_non_trouvees_qs = BRAsten.objects.filter(ic_integre=False, **filtres_br_base).exclude(exclude_quantite_0).select_related('code_magasin').order_by('-date_br', 'numero_br')
            br_trouvees_qs = BRAsten.objects.filter(ic_integre=True, **filtres_br_date).exclude(exclude_quantite_0).select_related('code_magasin').order_by('-date_br', 'numero_br')

        br_non_trouvees = _Pag(br_non_trouvees_qs, _per_page_br).get_page(request.GET.get('page', 1))
        br_trouvees = _Pag(br_trouvees_qs, _per_page_br).get_page(request.GET.get('page', 1))
        _dashboard_page_obj = br_non_trouvees
        _dashboard_per_page = _per_page_br
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
        gpv_d    = {i['date_creation__date']: i['count'] for i in CommandeGPV.objects.filter(date_creation__date__gte=evo_debut, date_creation__date__lte=evo_fin).values('date_creation__date').annotate(count=Count('id'))}
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
        'page_obj': _dashboard_page_obj,
        'per_page': _dashboard_per_page,
        'per_page_options': [30, 50, 100, 200],
        'br_trouvees': br_trouvees if type_donnees == 'br' else None,
        'br_non_trouvees': br_non_trouvees if type_donnees == 'br' else None,
        'sage_files': sage_files if type_donnees == 'factures' else None,
        'sage_days': sage_days if type_donnees == 'factures' else None,
        'sage_error': sage_error if type_donnees == 'factures' else None,
        'backup_files': backup_files if type_donnees == 'factures_backup' else None,
        'backup_error': backup_error if type_donnees == 'factures_backup' else None,
        'backup_stats_magasin_json': __import__('json').dumps(locals().get('stats_par_magasin', {})) if type_donnees == 'factures_backup' else '{}',
        'backup_top10_json': __import__('json').dumps(
            [{'cidc': c, **s} for c, s in locals().get('fv_sql', {}).get('top10_ecarts', [])]
            if type_donnees == 'factures_backup' else []
        ) if type_donnees == 'factures_backup' else '[]',
        'backup_semaines_json': __import__('json').dumps({
            'courante': locals().get('fv_sql', {}).get('semaine_courante', {}),
            'precedente': locals().get('fv_sql', {}).get('semaine_precedente', {}),
        }) if type_donnees == 'factures_backup' else '{}',
        'full_asten_codes': sorted(locals().get('full_asten_codes', [])),
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
            'full_asten': request.GET.get('full_asten') == '1' and type_donnees == 'factures_backup',
            'statut_fv': request.GET.get('statut_fv', ''),
        },
        'periode': periode,
        'show': show,
        'semaine_comparison': _get_semaine_comparison(request.GET.get('periode_cmp', 'semaine')),
        'periode_cmp': request.GET.get('periode_cmp', 'semaine'),
        'top5': _get_top5_magasins(debut=date_debut_parsed, fin=date_fin_parsed, n=request.GET.get('top_n', 5), source=type_donnees) if type_donnees not in ('factures', 'factures_backup', 'version') else None,
        'top_url_base': f"?type_donnees={request.GET.get('type_donnees','commandes_asten')}&periode={periode}&date_debut={date_debut or ''}&date_fin={date_fin or ''}",
        'qs_sans_page': '&'.join(f"{k}={v}" for k, v in request.GET.items() if k != 'page'),
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
            # Inclure aussi les erreurs des types optionnels (supplier_pre_order) + extra (delivery...)
            _ia_nb_extra_erreurs = sum(
                1
                for _pos in ia_data.get('pos', {}).values()
                for _shop in _pos.get('shops', {}).values()
                if _shop.get('nb_erreurs_articles', 0) > sum(
                    _shop.get('types', {}).get(_tk, {}).get('nb_erreurs', 0)
                    for _tk in TYPES_OBLIGATOIRES
                )
            )
            context['ia_stats_type'] = _ia_stats_type
            context['ia_nb_fichiers_ok']      = sum(v['nb_ok']          for v in _ia_stats_type.values())
            context['ia_nb_fichiers_erreurs'] = sum(v['nb_ok_erreurs']  for v in _ia_stats_type.values()) + _ia_nb_extra_erreurs
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
    
    # ASTEN — comparaison directe avec Cyrus via SQL (sans dépendre des écarts calculés)
    try:
        from django.db.models import Count, Q as _Q
        from django.db import connection as _conn
        filtres_asten = {}
        if date_debut:
            filtres_asten['date_commande__gte'] = date_debut
        if date_fin:
            filtres_asten['date_commande__lte'] = date_fin

        total_asten = CommandeAsten.objects.filter(**filtres_asten).count()

        # Compter directement combien de commandes Asten existent dans Cyrus
        date_cond_a = ""
        date_cond_c = ""
        params = []
        if date_debut:
            date_cond_a += " AND a.date_commande >= %s"
            params.append(str(date_debut))
        if date_fin:
            date_cond_a += " AND a.date_commande <= %s"
            params.append(str(date_fin))

        with _conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    COUNT(*) FILTER (WHERE c.numero_commande IS NOT NULL) AS integrees,
                    COUNT(*) FILTER (WHERE c.numero_commande IS NULL)     AS non_integrees
                FROM asten_commandeasten a
                LEFT JOIN cyrus_commandecyrus c
                    ON c.numero_commande = a.numero_commande
                    AND c.code_magasin_id = a.code_magasin_id
                WHERE 1=1 {date_cond_a}
            """, params)
            row = cur.fetchone()
        commandes_integres_asten  = row[0] or 0
        commandes_non_integres_asten = row[1] or 0
        total_asten_pour_stats = total_asten
        taux_integration_asten     = round(commandes_integres_asten  / total_asten_pour_stats * 100, 2) if total_asten_pour_stats else 0
        taux_non_integration_asten = round(commandes_non_integres_asten / total_asten_pour_stats * 100, 2) if total_asten_pour_stats else 0
        stats_asten = {
            'total': total_asten_pour_stats,
            'integres': commandes_integres_asten,
            'non_integres': commandes_non_integres_asten,
            'taux_integration': taux_integration_asten,
            'taux_non_integration': taux_non_integration_asten,
        }
    except:
        stats_asten = {'total': 0, 'integres': 0, 'non_integres': 0, 'taux_integration': 0, 'taux_non_integration': 0}
    
    # GPV — comparaison directe avec Cyrus (transmises uniquement)
    try:
        from django.db.models import Count, Q as _Q
        from django.db import connection as _conn

        date_cond_gpv = ""
        params_gpv = []
        if date_debut:
            date_cond_gpv += " AND DATE(g.date_creation) >= %s"
            params_gpv.append(str(date_debut))
        if date_fin:
            date_cond_gpv += " AND DATE(g.date_creation) <= %s"
            params_gpv.append(str(date_fin))

        with _conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    COUNT(*)                                              AS total,
                    COUNT(*) FILTER (WHERE c.numero_commande IS NOT NULL) AS integrees,
                    COUNT(*) FILTER (WHERE c.numero_commande IS NULL)     AS non_integrees
                FROM gpv_commandegpv g
                LEFT JOIN cyrus_commandecyrus c
                    ON c.numero_commande = g.numero_commande
                    AND c.code_magasin_id = g.code_magasin_id
                WHERE UPPER(TRIM(g.statut)) IN ('TRANSMISE', 'TRANSMIS')
                {date_cond_gpv}
            """, params_gpv)
            row = cur.fetchone()
        total_gpv_transmise        = row[0] or 0
        commandes_integres_gpv     = row[1] or 0
        commandes_non_integres_gpv = row[2] or 0
        taux_integration_gpv     = round(commandes_integres_gpv     / total_gpv_transmise * 100, 2) if total_gpv_transmise else 0
        taux_non_integration_gpv = round(commandes_non_integres_gpv / total_gpv_transmise * 100, 2) if total_gpv_transmise else 0
        stats_gpv = {
            'total': total_gpv_transmise,
            'integres': commandes_integres_gpv,
            'non_integres': commandes_non_integres_gpv,
            'taux_integration': taux_integration_gpv,
            'taux_non_integration': taux_non_integration_gpv,
        }
    except:
        stats_gpv = {'total': 0, 'integres': 0, 'non_integres': 0, 'taux_integration': 0, 'taux_non_integration': 0}

    # LEGEND — comparaison avec GPV (flux : Legend exportée → GPV → Cyrus)
    try:
        from django.db import connection as _conn

        date_cond_leg = ""
        params_leg = []
        if date_debut:
            date_cond_leg += " AND l.date_commande >= %s"
            params_leg.append(str(date_debut))
        if date_fin:
            date_cond_leg += " AND l.date_commande <= %s"
            params_leg.append(str(date_fin))

        with _conn.cursor() as cur:
            cur.execute(f"""
                SELECT
                    COUNT(*)                                              AS total,
                    COUNT(*) FILTER (WHERE g.numero_commande IS NOT NULL) AS integrees,
                    COUNT(*) FILTER (WHERE g.numero_commande IS NULL)     AS non_integrees
                FROM legend_commandelegend l
                LEFT JOIN gpv_commandegpv g
                    ON REGEXP_REPLACE(g.numero_commande, '[^0-9]', '', 'g') =
                       LTRIM(REGEXP_REPLACE(l.numero_commande, '[^0-9]', '', 'g'), '0')
                WHERE l.exportee = TRUE
                {date_cond_leg}
            """, params_leg)
            row = cur.fetchone()
        total_legend_exportee          = row[0] or 0
        commandes_integres_legend      = row[1] or 0
        commandes_non_integres_legend  = row[2] or 0
        taux_integration_legend     = round(commandes_integres_legend     / total_legend_exportee * 100, 2) if total_legend_exportee else 0
        taux_non_integration_legend = round(commandes_non_integres_legend / total_legend_exportee * 100, 2) if total_legend_exportee else 0
        stats_legend = {
            'total': total_legend_exportee,
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
        
        from django.db.models import Count, Q as _Q
        _q0 = _Q(statut_ic__icontains='Quantité 0') | _Q(statut_ic__icontains='quantite_0') | _Q(statut_ic__icontains='Quantite 0')
        br_agg = BRAsten.objects.filter(**filtres_br).aggregate(
            total=Count('id'),
            quantite_0=Count('id', filter=_q0),
            trouvees=Count('id', filter=_Q(ic_integre=True) & ~_q0),
            non_trouvees=Count('id', filter=_Q(ic_integre=False) & ~_q0),
        )
        total_br = br_agg['total']
        br_quantite_0 = br_agg['quantite_0']
        total_br_pour_stats = total_br - br_quantite_0
        br_trouvees = br_agg['trouvees']
        br_non_trouvees = br_agg['non_trouvees']
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
    
    # FACTURES (Cyrus/Asten — stats SQL rapides)
    try:
        from imports.services import get_factures_stats_sql
        fv_sql = get_factures_stats_sql()
        nb_int_acc  = fv_sql.get('integrees', 0) + fv_sql.get('integrees_vide', 0)
        nb_eca_acc  = fv_sql.get('ecarts', 0)
        nb_tot_acc  = fv_sql.get('total', 0)
        taux_int = round(nb_int_acc / nb_tot_acc * 100, 2) if nb_tot_acc else 0
        stats_factures = {
            'total':               nb_tot_acc,
            'integres':            nb_int_acc,
            'non_integres':        nb_eca_acc,
            'taux_integration':    taux_int,
            'taux_non_integration': round(100 - taux_int, 2),
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
        
        from django.db.models import Count, Q as _Q
        ticket_agg = Ticket.objects.filter(**filtres_remontees).aggregate(
            total=Count('id'),
            resolu=Count('id', filter=_Q(statut=Ticket.STATUT_RESOLU)),
            en_cours=Count('id', filter=_Q(statut=Ticket.STATUT_EN_COURS)),
            en_attente=Count('id', filter=_Q(statut=Ticket.STATUT_EN_ATTENTE)),
            ferme=Count('id', filter=_Q(statut=Ticket.STATUT_FERME)),
        )
        total_remontees = ticket_agg['total']
        resolu_remontees = ticket_agg['resolu']
        en_cours_remontees = ticket_agg['en_cours']
        en_attente_remontees = ticket_agg['en_attente']
        ferme_remontees = ticket_agg['ferme']
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
            item['date_creation__date']: item['count']
            for item in CommandeGPV.objects
            .filter(date_creation__date__gte=evo_debut, date_creation__date__lte=evo_fin)
            .values('date_creation__date').annotate(count=Count('id'))
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
        try:
            from imports.models import FactureSage as _FS, FactureBackupCyrus as _FB
            sage_daily = {
                item['date_depot']: item['count']
                for item in _FS.objects
                .filter(date_depot__gte=evo_debut, date_depot__lte=evo_fin)
                .values('date_depot').annotate(count=Count('id'))
            }
            backup_daily = {
                item['date_modif__date']: item['count']
                for item in _FB.objects
                .filter(date_modif__date__gte=evo_debut, date_modif__date__lte=evo_fin)
                .values('date_modif__date').annotate(count=Count('id'))
            }
        except Exception:
            sage_daily = {}
            backup_daily = {}
        evolution_journaliere = {
            'labels':  labels_evo,
            'asten':   [asten_daily.get(d, 0)   for d in dates_evo],
            'gpv':     [gpv_daily.get(d, 0)      for d in dates_evo],
            'legend':  [legend_daily.get(d, 0)   for d in dates_evo],
            'br':      [br_daily.get(d, 0)        for d in dates_evo],
            'sage':    [sage_daily.get(d, 0)      for d in dates_evo],
            'backup':  [backup_daily.get(d, 0)    for d in dates_evo],
        }
    except Exception:
        evolution_journaliere = {'labels': [], 'asten': [], 'gpv': [], 'legend': [], 'br': [], 'sage': [], 'backup': []}

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

    # FACTURES SAGE
    try:
        from imports.models import FactureSage
        nb_sage = FactureSage.objects.count()
        stats_facture_sage = {'total': nb_sage}
    except Exception:
        stats_facture_sage = {'total': 0}

    # VERSION ASTEN
    try:
        from imports.models import ImportFichier
        derniere_version = ImportFichier.objects.filter(type_fichier='version_asten', statut='termine').order_by('-date_import').first()
        stats_version = {
            'derniere_import': derniere_version.date_import if derniere_version else None,
            'nom_fichier': derniere_version.nom_fichier if derniere_version else None,
        }
    except Exception:
        stats_version = {'derniere_import': None, 'nom_fichier': None}

    context = {
        'stats_asten': stats_asten,
        'stats_gpv': stats_gpv,
        'stats_legend': stats_legend,
        'stats_br': stats_br,
        'stats_factures': stats_factures,
        'stats_facture_sage': stats_facture_sage,
        'stats_version': stats_version,
        'stats_remontees': stats_remontees,
        'ia_rpos': ia_rpos,
        'evolution_journaliere': evolution_journaliere,
        'periode': periode,
        'date_debut': date_debut.strftime('%Y-%m-%d') if date_debut else '',
        'date_fin': date_fin.strftime('%Y-%m-%d') if date_fin else '',
        'semaine_comparison': _get_semaine_comparison(request.GET.get('periode_cmp', 'semaine')),
        'periode_cmp': request.GET.get('periode_cmp', 'semaine'),
        'top5': _get_top5_magasins(debut=date_debut, fin=date_fin, n=request.GET.get('top_n', 5), source=request.GET.get('top_source') or None),
        'top_source': request.GET.get('top_source', ''),
        'top_url_base': f"?periode={request.GET.get('periode','')}&date_debut={date_debut or ''}&date_fin={date_fin or ''}&periode_cmp={request.GET.get('periode_cmp','semaine')}",
    }

    # Mode partial : retourne uniquement le bloc top magasins (AJAX)
    if request.GET.get('partial') == 'top':
        from django.template.loader import render_to_string
        html = render_to_string('dashboard/_top_magasins.html', context, request=request)
        return HttpResponse(html)

    return render(request, 'dashboard/accueil.html', context)


@require_http_methods(["GET", "POST"])
def actualiser_stream(request):
    """
    Vue SSE : stream les logs d'import en temps réel vers le navigateur.
    Chaque message : data: {"msg": "...", "level": "..."}
    Dernier message : data: {"done": true, "ok": bool, "imported": N, ...}
    """
    from core.permissions import user_has_perm
    from django.http import StreamingHttpResponse
    import sys, io, json, traceback, threading

    if not user_has_perm(request.user, 'actualiser_importer'):
        def _denied():
            yield 'data: ' + json.dumps({'msg': 'Accès non autorisé.', 'level': 'error'}) + '\n\n'
            yield 'data: ' + json.dumps({'done': True, 'ok': False, 'imported': 0, 'crees': 0, 'resolus': 0}) + '\n\n'
        return StreamingHttpResponse(_denied(), content_type='text/event-stream')

    def _generate():
        def sse_line(msg, level='info'):
            return 'data: ' + json.dumps({'msg': msg, 'level': level}) + '\n\n'

        # Capturer les print() de services.py
        captured = []
        class PrintCapture(io.TextIOBase):
            def write(self, s):
                for line in s.splitlines():
                    line = line.strip()
                    if line:
                        captured.append(line)
                return len(s)
            def flush(self):
                pass

        def flush_captured():
            while captured:
                line = captured.pop(0)
                level = ('error' if any(w in line for w in ('Erreur', 'ERROR', 'ERREUR')) else
                         'warn'  if any(w in line for w in ('Attention', 'WARNING', 'warn')) else
                         'ok'    if any(w in line for w in ('→', 'importé', 'lignes en base', '[OK]', 'synced', 'Terminé')) else
                         'info')
                yield 'data: ' + json.dumps({'msg': line, 'level': level}) + '\n\n'

        old_stdout = sys.stdout
        sys.stdout = PrintCapture()

        try:
            yield sse_line('=== Scan des dossiers ===', 'section')
            dossiers = {
                'Asten':       settings.DOSSIER_COMMANDES_ASTEN_PATH,
                'Cyrus':       settings.DOSSIER_COMMANDES_CYRUS_PATH,
                'GPV':         settings.DOSSIER_COMMANDES_GPV_PATH,
                'Legend':      settings.DOSSIER_COMMANDES_LEGEND_PATH,
                'BR Asten':    settings.DOSSIER_BR_ASTEN_PATH,
                'BR IC':       settings.DOSSIER_BR_IC_PATH,
                'Anomalie BR': settings.DOSSIER_ANOMALIE_BR_PATH,
            }
            total_fichiers = 0
            for nom, chemin_str in dossiers.items():
                chemin = Path(chemin_str)
                if chemin.exists():
                    nb = len(list(chemin.glob('*.csv')) + list(chemin.glob('*.CSV')) +
                             list(chemin.glob('*.xlsx')) + list(chemin.glob('*.XLSX')) +
                             list(chemin.glob('*.xls'))  + list(chemin.glob('*.XLS')))
                    total_fichiers += nb
                    yield sse_line(f'  {nom} : {nb} fichier(s)', 'ok' if nb > 0 else 'warn')
                else:
                    yield sse_line(f'  {nom} : dossier introuvable', 'error')
            yield sse_line(f'  Total : {total_fichiers} fichier(s) à traiter', 'info')
            yield sse_line('', 'info')

            yield sse_line('=== Import des fichiers ===', 'section')

            # Lancer l'import dans un thread pour pouvoir streamer les print() en temps réel
            fichiers_importes = []
            done_event = threading.Event()

            def run_import():
                nonlocal fichiers_importes
                try:
                    fichiers_importes = scanner_et_importer_fichiers()
                except Exception as _e:
                    captured.append(f'Erreur import : {_e}')
                finally:
                    done_event.set()

            t = threading.Thread(target=run_import, daemon=True)
            t.start()

            while not done_event.wait(timeout=0.15):
                chunks = list(flush_captured())
                if chunks:
                    yield from chunks
                else:
                    yield ': ping\n\n'  # SSE keepalive — force le flush TCP
            yield from flush_captured()  # vider ce qui reste après done

            if not fichiers_importes:
                yield sse_line('  Aucun nouveau fichier à importer.', 'warn')
            yield sse_line(f'  → {len(fichiers_importes)} fichier(s) importé(s)', 'info')
            yield sse_line('', 'info')

            yield sse_line('=== Recalcul des écarts ===', 'section')
            resultat_ecarts = recalculer_ecarts()
            yield from flush_captured()
            if isinstance(resultat_ecarts, dict):
                crees   = resultat_ecarts.get('ecarts_crees', 0)
                resolus = resultat_ecarts.get('ecarts_resolus', 0)
            else:
                crees, resolus = (resultat_ecarts if isinstance(resultat_ecarts, int) else 0), 0
            yield sse_line(f'  Nouveaux écarts : {crees}',  'warn' if crees > 0 else 'ok')
            yield sse_line(f'  Écarts résolus  : {resolus}', 'ok')
            yield sse_line('', 'info')
            yield sse_line('=== Actualisation terminée ✓ ===', 'ok')
            yield 'data: ' + json.dumps({'done': True, 'ok': True,
                'imported': len(fichiers_importes), 'crees': crees, 'resolus': resolus}) + '\n\n'

        except Exception as e:
            yield from flush_captured()
            yield sse_line(f'ERREUR : {e}', 'error')
            for line in traceback.format_exc().splitlines():
                yield sse_line(line, 'error')
            yield 'data: ' + json.dumps({'done': True, 'ok': False, 'imported': 0, 'crees': 0, 'resolus': 0}) + '\n\n'
        finally:
            sys.stdout = old_stdout

    resp = StreamingHttpResponse(_generate(), content_type='text/event-stream')
    resp['Cache-Control'] = 'no-cache'
    resp['X-Accel-Buffering'] = 'no'
    return resp


@require_http_methods(["POST"])
def actualiser_donnees(request):
    """Lance l'import en arrière-plan et redirige immédiatement sans bloquer."""
    from core.permissions import user_has_perm
    if not user_has_perm(request.user, 'actualiser_importer'):
        messages.error(request, "Action non autorisée.")
        return redirect('dashboard:dashboard')

    with _import_lock:
        already_running = _import_state['running']

    if already_running:
        messages.warning(request, "Un import est déjà en cours, patientez quelques secondes.")
    else:
        t = threading.Thread(target=_run_import_background, daemon=True)
        t.start()
        messages.info(request, "Import lancé en arrière-plan — la page se met à jour automatiquement.")

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

    # Marquer la visite pour les notifications
    from django.utils import timezone as _tz
    request.session['ecarts_last_seen'] = _tz.now().isoformat()

    # Filtres
    date_debut  = request.GET.get('date_debut')
    date_fin    = request.GET.get('date_fin')
    code_magasin = request.GET.get('magasin')
    statut      = request.GET.get('statut', '')
    type_ecart  = request.GET.get('type_ecart', '')

    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed   = parse_date(date_fin)   if date_fin   else None

    # Statuts affichés par défaut (hors 'resolu' et 'quantite_0')
    statuts_actifs = ['ouvert', 'ignore']

    from django.db import connection

    # ── Construction dynamique de la UNION ALL ──────────────────────────────
    unions = []
    params = []

    # ─ Asten ─
    if not type_ecart or type_ecart == 'asten':
        w_asten = ["1=1"]
        if statut:
            w_asten.append("ea.statut = %s"); params.append(statut)
        else:
            w_asten.append("ea.statut = ANY(%s)"); params.append(statuts_actifs)
        if date_debut_parsed:
            w_asten.append("ca.date_commande >= %s"); params.append(date_debut_parsed)
        if date_fin_parsed:
            w_asten.append("ca.date_commande <= %s"); params.append(date_fin_parsed)
        if code_magasin:
            w_asten.append("ca.code_magasin = %s"); params.append(code_magasin)
        unions.append(f"""
            SELECT ea.id, 'asten' AS type_ecart,
                   ea.statut, ea.date_creation,
                   ca.date_commande, ca.numero_commande,
                   ca.code_magasin  AS code_magasin,
                   ca.montant::text AS montant,
                   NULL::text       AS depot_origine,
                   NULL::text       AS depot_destination
            FROM ecarts_ecartcommande ea
            JOIN asten_commandeasten ca ON ca.id = ea.commande_asten_id
            WHERE {' AND '.join(w_asten)}
        """)

    # ─ GPV ─
    if not type_ecart or type_ecart == 'gpv':
        w_gpv = ["1=1"]
        if statut:
            w_gpv.append("eg.statut = %s"); params.append(statut)
        else:
            w_gpv.append("eg.statut = ANY(%s)"); params.append(statuts_actifs)
        if date_debut_parsed:
            w_gpv.append("cg.date_creation::date >= %s"); params.append(date_debut_parsed)
        if date_fin_parsed:
            w_gpv.append("cg.date_creation::date <= %s"); params.append(date_fin_parsed)
        if code_magasin:
            w_gpv.append("cg.code_magasin = %s"); params.append(code_magasin)
        unions.append(f"""
            SELECT eg.id, 'gpv' AS type_ecart,
                   eg.statut, eg.date_creation,
                   cg.date_creation::date AS date_commande,
                   cg.numero_commande,
                   cg.code_magasin  AS code_magasin,
                   NULL::text       AS montant,
                   NULL::text       AS depot_origine,
                   NULL::text       AS depot_destination
            FROM ecarts_ecartgpv eg
            JOIN gpv_commandegpv cg ON cg.id = eg.commande_gpv_id
            WHERE {' AND '.join(w_gpv)}
        """)

    # ─ Legend ─
    if (not type_ecart or type_ecart == 'legend') and not code_magasin:
        w_leg = ["1=1"]
        if statut:
            w_leg.append("el.statut = %s"); params.append(statut)
        else:
            w_leg.append("el.statut = ANY(%s)"); params.append(statuts_actifs)
        if date_debut_parsed:
            w_leg.append("cl.date_commande >= %s"); params.append(date_debut_parsed)
        if date_fin_parsed:
            w_leg.append("cl.date_commande <= %s"); params.append(date_fin_parsed)
        unions.append(f"""
            SELECT el.id, 'legend' AS type_ecart,
                   el.statut, el.date_creation,
                   cl.date_commande, cl.numero_commande,
                   NULL::text AS code_magasin,
                   NULL::text AS montant,
                   cl.depot_origine,
                   cl.depot_destination
            FROM ecarts_ecartlegend el
            JOIN legend_commandelegend cl ON cl.id = el.commande_legend_id
            WHERE {' AND '.join(w_leg)}
        """)

    if not unions:
        rows = []
        total = 0
    else:
        union_sql = " UNION ALL ".join(unions)
        count_sql = f"SELECT COUNT(*) FROM ({union_sql}) sub"
        with connection.cursor() as cur:
            cur.execute(count_sql, params)
            total = cur.fetchone()[0]

        per_page    = int(request.GET.get('per_page', 30))
        if per_page not in [30, 50, 100, 200]:
            per_page = 30
        page_number = max(1, int(request.GET.get('page', 1)))
        num_pages   = max(1, (total + per_page - 1) // per_page)
        page_number = min(page_number, num_pages)
        offset      = (page_number - 1) * per_page

        data_sql = f"""
            SELECT * FROM ({union_sql}) sub
            ORDER BY
                CASE WHEN statut = 'ouvert' THEN 0 ELSE 1 END,
                date_creation DESC
            LIMIT %s OFFSET %s
        """
        with connection.cursor() as cur:
            cur.execute(data_sql, params + [per_page, offset])
            cols = [c[0] for c in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

    if not rows:
        per_page    = int(request.GET.get('per_page', 30))
        if per_page not in [30, 50, 100, 200]:
            per_page = 30
        page_number = 1
        num_pages   = 1

    has_prev   = page_number > 1
    has_next   = page_number < num_pages
    page_range = list(range(max(1, page_number - 2), min(num_pages + 1, page_number + 3)))

    magasins = Magasin.objects.all().order_by('code')

    context = {
        'ecarts': rows,
        'ecarts_count': total,
        'page_num':   page_number,
        'num_pages':  num_pages,
        'has_prev':   has_prev,
        'has_next':   has_next,
        'page_range': page_range,
        'titre': "Liste des Écarts",
        'magasins': magasins,
        'filtres': {
            'date_debut':  date_debut  or '',
            'date_fin':    date_fin    or '',
            'magasin':     code_magasin or '',
            'statut':      statut      or '',
            'type_ecart':  type_ecart  or '',
        },
        'per_page': per_page,
        'per_page_options': [30, 50, 100, 200],
    }

    return render(request, 'dashboard/liste_ecarts.html', context)




def liste_commandes_asten(request):
    """Affiche la liste des commandes Asten"""
    from datetime import date, timedelta
    # Filtres
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    codes_magasins = request.GET.getlist('magasin')
    numero_commande = request.GET.get('numero_commande', '').strip()
    recherche_magasin = request.GET.get('recherche_magasin', '').strip()

    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed = parse_date(date_fin) if date_fin else None

    if not date_debut_parsed and not date_fin_parsed and not numero_commande and not codes_magasins:
        date_fin_parsed = date.today()
        date_debut_parsed = date_fin_parsed - timedelta(days=30)

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
    
    # Pagination
    per_page = int(request.GET.get('per_page', 30))
    if per_page not in [30, 50, 100, 200]:
        per_page = 30
    paginator = Paginator(commandes, per_page)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

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
        'per_page': per_page,
        'per_page_options': [30, 50, 100, 200],
    }

    return render(request, 'dashboard/liste_commandes_asten.html', context)


def liste_commandes_cyrus(request):
    """Affiche la liste des commandes Cyrus"""
    from datetime import date, timedelta
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    codes_magasins = request.GET.getlist('magasin')
    numero_commande = request.GET.get('numero_commande', '').strip()
    recherche_magasin = request.GET.get('recherche_magasin', '').strip()

    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed = parse_date(date_fin) if date_fin else None

    if not date_debut_parsed and not date_fin_parsed and not numero_commande and not codes_magasins:
        date_fin_parsed = date.today()
        date_debut_parsed = date_fin_parsed - timedelta(days=30)

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

    per_page = int(request.GET.get('per_page', 30))
    if per_page not in [30, 50, 100, 200]:
        per_page = 30
    paginator = Paginator(commandes, per_page)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

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
        'per_page': per_page,
        'per_page_options': [30, 50, 100, 200],
    }
    
    return render(request, 'dashboard/liste_commandes_cyrus.html', context)


def liste_br_asten(request):
    """Affiche la liste des BR Asten"""
    from datetime import date, timedelta
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    codes_magasins = request.GET.getlist('magasin')
    numero_br = request.GET.get('numero_br', '').strip()
    statut_ic = request.GET.get('statut_ic', '')

    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed = parse_date(date_fin) if date_fin else None

    if not date_debut_parsed and not date_fin_parsed and not numero_br and not codes_magasins:
        date_fin_parsed = date.today()
        date_debut_parsed = date_fin_parsed - timedelta(days=30)

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

    per_page = int(request.GET.get('per_page', 30))
    if per_page not in [30, 50, 100, 200]:
        per_page = 30
    paginator = Paginator(brs, per_page)
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
        'per_page': per_page,
        'per_page_options': [30, 50, 100, 200],
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
    per_page = int(request.GET.get('per_page', 30))
    if per_page not in [30, 50, 100, 200]:
        per_page = 30
    paginator = Paginator(brs, per_page)
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
        'per_page': per_page,
        'per_page_options': [30, 50, 100, 200],
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
        
        from br.models import BRICLigne
        # Lignes IC : match exact (numero_br + commande) ou par commande seule
        lignes_ic_exactes = BRICLigne.objects.filter(
            numero_br=br.numero_br,
            commande_fournisseur=br.commande_fournisseur
        ).order_by('-date_reception') if br.commande_fournisseur else BRICLigne.objects.none()
        lignes_ic_commande = BRICLigne.objects.filter(
            commande_fournisseur=br.commande_fournisseur
        ).exclude(numero_br=br.numero_br).order_by('-date_reception') if br.commande_fournisseur else BRICLigne.objects.none()

        context = {
            'br': br,
            'lignes_ic_exactes': lignes_ic_exactes,
            'lignes_ic_commande': lignes_ic_commande,
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
        # Chercher d'abord par date + numéro exact, puis fallback numéro seul
        # On utilise __in sur les candidats proches pour éviter de charger toute la table
        for cyrus_cmd in CommandeCyrus.objects.filter(
            date_commande=commande.date_commande,
            numero_commande__in=CommandeCyrus.objects.filter(
                date_commande=commande.date_commande
            ).values_list('numero_commande', flat=True)
        ):
            if normalize_numero(cyrus_cmd.numero_commande) == numero_legend_normalise:
                commande_cyrus = cyrus_cmd
                break

        if commande_cyrus is None:
            # Fallback : chercher uniquement les numéros qui commencent par les mêmes chiffres
            prefix = numero_legend_normalise[:4] if len(numero_legend_normalise) >= 4 else numero_legend_normalise
            for cyrus_cmd in CommandeCyrus.objects.filter(
                numero_commande__startswith=prefix
            ).values('id', 'numero_commande', 'date_commande', 'code_magasin_id')[:500]:
                if normalize_numero(cyrus_cmd['numero_commande']) == numero_legend_normalise:
                    commande_cyrus = CommandeCyrus.objects.filter(pk=cyrus_cmd['id']).first()
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
    from datetime import date, timedelta
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    codes_magasins = request.GET.getlist('magasin')
    numero_commande = request.GET.get('numero_commande', '').strip()
    recherche_magasin = request.GET.get('recherche_magasin', '').strip()

    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed = parse_date(date_fin) if date_fin else None

    if not date_debut_parsed and not date_fin_parsed and not numero_commande and not codes_magasins:
        date_fin_parsed = date.today()
        date_debut_parsed = date_fin_parsed - timedelta(days=30)

    filtres = {}
    if date_debut_parsed:
        filtres['date_creation__date__gte'] = date_debut_parsed
    if date_fin_parsed:
        filtres['date_creation__date__lte'] = date_fin_parsed
    if codes_magasins:
        filtres['code_magasin__code__in'] = codes_magasins
    if numero_commande:
        filtres['numero_commande__icontains'] = numero_commande
    
    commandes = CommandeGPV.objects.filter(**filtres).select_related(
        'code_magasin'
    ).order_by('-date_creation', 'numero_commande')

    per_page = int(request.GET.get('per_page', 30))
    if per_page not in [30, 50, 100, 200]:
        per_page = 30
    paginator = Paginator(commandes, per_page)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

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
        'per_page': per_page,
        'per_page_options': [30, 50, 100, 200],
    }
    
    return render(request, 'dashboard/liste_commandes_gpv.html', context)


def liste_commandes_legend(request):
    """Affiche la liste des commandes Legend"""
    from datetime import date, timedelta
    # Filtres
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    numero_commande = request.GET.get('numero_commande', '').strip()
    depot_recherche = request.GET.get('depot', '').strip()
    exportee = request.GET.get('exportee', '')

    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed = parse_date(date_fin) if date_fin else None

    # Par défaut : 30 derniers jours si aucun filtre de date ni de numéro
    _has_filtre = date_debut_parsed or date_fin_parsed or numero_commande or depot_recherche
    if not _has_filtre:
        date_fin_parsed = date.today()
        date_debut_parsed = date_fin_parsed - timedelta(days=30)

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

    per_page = int(request.GET.get('per_page', 30))
    if per_page not in [30, 50, 100, 200]:
        per_page = 30
    paginator = Paginator(commandes, per_page)
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
    # Récupérer uniquement les numéros/dates Cyrus pour les commandes de la page courante
    page_numeros_raw = {cmd.numero_commande for cmd in page_obj.object_list}

    cyrus_lookup = set()
    cyrus_numero_lookup = set()

    filtres_cyrus_lookup = {}
    if date_debut_parsed:
        filtres_cyrus_lookup['date_commande__gte'] = date_debut_parsed
    if date_fin_parsed:
        filtres_cyrus_lookup['date_commande__lte'] = date_fin_parsed

    for date_cmd, num in CommandeCyrus.objects.filter(
        **filtres_cyrus_lookup,
        numero_commande__in=page_numeros_raw,
    ).values_list('date_commande', 'numero_commande'):
        numero_normalise = normalize_numero(num)
        cyrus_lookup.add((date_cmd, numero_normalise))
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
        'per_page': per_page,
        'per_page_options': [30, 50, 100, 200],
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
    type_fichier = request.GET.get('type_fichier', '')
    statut = request.GET.get('statut', '')
    date_debut = request.GET.get('date_debut', '')
    date_fin = request.GET.get('date_fin', '')

    queryset = ImportFichier.objects.all()

    if type_fichier:
        queryset = queryset.filter(type_fichier=type_fichier)
    if statut:
        queryset = queryset.filter(statut=statut)
    if date_debut:
        try:
            from datetime import datetime
            queryset = queryset.filter(date_import__date__gte=datetime.strptime(date_debut, '%Y-%m-%d').date())
        except ValueError:
            pass
    if date_fin:
        try:
            from datetime import datetime
            queryset = queryset.filter(date_import__date__lte=datetime.strptime(date_fin, '%Y-%m-%d').date())
        except ValueError:
            pass
    
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
    for type_f in ['asten', 'cyrus', 'gpv', 'legend', 'br_asten', 'br_ic']:
        dernier = ImportFichier.objects.filter(type_fichier=type_f).first()
        if dernier:
            derniers_imports[type_f] = dernier
    
    context = {
        'page_obj': page_obj,
        'type_fichier': type_fichier,
        'statut': statut,
        'date_debut': date_debut,
        'date_fin': date_fin,
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
    from core.permissions import get_user_role, user_has_perm
    role = get_user_role(request.user)
    peut_configurer_systeme    = role == 'superadmin'
    peut_configurer_integration = role == 'superadmin' or user_has_perm(request.user, 'configurer_integration')

    if not peut_configurer_systeme and not peut_configurer_integration:
        messages.error(request, "Accès non autorisé.")
        return redirect('dashboard:dashboard')

    config_path = Path(settings.BASE_DIR) / 'config.env'

    CHAMPS = [
        ('MEDIA_ROOT',                  'Dossier média racine',             'folder'),
        ('DOSSIER_COMMANDES_ASTEN',     'Commandes Asten',                  'folder'),
        ('DOSSIER_COMMANDES_CYRUS',     'Commandes Cyrus',                  'folder'),
        ('DOSSIER_COMMANDES_GPV',       'Commandes GPV',                    'folder'),
        ('DOSSIER_COMMANDES_LEGEND',    'Commandes Legend',                 'folder'),
        ('DOSSIER_BR_ASTEN',            'Bons de réception Asten',          'folder'),
        ('DOSSIER_BR_IC',               'Bons de réception IC',             'folder'),
        ('DOSSIER_ANOMALIE_BR',         'Anomalies BR ASTEN/IC',            'folder'),
        ('DOSSIER_FACTURES_ASTEN',      'Factures Asten (CSV)',             'folder'),
        ('DOSSIER_FACTURES_ASTEN_BACKUP','Factures Asten (Backup SMB)',     'smb'),
        ('DOSSIER_FACTURES_SAGE',       'Factures Sage (Archive)',          'smb'),
        ('FACTURES_SAGE_PREFIX',        'Préfixe fichiers Sage',            'text'),
        ('DOSSIER_VERSIONS_ASTEN',      'Versions Asten (Backup)',          'folder'),
    ]

    # Lire le fichier config.env
    def read_config():
        vals = {}
        if config_path.exists():
            for line in config_path.read_text(encoding='utf-8').splitlines():
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    k, _, v = line.partition('=')
                    vals[k.strip()] = v.strip()
        return vals

    # Écrire une clé dans config.env
    def update_config(key, value):
        content = config_path.read_text(encoding='utf-8') if config_path.exists() else ''
        lines = content.splitlines()
        found = False
        new_lines = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(f'{key}=') or stripped.startswith(f'{key} ='):
                new_lines.append(f'{key}={value}')
                found = True
            else:
                new_lines.append(line)
        if not found:
            new_lines.append(f'{key}={value}')
        config_path.write_text('\n'.join(new_lines) + '\n', encoding='utf-8')

    from entree_journal.models import TypeFichierConfig, TYPES_CONNUS, ErreurIgnoreeConfig, PATTERNS_IGNORES_CONNUS

    if request.method == 'POST':
        action = request.POST.get('action', 'config')

        if action == 'types_fichiers':
            if not peut_configurer_integration:
                messages.error(request, "Permission insuffisante.")
                return redirect('dashboard:configuration_systeme')
            TypeFichierConfig.get_types_actifs()
            for key in TYPES_CONNUS:
                actif = request.POST.get(f'type_{key}') == '1'
                TypeFichierConfig.objects.filter(type_key=key).update(actif=actif)
            messages.success(request, "Configuration des types de fichiers enregistrée.")
            return redirect('dashboard:configuration_systeme')

        if action == 'erreurs_ignorees':
            if not peut_configurer_integration:
                messages.error(request, "Permission insuffisante.")
                return redirect('dashboard:configuration_systeme')
            ErreurIgnoreeConfig.get_patterns_ignores()
            for pattern in PATTERNS_IGNORES_CONNUS:
                comptabilise = request.POST.get(f'pattern_{pattern}') == '1'
                ErreurIgnoreeConfig.objects.filter(pattern=pattern).update(ignorer=not comptabilise)
            # Mettre à jour aussi les patterns découverts automatiquement
            for e in ErreurIgnoreeConfig.objects.exclude(pattern__in=list(PATTERNS_IGNORES_CONNUS.keys())):
                comptabilise = request.POST.get(f'pattern_{e.pattern}') == '1'
                e.ignorer = not comptabilise
                e.save()
            messages.success(request, "Configuration des erreurs enregistrée.")
            return redirect('dashboard:configuration_systeme')

        vals = read_config()
        changed = []
        for key, label, _ in CHAMPS:
            new_val = request.POST.get(key, '').strip()
            if new_val != vals.get(key, ''):
                update_config(key, new_val)
                # Recharger en mémoire immédiatement
                setattr(settings, key, new_val)
                # Recalculer les _PATH si besoin
                path_attr = key + '_PATH'
                if hasattr(settings, path_attr):
                    from verification_commande.settings import get_dossier_path
                    setattr(settings, path_attr, get_dossier_path(new_val))
                changed.append(label)
        if changed:
            messages.success(request, f"Enregistré : {', '.join(changed)}")
        else:
            messages.info(request, "Aucune modification détectée.")
        return redirect('dashboard:configuration_systeme')

    vals = read_config()
    def get_default(key):
        val = getattr(settings, key, None)
        if val is not None:
            return str(val)
        return ''
    champs_ctx = [{'key': k, 'label': l, 'type': t, 'value': vals.get(k) or get_default(k)} for k, l, t in CHAMPS]

    # Charger config types fichiers (initialise si besoin)
    TypeFichierConfig.get_types_actifs()
    types_fichiers_ctx = list(TypeFichierConfig.objects.order_by('type_key'))

    # Charger config erreurs ignorées + découvrir nouveaux patterns automatiquement
    ErreurIgnoreeConfig.get_patterns_ignores()
    ErreurIgnoreeConfig.decouvrir_nouveaux_patterns()
    erreurs_ignorees_ctx = list(ErreurIgnoreeConfig.objects.order_by('ignorer', 'pattern'))

    return render(request, 'dashboard/configuration_systeme.html', {
        'champs': champs_ctx,
        'types_fichiers': types_fichiers_ctx,
        'erreurs_ignorees': erreurs_ignorees_ctx,
        'peut_configurer_systeme':    peut_configurer_systeme,
        'peut_configurer_integration': peut_configurer_integration,
    })


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
            original_code = (request.POST.get('original_code') or '').strip()
            if not original_code or not nom:
                messages.error(request, "Le nom du magasin est obligatoire pour la modification.")
            else:
                try:
                    magasin = Magasin.objects.get(code=original_code)
                    magasin.nom = nom
                    magasin.full_asten = request.POST.get('full_asten') == '1'
                    magasin.save()
                    messages.success(request, f"Magasin {original_code} mis à jour avec succès.")
                    return redirect('dashboard:gestion_magasins')
                except Magasin.DoesNotExist:
                    messages.error(request, "Magasin introuvable pour la modification.")
        elif action == 'toggle_full_asten':
            # Cocher ou décocher tous les magasins d'un coup
            val = request.POST.get('full_asten_val') == '1'
            Magasin.objects.all().update(full_asten=val)
            label = "cochés" if val else "décochés"
            messages.success(request, f"Tous les magasins ont été {label} (Full Asten).")
            return redirect('dashboard:gestion_magasins')
        elif action == 'toggle_one':
            # Basculer Full Asten d'un seul magasin (appel AJAX)
            if code:
                try:
                    magasin = Magasin.objects.get(code=code)
                    magasin.full_asten = not magasin.full_asten
                    magasin.save()
                    from django.http import JsonResponse
                    return JsonResponse({'ok': True, 'full_asten': magasin.full_asten})
                except Magasin.DoesNotExist:
                    from django.http import JsonResponse
                    return JsonResponse({'ok': False}, status=404)
        else:
            # Création d'un nouveau magasin
            if not code or not nom:
                messages.error(request, "Le code et le nom du magasin sont obligatoires.")
            elif len(code) > 10:
                messages.error(request, "Le code du magasin ne doit pas dépasser 10 caractères.")
            else:
                try:
                    full_asten = request.POST.get('full_asten') == '1'
                    Magasin.objects.create(code=code, nom=nom, full_asten=full_asten)
                    messages.success(request, f"Magasin {code} - {nom} ajouté avec succès.")
                    return redirect('dashboard:gestion_magasins')
                except IntegrityError:
                    messages.error(request, f"Un magasin avec le code {code} existe déjà.")

    magasins = Magasin.objects.all().order_by('code')
    return render(
        request,
        'dashboard/gestion_magasins.html',
        {
            'magasins':      magasins,
            'magasin_edit':  magasin_edit,
            'nb_full_asten': magasins.filter(full_asten=True).count(),
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

    # Désactiver / réactiver
    if request.method == 'POST' and request.POST.get('action') == 'toggle_active' and role in ('admin', 'superadmin'):
        user_id = request.POST.get('user_id')
        try:
            u = User.objects.get(pk=user_id)
            if u == request.user:
                messages.error(request, "Impossible de modifier votre propre compte.")
            else:
                u.is_active = not u.is_active
                u.save()
                etat = "réactivé" if u.is_active else "désactivé"
                messages.success(request, f"Utilisateur '{u.username}' {etat}.")
        except User.DoesNotExist:
            messages.error(request, "Utilisateur introuvable.")
        return redirect('dashboard:gestion_utilisateurs')

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
            profile.must_change_password = True
            profile.save()
            # Mettre à jour le hash de session de l'admin pour éviter son invalidation
            from django.contrib.auth import update_session_auth_hash
            update_session_auth_hash(request, request.user)
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

        # Modifier les infos du compte
        if request.POST.get('action') == 'edit_info':
            new_username = request.POST.get('username', '').strip()
            new_first_name = request.POST.get('first_name', '').strip()
            new_last_name = request.POST.get('last_name', '').strip()
            new_email = request.POST.get('email', '').strip()
            if not new_username:
                messages.error(request, "Le nom d'utilisateur est obligatoire.")
            elif new_username != u.username and User.objects.filter(username=new_username).exists():
                messages.error(request, f"Le nom d'utilisateur '{new_username}' est déjà pris.")
            else:
                u.username = new_username
                u.first_name = new_first_name
                u.last_name = new_last_name
                u.email = new_email
                u.save()
                messages.success(request, f"Informations de '{new_username}' mises à jour.")
            return redirect('dashboard:permissions_utilisateur', user_id=user_id)

        # Réinitialisation mot de passe
        if request.POST.get('action') == 'reset_password':
            nouveau_mdp = request.POST.get('nouveau_mdp', '').strip()
            if len(nouveau_mdp) < 6:
                messages.error(request, "Le mot de passe doit contenir au moins 6 caractères.")
            else:
                u.set_password(nouveau_mdp)
                u.save()
                # Si l'admin change son propre mot de passe, garder sa session active
                from django.contrib.auth import update_session_auth_hash
                if u == request.user:
                    update_session_auth_hash(request, u)
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


def _section_to_source(section):
    """Convertit la section du rapport en source pour _get_top5_magasins."""
    return {
        'commandes': None,          # toutes les sources
        'br':        'br',
        'factures':  None,          # pas de top magasin pertinent
        'versions':  None,
        'remontees': 'remontees',   # top remontées par magasin
    }.get(section, None)


def rapport_global(request):
    """Page de rapports avec stats, récaps et exports CSV par période."""
    import json
    import csv
    from datetime import date, timedelta
    from django.http import HttpResponse
    from django.db.models import Count, Q
    from tickets.models import Ticket
    from imports.models import FactureBackupCyrus, FactureSage, FactureEcartStatut, VersionAstenSnap

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
    gpv_qs = CommandeGPV.objects.filter(date_creation__date__gte=date_debut, date_creation__date__lte=date_fin)
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

    # ---- FACTURES BACKUP CYRUS ----
    backup_qs = FactureBackupCyrus.objects.filter(date_modif__date__gte=date_debut, date_modif__date__lte=date_fin)
    backup_total = backup_qs.count()
    backup_generales = backup_qs.filter(type_facture='general').count()
    backup_promos = backup_qs.filter(type_facture='promo').count()
    backup_par_magasin = list(
        backup_qs.values('code_magasin')
        .annotate(total=Count('id')).order_by('-total')[:10]
    )

    # ---- FACTURES ÉCARTS (statuts) ----
    fes_agg = FactureEcartStatut.objects.aggregate(
        integre=Count('id', filter=Q(statut='integre')),
        non_integre=Count('id', filter=Q(statut='non_integre')),
        ignore=Count('id', filter=Q(statut='ignore')),
    )
    ecart_integre = fes_agg['integre']
    ecart_non_integre = fes_agg['non_integre']
    ecart_ignore = fes_agg['ignore']
    ecart_total = ecart_integre + ecart_non_integre + ecart_ignore

    # ---- VERSIONS ASTEN ----
    versions_qs = VersionAstenSnap.objects.filter(date__gte=date_debut, date__lte=date_fin)
    ver_agg = versions_qs.aggregate(
        total=Count('id'),
        ok=Count('id', filter=Q(statut='ok')),
        warning=Count('id', filter=Q(statut='warning')),
        error=Count('id', filter=Q(statut='error')),
    )
    versions_total = ver_agg['total']
    versions_ok = ver_agg['ok']
    versions_warning = ver_agg['warning']
    versions_error = ver_agg['error']
    versions_par_jour = list(
        versions_qs.values('date').annotate(total=Count('id')).order_by('date')
    )
    versions_recentes = list(versions_qs.order_by('-date', '-heure')[:10])

    # ---- REMONTÉES ----
    tickets_qs = Ticket.objects.filter(
        date_creation__date__gte=date_debut, date_creation__date__lte=date_fin
    )
    tkt_agg = tickets_qs.aggregate(
        total=Count('id'),
        ouverts=Count('id', filter=Q(statut__in=['nouveau', 'en_cours', 'en_attente'])),
        resolus=Count('id', filter=Q(statut__in=['resolu', 'ferme'])),
    )
    tickets_total = tkt_agg['total']
    tickets_ouverts = tkt_agg['ouverts']
    tickets_resolus = tkt_agg['resolus']

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
        # Factures Backup
        'backup_total': backup_total,
        'backup_generales': backup_generales,
        'backup_promos': backup_promos,
        'backup_par_magasin': backup_par_magasin,
        # Factures Écarts
        'ecart_total': ecart_total,
        'ecart_integre': ecart_integre,
        'ecart_non_integre': ecart_non_integre,
        'ecart_ignore': ecart_ignore,
        # Versions Asten
        'versions_total': versions_total,
        'versions_ok': versions_ok,
        'versions_warning': versions_warning,
        'versions_error': versions_error,
        'versions_recentes': versions_recentes,
        'chart_versions_json': json.dumps({'labels': [str(r['date']) for r in versions_par_jour], 'values': [r['total'] for r in versions_par_jour]}),
        'semaine_comparison': _get_semaine_comparison(request.GET.get('periode_cmp', 'semaine')),
        'periode_cmp': request.GET.get('periode_cmp', 'semaine'),
        'top5': _get_top5_magasins(debut=date_debut, fin=date_fin, n=request.GET.get('top_n', 5),
                                   source=_section_to_source(section)),
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
    type_data = shop['types'].get(type_key) or shop.get('types_extra', {}).get(type_key)
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
    from imports.models import FactureAstenLigne
    error = None

    # Filtres
    f_magasin    = request.GET.get('magasin', '').strip()
    f_date_debut = _factures_parse_date_filter(request.GET.get('date_debut', ''))
    f_date_fin   = _factures_parse_date_filter(request.GET.get('date_fin', ''))

    try:
        qs = FactureAstenLigne.objects.order_by('magasin', '-date_reception_date')
        magasins_list = sorted(FactureAstenLigne.objects.values_list('magasin', flat=True).distinct())

        if f_magasin:
            qs = qs.filter(magasin=f_magasin)
        if f_date_debut:
            qs = qs.filter(date_reception_date__gte=f_date_debut)
        if f_date_fin:
            qs = qs.filter(date_reception_date__lte=f_date_fin)

        total = qs.count()
        page_obj, paginator = _paginate(request, qs)
    except Exception as e:
        error = str(e)
        page_obj, paginator, total, magasins_list = None, None, 0, []

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
    from django.db import connection
    PER_PAGE = 200

    f_magasin    = request.GET.get('magasin', '').strip()
    f_nsee       = request.GET.get('nsee', '').strip()
    f_date_debut = request.GET.get('date_debut', '').strip()
    f_date_fin   = request.GET.get('date_fin', '').strip()
    try:
        page_num = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page_num = 1

    where  = ["statut_effectif != 'ignore'"]
    params = []
    if f_magasin:
        where.append("cidc = %s"); params.append(f_magasin)
    if f_nsee:
        where.append("nsee = %s"); params.append(f_nsee)
    if f_date_debut:
        where.append("dfac_date >= %s"); params.append(f_date_debut)
    if f_date_fin:
        where.append("dfac_date <= %s"); params.append(f_date_fin)

    where_sql = "WHERE " + " AND ".join(where) if where else ""

    try:
        with connection.cursor() as cur:
            # Listes de filtres (depuis la MV, pas de chargement complet)
            cur.execute("SELECT DISTINCT cidc FROM mv_factures_joined WHERE cidc IS NOT NULL ORDER BY cidc")
            magasins_list = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT DISTINCT nsee FROM mv_factures_joined WHERE nsee IS NOT NULL ORDER BY nsee")
            nsee_list = [r[0] for r in cur.fetchall()]

            # Total
            cur.execute(f"SELECT COUNT(*) FROM mv_factures_joined {where_sql}", params)
            total = cur.fetchone()[0]

            # Page courante uniquement
            offset = (page_num - 1) * PER_PAGE
            cur.execute(f"""
                SELECT cle_facture, nfac, nsee, dfac_str, dfac_date, cidc,
                       pfth_total, nb_articles, qt_asten, valo_ttc, integree,
                       statut_effectif, statut_manuel, ecart_valo, has_ecart_valo
                FROM mv_factures_joined
                {where_sql}
                ORDER BY cidc, dfac_date DESC
                LIMIT %s OFFSET %s
            """, params + [PER_PAGE, offset])
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        error = None
    except Exception as e:
        rows, total, magasins_list, nsee_list, error = [], 0, [], [], str(e)

    import math
    num_pages = max(1, math.ceil(total / PER_PAGE))

    return render(request, 'dashboard/factures_cyrus.html', {
        'rows':          rows,
        'page_obj':      rows,
        'total':         total,
        'page_num':      page_num,
        'num_pages':     num_pages,
        'has_prev':      page_num > 1,
        'has_next':      page_num < num_pages,
        'page_range':    range(max(1, page_num - 3), min(num_pages + 1, page_num + 4)),
        'error':         error,
        'magasins_list': magasins_list,
        'nsee_list':     nsee_list,
        'f_magasin':     f_magasin,
        'f_nsee':        f_nsee,
        'f_date_debut':  f_date_debut,
        'f_date_fin':    f_date_fin,
    })


def vue_factures_backup(request):
    from django.db import connection
    from core.models import Magasin as MagasinModel
    PER_PAGE = 100

    f_statut     = request.GET.get('statut', 'non_integree').strip()
    f_magasin    = request.GET.get('magasin', '').strip()
    f_nsee       = request.GET.get('nsee', '').strip()
    f_search     = request.GET.get('search', '').strip()
    f_date_debut = request.GET.get('date_debut', '').strip()
    f_date_fin   = request.GET.get('date_fin', '').strip()
    f_full_asten = request.GET.get('full_asten', '').strip()
    try:
        page_num = max(1, int(request.GET.get('page', 1)))
    except (ValueError, TypeError):
        page_num = 1

    full_asten_codes = list(
        MagasinModel.objects.filter(full_asten=True).values_list('code', flat=True)
    )

    where  = ["statut_effectif != 'ignore'"]
    params = []

    if f_full_asten == '1' and full_asten_codes:
        where.append(f"cidc = ANY(%s)"); params.append(full_asten_codes)
    if f_statut == 'integree':
        where.append("statut_effectif = 'integre'")
    elif f_statut == 'integree_vide':
        where.append("statut_effectif = 'integre_vide'")
    elif f_statut == 'non_integree':
        where.append("statut_effectif = 'non_integre'")
    elif f_statut == 'ecart_valo':
        where.append("has_ecart_valo = true")
    elif f_statut == 'ecart':
        where.append("integree = false")
    elif f_statut == 'manuel_integre':
        where.append("statut_manuel = 'integre'")
    if f_magasin:
        where.append("cidc = %s"); params.append(f_magasin)
    if f_nsee:
        where.append("nsee = %s"); params.append(f_nsee)
    if f_date_debut:
        where.append("dfac_date >= %s"); params.append(f_date_debut)
    if f_date_fin:
        where.append("dfac_date <= %s"); params.append(f_date_fin)
    if f_search:
        where.append("(UPPER(cle_facture) LIKE %s OR UPPER(nfac) LIKE %s)")
        s = f_search.upper()
        params += [f'%{s}%', f'%{s}%']

    where_sql = "WHERE " + " AND ".join(where)

    try:
        with connection.cursor() as cur:
            # Stats globales (toujours sans filtre statut/search)
            base_where = ["statut_effectif != 'ignore'"]
            base_params = []
            if f_full_asten == '1' and full_asten_codes:
                base_where.append("cidc = ANY(%s)"); base_params.append(full_asten_codes)
            base_sql = "WHERE " + " AND ".join(base_where)

            cur.execute(f"""
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN statut_effectif='integre'     THEN 1 ELSE 0 END) AS integrees,
                    SUM(CASE WHEN statut_effectif='integre_vide'THEN 1 ELSE 0 END) AS integrees_vide,
                    SUM(CASE WHEN statut_effectif='non_integre' THEN 1 ELSE 0 END) AS ecarts,
                    SUM(CASE WHEN has_ecart_valo               THEN 1 ELSE 0 END) AS ecarts_valo
                FROM mv_factures_joined {base_sql}
            """, base_params)
            r = cur.fetchone()
            stats = {
                'total': r[0] or 0, 'integrees': r[1] or 0,
                'integrees_vide': r[2] or 0, 'ecarts': r[3] or 0,
                'ecarts_valo': r[4] or 0,
            }

            # Listes filtres
            cur.execute("SELECT DISTINCT cidc FROM mv_factures_joined WHERE cidc IS NOT NULL ORDER BY cidc")
            magasins_list = [r[0] for r in cur.fetchall()]
            cur.execute("SELECT DISTINCT nsee FROM mv_factures_joined WHERE nsee IS NOT NULL ORDER BY nsee")
            nsee_list = [r[0] for r in cur.fetchall()]

            # Total avec filtres
            cur.execute(f"SELECT COUNT(*) FROM mv_factures_joined {where_sql}", params)
            total = cur.fetchone()[0]

            # Page courante seulement
            offset = (page_num - 1) * PER_PAGE
            cur.execute(f"""
                SELECT cle_facture, nfac, nsee, dfac_str, dfac_date, cidc,
                       pfth_total, nb_articles, qt_asten, valo_ttc, integree,
                       statut_effectif, statut_manuel, ecart_valo, has_ecart_valo
                FROM mv_factures_joined
                {where_sql}
                ORDER BY cidc, dfac_date DESC
                LIMIT %s OFFSET %s
            """, params + [PER_PAGE, offset])
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]

        error = None
    except Exception as e:
        rows, total, magasins_list, nsee_list, stats, error = [], 0, [], [], {}, str(e)

    import math
    num_pages = max(1, math.ceil(total / PER_PAGE)) if total else 1

    return render(request, 'dashboard/factures_backup.html', {
        'rows':            rows,
        'page_obj':        rows,
        'stats':           stats,
        'error':           error,
        'magasins_list':   magasins_list,
        'nsee_list':       nsee_list,
        'f_statut':        f_statut,
        'f_magasin':       f_magasin,
        'f_nsee':          f_nsee,
        'f_search':        f_search,
        'f_date_debut':    f_date_debut,
        'f_date_fin':      f_date_fin,
        'f_full_asten':    f_full_asten,
        'full_asten_codes': set(full_asten_codes),
        'total':           total,
        'page_num':        page_num,
        'num_pages':       num_pages,
        'has_prev':        page_num > 1,
        'has_next':        page_num < num_pages,
        'page_range':      range(max(1, page_num - 3), min(num_pages + 1, page_num + 4)),
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


def api_last_import(request):
    """Retourne le timestamp du dernier import réussi — utilisé pour le polling auto-refresh."""
    from imports.models import ImportFichier
    last = ImportFichier.objects.filter(statut='termine').order_by('-date_import').values('date_import').first()
    ts = last['date_import'].isoformat() if last else None
    return JsonResponse({'last_import': ts})


def activite_live(request):
    """Page journal d'activité : copie SMB + imports en base."""
    return render(request, 'dashboard/activite_live.html')


def api_activite(request):
    """API JSON — dernières activités pour le polling du journal live."""
    import os
    from imports.models import ImportFichier
    from django.utils import timezone as _tz

    # ── Derniers imports en base (50 max) ──────────────────────────────────
    imports = list(
        ImportFichier.objects
        .order_by('-date_import')
        .values('id', 'type_fichier', 'nom_fichier', 'statut',
                'nombre_lignes', 'nombre_nouveaux', 'message_erreur', 'date_import')
        [:50]
    )
    for imp in imports:
        imp['date_import'] = imp['date_import'].isoformat()

    # ── Dernières lignes du log run_auto ───────────────────────────────────
    log_path = os.path.join(settings.BASE_DIR, 'logs', 'run_auto.log')
    log_lines = []
    if os.path.exists(log_path):
        try:
            with open(log_path, 'r', errors='replace') as f:
                lines = f.readlines()
            log_lines = [l.rstrip() for l in lines[-100:]]
        except Exception:
            pass

    # ── Statut run_auto (en cours ou arrêté) ──────────────────────────────
    import subprocess
    try:
        out = subprocess.check_output(['pgrep', '-f', 'run_auto.py'], text=True)
        run_auto_actif = bool(out.strip())
    except subprocess.CalledProcessError:
        run_auto_actif = False

    with _import_lock:
        import_bg = dict(_import_state)

    return JsonResponse({
        'imports':        imports,
        'log_lines':      log_lines,
        'run_auto_actif': run_auto_actif,
        'import_bg':      import_bg,
        'now':            _tz.now().isoformat(),
    })


def api_import_status(request):
    """Retourne l'état courant de l'import asynchrone (pour polling JS)."""
    with _import_lock:
        state = dict(_import_state)
    return JsonResponse(state)


def changer_mot_de_passe(request):
    """Vue forcée au premier login si must_change_password=True."""
    from django.contrib.auth import update_session_auth_hash

    if request.method == 'POST':
        pwd1 = request.POST.get('password1', '').strip()
        pwd2 = request.POST.get('password2', '').strip()
        if not pwd1 or len(pwd1) < 6:
            messages.error(request, "Le mot de passe doit contenir au moins 6 caractères.")
        elif pwd1 != pwd2:
            messages.error(request, "Les mots de passe ne correspondent pas.")
        else:
            request.user.set_password(pwd1)
            request.user.save()
            request.user.profile.must_change_password = False
            request.user.profile.save()
            update_session_auth_hash(request, request.user)
            messages.success(request, "Mot de passe mis à jour avec succès.")
            return redirect('/')
    return render(request, 'dashboard/changer_mot_de_passe.html')


def configuration_chemins(request):
    return configuration_systeme(request)


@require_http_methods(["GET", "POST"])
def assistant_ia(request):
    """Assistant IA Text-to-SQL avec Gemini — accès restreint par permission."""
    import uuid, datetime
    from core.permissions import user_has_perm
    if not user_has_perm(request.user, 'assistant_ia'):
        from django.contrib import messages as _msg
        _msg.error(request, "Vous n'avez pas accès à l'Assistant IA.")
        return redirect('dashboard:accueil')

    CHATS_KEY   = 'ai_chats'       # liste de toutes les sessions [{id, title, turns}]
    CURRENT_KEY = 'ai_current_chat' # id de la session active

    def _get_chats():
        return request.session.get(CHATS_KEY, [])

    def _save_chats(chats):
        request.session[CHATS_KEY] = chats
        request.session.modified = True

    action = request.GET.get('action') or request.POST.get('action', '')

    # ── Nouveau chat ──────────────────────────────────────────────────
    if action == 'new':
        new_id = str(uuid.uuid4())[:8]
        chats = _get_chats()
        chats.insert(0, {
            'id': new_id,
            'title': 'Nouvelle conversation',
            'created': datetime.datetime.now().strftime('%d/%m %H:%M'),
            'turns': []
        })
        _save_chats(chats)
        request.session[CURRENT_KEY] = new_id
        return redirect('dashboard:assistant_ia')

    # ── Supprimer un chat ─────────────────────────────────────────────
    if action == 'delete':
        del_id = request.GET.get('id', '')
        chats = [c for c in _get_chats() if c['id'] != del_id]
        _save_chats(chats)
        if request.session.get(CURRENT_KEY) == del_id:
            request.session[CURRENT_KEY] = chats[0]['id'] if chats else None
        return redirect('dashboard:assistant_ia')

    # ── Changer de chat ───────────────────────────────────────────────
    if action == 'switch':
        request.session[CURRENT_KEY] = request.GET.get('id', '')
        request.session.modified = True
        return redirect('dashboard:assistant_ia')

    # ── Récupérer ou créer le chat courant ────────────────────────────
    chats = _get_chats()
    current_id = request.session.get(CURRENT_KEY)

    if not chats or not current_id or not any(c['id'] == current_id for c in chats):
        new_id = str(uuid.uuid4())[:8]
        chats = [{'id': new_id, 'title': 'Nouvelle conversation',
                  'created': datetime.datetime.now().strftime('%d/%m %H:%M'), 'turns': []}]
        _save_chats(chats)
        current_id = new_id
        request.session[CURRENT_KEY] = current_id

    current_chat = next(c for c in chats if c['id'] == current_id)

    # ── POST : question ───────────────────────────────────────────────
    if request.method == 'POST':
        question = request.POST.get('question', '').strip()
        if question:
            from dashboard.ai_service import query_with_gemini
            result = query_with_gemini(question)
            turns = current_chat.get('turns', [])
            turns.append({'question': question, 'result': result})
            if len(turns) > 20:
                turns = turns[-20:]
            current_chat['turns'] = turns
            # Titre = première question tronquée
            if len(turns) == 1:
                current_chat['title'] = question[:48] + ('…' if len(question) > 48 else '')
            _save_chats(chats)

    return render(request, 'dashboard/assistant_ia.html', {
        'chats': chats,
        'current_chat': current_chat,
        'conversation': current_chat.get('turns', []),
    })
