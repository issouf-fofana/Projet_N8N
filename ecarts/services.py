from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from asten.models import CommandeAsten
from cyrus.models import CommandeCyrus
from gpv.models import CommandeGPV
from legend.models import CommandeLegend
from ecarts.models import EcartCommande, EcartGPV, EcartLegend


def recalculer_ecarts():
    """
    Recalcule tous les écarts entre Asten/GPV/Legend et Cyrus.
    Version optimisée : chargement bulk en mémoire, pas de requête par ligne.
    """
    def normalize_numero(numero):
        if not numero:
            return ''
        digits = ''.join(ch for ch in str(numero).strip() if ch.isdigit())
        return digits.lstrip('0') or '0' if digits else str(numero).strip()

    with transaction.atomic():

        # ── 1. ÉCARTS ASTEN ──────────────────────────────────────────────────
        # Construire un set de (numero_commande, code_magasin) présents dans Cyrus
        cyrus_keys = set(
            CommandeCyrus.objects.values_list('numero_commande', 'code_magasin')
        )

        ecarts_crees = 0
        ecarts_resolus = 0

        # Charger tous les écarts existants en une requête
        ecarts_existants = {
            e.commande_asten_id: e
            for e in EcartCommande.objects.select_related('commande_asten').all()
        }

        a_creer = []
        a_supprimer = []

        for commande_asten in CommandeAsten.objects.all():
            key = (commande_asten.numero_commande, commande_asten.code_magasin_id)
            existe_cyrus = key in cyrus_keys

            ecart = ecarts_existants.get(commande_asten.pk)
            if ecart:
                if existe_cyrus and ecart.statut == 'ouvert':
                    a_supprimer.append(ecart.pk)
                    ecarts_resolus += 1
            else:
                if not existe_cyrus:
                    a_creer.append(EcartCommande(commande_asten=commande_asten, statut='ouvert'))
                    ecarts_crees += 1

        if a_supprimer:
            EcartCommande.objects.filter(pk__in=a_supprimer).delete()
        if a_creer:
            EcartCommande.objects.bulk_create(a_creer, ignore_conflicts=True)

        # ── 2. ÉCARTS GPV ────────────────────────────────────────────────────
        ecarts_gpv_crees = 0
        ecarts_gpv_resolus = 0

        ecarts_gpv_existants = {
            e.commande_gpv_id: e
            for e in EcartGPV.objects.all()
        }

        a_creer_gpv = []
        a_supprimer_gpv = []
        a_supprimer_gpv_non_transmis = []

        for commande_gpv in CommandeGPV.objects.all():
            statut_gpv = (commande_gpv.statut or '').strip().upper()
            doit_etre_dans_cyrus = statut_gpv in ('TRANSMISE', 'TRANSMIS')

            ecart = ecarts_gpv_existants.get(commande_gpv.pk)

            if not doit_etre_dans_cyrus:
                if ecart and ecart.statut != 'ignore':
                    a_supprimer_gpv_non_transmis.append(ecart.pk)
                continue

            key = (commande_gpv.numero_commande, commande_gpv.code_magasin_id)
            existe_cyrus = key in cyrus_keys

            if ecart:
                if existe_cyrus and ecart.statut == 'ouvert':
                    a_supprimer_gpv.append(ecart.pk)
                    ecarts_gpv_resolus += 1
            else:
                if not existe_cyrus:
                    a_creer_gpv.append(EcartGPV(commande_gpv=commande_gpv, statut='ouvert'))
                    ecarts_gpv_crees += 1

        if a_supprimer_gpv_non_transmis:
            EcartGPV.objects.filter(pk__in=a_supprimer_gpv_non_transmis).delete()
        if a_supprimer_gpv:
            EcartGPV.objects.filter(pk__in=a_supprimer_gpv).delete()
        if a_creer_gpv:
            EcartGPV.objects.bulk_create(a_creer_gpv, ignore_conflicts=True)

        # ── 3. ÉCARTS LEGEND ─────────────────────────────────────────────────
        ecarts_legend_crees = 0
        ecarts_legend_resolus = 0

        # Construire un set de numéros normalisés présents dans Cyrus
        cyrus_numeros_norm = set(
            normalize_numero(n)
            for n in CommandeCyrus.objects.values_list('numero_commande', flat=True)
        )

        ecarts_legend_existants = {
            e.commande_legend_id: e
            for e in EcartLegend.objects.all()
        }

        a_creer_legend = []
        a_supprimer_legend_non_exportees = []
        a_supprimer_legend_resolus = []
        a_maj_legend = []

        for commande_legend in CommandeLegend.objects.all():
            ecart = ecarts_legend_existants.get(commande_legend.pk)

            if not commande_legend.exportee:
                if ecart and ecart.statut != 'ignore':
                    a_supprimer_legend_non_exportees.append(ecart.pk)
                continue

            num_norm = normalize_numero(commande_legend.numero_commande)
            cyrus_existe = num_norm in cyrus_numeros_norm
            type_ecart = None if cyrus_existe else 'cyrus_absent'

            if type_ecart is None:
                if ecart and ecart.statut == 'ouvert':
                    a_supprimer_legend_resolus.append(ecart.pk)
                    ecarts_legend_resolus += 1
            else:
                if ecart:
                    if ecart.statut not in ('ignore', 'resolu'):
                        ecart.type_ecart = type_ecart
                        a_maj_legend.append(ecart)
                else:
                    a_creer_legend.append(EcartLegend(
                        commande_legend=commande_legend,
                        statut='ouvert',
                        type_ecart=type_ecart
                    ))
                    ecarts_legend_crees += 1

        if a_supprimer_legend_non_exportees:
            EcartLegend.objects.filter(pk__in=a_supprimer_legend_non_exportees).delete()
        if a_supprimer_legend_resolus:
            EcartLegend.objects.filter(pk__in=a_supprimer_legend_resolus).delete()
        if a_creer_legend:
            EcartLegend.objects.bulk_create(a_creer_legend, ignore_conflicts=True)
        if a_maj_legend:
            EcartLegend.objects.bulk_update(a_maj_legend, ['type_ecart'])

        return {
            'ecarts_crees': ecarts_crees + ecarts_gpv_crees + ecarts_legend_crees,
            'ecarts_resolus': ecarts_resolus + ecarts_gpv_resolus + ecarts_legend_resolus
        }


def get_statistiques(date_debut=None, date_fin=None, code_magasin=None):
    """
    Retourne les statistiques de rapprochement
    
    Returns:
        dict avec les statistiques
    """
    # Filtres de base
    filtres_asten = {}
    filtres_cyrus = {}
    
    if date_debut:
        filtres_asten['date_commande__gte'] = date_debut
        filtres_cyrus['date_commande__gte'] = date_debut
    
    if date_fin:
        filtres_asten['date_commande__lte'] = date_fin
        filtres_cyrus['date_commande__lte'] = date_fin
    
    if code_magasin:
        filtres_asten['code_magasin__code'] = code_magasin
        filtres_cyrus['code_magasin__code'] = code_magasin
    
    # Compter les commandes
    total_asten = CommandeAsten.objects.filter(**filtres_asten).count()
    total_cyrus = CommandeCyrus.objects.filter(**filtres_cyrus).count()
    
    # Compter les commandes intégrées (présentes dans les deux)
    commandes_asten = CommandeAsten.objects.filter(**filtres_asten)
    commandes_integres = 0
    
    for cmd_asten in commandes_asten:
        if CommandeCyrus.objects.filter(
            date_commande=cmd_asten.date_commande,
            numero_commande=cmd_asten.numero_commande,
            code_magasin=cmd_asten.code_magasin
        ).exists():
            commandes_integres += 1
    
    # Compter les écarts
    filtres_ecarts = {}
    if date_debut:
        filtres_ecarts['commande_asten__date_commande__gte'] = date_debut
    if date_fin:
        filtres_ecarts['commande_asten__date_commande__lte'] = date_fin
    if code_magasin:
        filtres_ecarts['commande_asten__code_magasin__code'] = code_magasin
    
    total_ecarts = EcartCommande.objects.filter(**filtres_ecarts).count()
    
    # Calculer les taux
    taux_integration = round((commandes_integres / total_asten * 100) if total_asten > 0 else 0, 2)
    taux_non_integration = round((total_ecarts / total_asten * 100) if total_asten > 0 else 0, 2)
    
    return {
        'total_asten': total_asten,
        'total_cyrus': total_cyrus,
        'commandes_integres': commandes_integres,
        'commandes_non_integres': total_ecarts,
        'taux_integration': taux_integration,
        'taux_non_integration': taux_non_integration,
    }

