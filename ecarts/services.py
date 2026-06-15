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
    Utilise .iterator() pour ne jamais charger toutes les lignes en RAM.
    Seuls les sets d'IDs et les dicts d'écarts existants sont en mémoire.
    """
    def normalize_numero(numero):
        if not numero:
            return ''
        digits = ''.join(ch for ch in str(numero).strip() if ch.isdigit())
        return digits.lstrip('0') or '0' if digits else str(numero).strip()

    with transaction.atomic():

        # ── 1. ÉCARTS ASTEN ──────────────────────────────────────────────────
        # Set de (numero_commande, code_magasin_id) présents dans Cyrus pour la source Asten
        # (la table Cyrus contient aussi les commandes GPV, distinguées par type_commande='AST'/'GPV')
        cyrus_keys = set(
            CommandeCyrus.objects.filter(type_commande='AST').values_list('numero_commande', 'code_magasin')
        )

        ecarts_crees = 0
        ecarts_resolus = 0

        # Dict id → statut uniquement (pas d'objets complets)
        ecarts_existants = {
            e['commande_asten_id']: e['statut']
            for e in EcartCommande.objects.values('commande_asten_id', 'statut', 'id')
        }
        ecarts_id_par_asten = {
            e['commande_asten_id']: e['id']
            for e in EcartCommande.objects.values('commande_asten_id', 'id')
        }

        a_creer = []
        a_supprimer = []

        # iterator() : lit ligne par ligne depuis la DB, rien en RAM sauf la ligne courante
        for asten_id, num_cmd, mag_id in CommandeAsten.objects.values_list(
            'id', 'numero_commande', 'code_magasin'
        ).iterator(chunk_size=2000):
            key = (num_cmd, mag_id)
            existe_cyrus = key in cyrus_keys
            statut = ecarts_existants.get(asten_id)

            if statut is not None:
                # resolu/ignore = modifié manuellement, on ne touche pas
                if existe_cyrus and statut == 'ouvert':
                    a_supprimer.append(ecarts_id_par_asten[asten_id])
                    ecarts_resolus += 1
            else:
                if not existe_cyrus:
                    a_creer.append(EcartCommande(commande_asten_id=asten_id, statut='ouvert'))
                    ecarts_crees += 1

        if a_supprimer:
            EcartCommande.objects.filter(pk__in=a_supprimer).delete()
        if a_creer:
            EcartCommande.objects.bulk_create(a_creer, ignore_conflicts=True)

        # ── 2. ÉCARTS GPV ────────────────────────────────────────────────────
        # Set Cyrus dédié GPV (type_commande='GPV'), distinct du set Asten ci-dessus
        cyrus_keys_gpv = set(
            CommandeCyrus.objects.filter(type_commande='GPV').values_list('numero_commande', 'code_magasin')
        )

        ecarts_gpv_crees = 0
        ecarts_gpv_resolus = 0

        ecarts_gpv_existants = {
            e['commande_gpv_id']: e['statut']
            for e in EcartGPV.objects.values('commande_gpv_id', 'statut', 'id')
        }
        ecarts_gpv_id = {
            e['commande_gpv_id']: e['id']
            for e in EcartGPV.objects.values('commande_gpv_id', 'id')
        }

        a_creer_gpv = []
        a_supprimer_gpv = []
        a_supprimer_gpv_non_transmis = []

        for gpv_id, num_cmd, mag_id, statut_gpv in CommandeGPV.objects.values_list(
            'id', 'numero_commande', 'code_magasin', 'statut'
        ).iterator(chunk_size=2000):
            statut_upper = (statut_gpv or '').strip().upper()
            doit_etre_dans_cyrus = statut_upper in ('TRANSMISE', 'TRANSMIS')
            statut_ecart = ecarts_gpv_existants.get(gpv_id)

            if not doit_etre_dans_cyrus:
                if statut_ecart is not None and statut_ecart != 'ignore':
                    a_supprimer_gpv_non_transmis.append(ecarts_gpv_id[gpv_id])
                continue

            key = (num_cmd, mag_id)
            existe_cyrus = key in cyrus_keys_gpv

            if statut_ecart is not None:
                # resolu/ignore = modifié manuellement, on ne touche pas
                if existe_cyrus and statut_ecart == 'ouvert':
                    a_supprimer_gpv.append(ecarts_gpv_id[gpv_id])
                    ecarts_gpv_resolus += 1
            else:
                if not existe_cyrus:
                    a_creer_gpv.append(EcartGPV(commande_gpv_id=gpv_id, statut='ouvert'))
                    ecarts_gpv_crees += 1

        if a_supprimer_gpv_non_transmis:
            EcartGPV.objects.filter(pk__in=a_supprimer_gpv_non_transmis).delete()
        if a_supprimer_gpv:
            EcartGPV.objects.filter(pk__in=a_supprimer_gpv).delete()
        if a_creer_gpv:
            EcartGPV.objects.bulk_create(a_creer_gpv, ignore_conflicts=True)

        # ── 3. ÉCARTS LEGEND ─────────────────────────────────────────────────
        # Flux : Legend (exportée) → GPV → Cyrus
        # Un écart Legend = commande exportée absente dans GPV (pas dans Cyrus directement)
        ecarts_legend_crees = 0
        ecarts_legend_resolus = 0

        # Set des numéros normalisés présents dans GPV
        gpv_numeros_norm = set(
            normalize_numero(n)
            for n in CommandeGPV.objects.values_list('numero_commande', flat=True).iterator(chunk_size=2000)
        )

        ecarts_legend_existants = {
            e['commande_legend_id']: (e['id'], e['statut'], e['type_ecart'])
            for e in EcartLegend.objects.values('commande_legend_id', 'id', 'statut', 'type_ecart')
        }

        a_creer_legend = []
        a_supprimer_legend_non_exportees = []
        a_supprimer_legend_resolus = []
        a_maj_legend = []

        for leg_id, num_cmd, exportee in CommandeLegend.objects.values_list(
            'id', 'numero_commande', 'exportee'
        ).iterator(chunk_size=2000):
            ecart_info = ecarts_legend_existants.get(leg_id)

            if not exportee:
                # Non exportée → pas d'écart attendu
                if ecart_info and ecart_info[1] != 'ignore':
                    a_supprimer_legend_non_exportees.append(ecart_info[0])
                continue

            num_norm = normalize_numero(num_cmd)
            gpv_existe = num_norm in gpv_numeros_norm
            type_ecart = None if gpv_existe else 'gpv_absent'

            if type_ecart is None:
                # Trouvée dans GPV → écart résolu
                if ecart_info and ecart_info[1] == 'ouvert':
                    a_supprimer_legend_resolus.append(ecart_info[0])
                    ecarts_legend_resolus += 1
            else:
                # Absente dans GPV → écart
                if ecart_info:
                    # resolu/ignore = modifié manuellement, on ne touche pas
                    if ecart_info[1] == 'ouvert':
                        a_maj_legend.append({'id': ecart_info[0], 'type_ecart': type_ecart})
                else:
                    a_creer_legend.append(EcartLegend(
                        commande_legend_id=leg_id,
                        statut='ouvert',
                        type_ecart=type_ecart,
                    ))
                    ecarts_legend_crees += 1

        if a_supprimer_legend_non_exportees:
            EcartLegend.objects.filter(pk__in=a_supprimer_legend_non_exportees).delete()
        if a_supprimer_legend_resolus:
            EcartLegend.objects.filter(pk__in=a_supprimer_legend_resolus).delete()
        if a_creer_legend:
            EcartLegend.objects.bulk_create(a_creer_legend, ignore_conflicts=True)
        if a_maj_legend:
            for item in a_maj_legend:
                update_fields = {'type_ecart': item['type_ecart']}
                if 'statut' in item:
                    update_fields['statut'] = item['statut']
                EcartLegend.objects.filter(pk=item['id']).update(**update_fields)

        return {
            'ecarts_crees': ecarts_crees + ecarts_gpv_crees + ecarts_legend_crees,
            'ecarts_resolus': ecarts_resolus + ecarts_gpv_resolus + ecarts_legend_resolus,
        }


def get_statistiques(date_debut=None, date_fin=None, code_magasin=None):
    """
    Retourne les statistiques de rapprochement — version SQL, sans N+1.
    """
    from django.db.models import Count, Q

    filtres_asten = {}
    filtres_cyrus = {}
    filtres_ecarts = {}

    if date_debut:
        filtres_asten['date_commande__gte'] = date_debut
        filtres_cyrus['date_commande__gte'] = date_debut
        filtres_ecarts['commande_asten__date_commande__gte'] = date_debut
    if date_fin:
        filtres_asten['date_commande__lte'] = date_fin
        filtres_cyrus['date_commande__lte'] = date_fin
        filtres_ecarts['commande_asten__date_commande__lte'] = date_fin
    if code_magasin:
        filtres_asten['code_magasin__code'] = code_magasin
        filtres_cyrus['code_magasin__code'] = code_magasin
        filtres_ecarts['commande_asten__code_magasin__code'] = code_magasin

    total_asten = CommandeAsten.objects.filter(**filtres_asten).count()
    total_cyrus = CommandeCyrus.objects.filter(**filtres_cyrus).count()

    # Commandes intégrées = Asten sans écart ouvert (proxy fiable depuis EcartCommande)
    total_ecarts = EcartCommande.objects.filter(**filtres_ecarts).filter(statut='ouvert').count()
    commandes_integres = total_asten - total_ecarts

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

