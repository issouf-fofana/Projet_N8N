from django.db import transaction
from django.db.models import Exists, OuterRef, Q
from asten.models import CommandeAsten
from cyrus.models import CommandeCyrus
from gpv.models import CommandeGPV
from legend.models import CommandeLegend
from ecarts.models import EcartCommande, EcartGPV, EcartLegend


def recalculer_ecarts():
    """
    Recalcule tous les écarts entre Asten et Cyrus.
    Un écart = commande Asten absente dans Cyrus
    
    Logique des statuts :
    - "ouvert" : Écart détecté, commande Asten absente dans Cyrus
    - "resolu" : La commande est maintenant présente dans Cyrus (automatique lors du recalcul)
    - "ignore" : Écart ignoré manuellement par l'utilisateur (conservé même si la commande apparaît)
    """
    with transaction.atomic():
        # Récupérer toutes les commandes Asten
        commandes_asten = CommandeAsten.objects.all()
        
        ecarts_crees = 0
        ecarts_resolus = 0
        
        for commande_asten in commandes_asten:
            # Vérifier si la commande existe dans Cyrus
            # Fallback: si la date diffère, considérer intégrée si numéro+magasin existe
            existe_cyrus = CommandeCyrus.objects.filter(
                Q(date_commande=commande_asten.date_commande) |
                Q(numero_commande=commande_asten.numero_commande, code_magasin=commande_asten.code_magasin),
                numero_commande=commande_asten.numero_commande,
                code_magasin=commande_asten.code_magasin
            ).exists()
            
            # Vérifier si un écart existe déjà pour cette commande
            try:
                ecart_existant = commande_asten.ecart
                
                # Si la commande existe maintenant dans Cyrus
                if existe_cyrus:
                    # Si l'écart était "ouvert", le supprimer complètement (résolu automatiquement)
                    if ecart_existant.statut == 'ouvert':
                        ecart_existant.delete()
                        ecarts_resolus += 1
                    # Si l'écart était "ignore", on le garde tel quel (ignoré manuellement)
                    # Si l'écart était déjà "resolu", on ne fait rien (ne devrait plus arriver)
                else:
                    # Si la commande n'existe toujours pas dans Cyrus
                    # Ne pas réouvrir un écart résolu manuellement
                    # Si l'écart était "ignore" ou "resolu", on le garde tel quel
                    pass
                    
            except EcartCommande.DoesNotExist:
                # Aucun écart existant, créer un nouveau si la commande n'existe pas dans Cyrus
                if not existe_cyrus:
                    EcartCommande.objects.create(
                        commande_asten=commande_asten,
                        statut='ouvert'
                    )
                    ecarts_crees += 1
                # Si la commande existe dans Cyrus, pas besoin de créer un écart
        
        # Recalculer aussi les écarts GPV
        # IMPORTANT: Seules les commandes GPV avec statut "Transmise" doivent être dans Cyrus
        commandes_gpv = CommandeGPV.objects.all()
        ecarts_gpv_crees = 0
        ecarts_gpv_resolus = 0
        
        for commande_gpv in commandes_gpv:
            # Normaliser le statut (enlever les espaces, mettre en majuscules)
            statut_gpv = (commande_gpv.statut or '').strip().upper()
            
            # Seules les commandes "TRANSMISE" doivent être dans Cyrus
            # Les statuts "SAISIE" et "VALIDEE" ne doivent pas créer d'écart
            doit_etre_dans_cyrus = (statut_gpv == 'TRANSMISE' or statut_gpv == 'TRANSMIS')
            
            # Si le statut n'est pas "Transmise", on ne crée pas d'écart
            if not doit_etre_dans_cyrus:
                # Supprimer l'écart existant s'il y en a un (car ce n'est plus un écart valide)
                try:
                    ecart_existant = commande_gpv.ecart
                    # Si l'écart était "ignore", on le garde
                    if ecart_existant.statut != 'ignore':
                        ecart_existant.delete()
                except EcartGPV.DoesNotExist:
                    pass
                continue
            
            # Vérifier si la commande existe dans Cyrus (seulement pour les commandes "Transmise")
            # Fallback: si la date diffère, considérer intégrée si numéro+magasin existe
            existe_cyrus = CommandeCyrus.objects.filter(
                Q(date_commande=commande_gpv.date_creation) |
                Q(numero_commande=commande_gpv.numero_commande, code_magasin=commande_gpv.code_magasin),
                numero_commande=commande_gpv.numero_commande,
                code_magasin=commande_gpv.code_magasin
            ).exists()
            
            # Vérifier si un écart existe déjà pour cette commande
            try:
                ecart_existant = commande_gpv.ecart
                
                # Si la commande existe maintenant dans Cyrus
                if existe_cyrus:
                    # Si l'écart était "ouvert", le supprimer complètement (résolu automatiquement)
                    if ecart_existant.statut == 'ouvert':
                        ecart_existant.delete()
                        ecarts_gpv_resolus += 1
                    # Si l'écart était "ignore", on le garde tel quel (ignoré manuellement)
                else:
                    # Si la commande n'existe toujours pas dans Cyrus
                    # Ne pas réouvrir un écart résolu manuellement
                    pass
                    
            except EcartGPV.DoesNotExist:
                # Aucun écart existant, créer un nouveau si la commande n'existe pas dans Cyrus
                # (et seulement si le statut est "Transmise")
                if not existe_cyrus:
                    EcartGPV.objects.create(
                        commande_gpv=commande_gpv,
                        statut='ouvert'
                    )
                    ecarts_gpv_crees += 1
                # Si la commande existe dans Cyrus, pas besoin de créer un écart
        
        # Recalculer les écarts Legend (Legend -> Cyrus uniquement)
        ecarts_legend_crees = 0
        ecarts_legend_resolus = 0

        commandes_legend = CommandeLegend.objects.all()
        for commande_legend in commandes_legend:
            # Les commandes non exportées sont ignorées
            if not commande_legend.exportee:
                try:
                    ecart_existant = commande_legend.ecart
                    if ecart_existant.statut != 'ignore':
                        ecart_existant.delete()
                except EcartLegend.DoesNotExist:
                    pass
                continue

            cyrus_existe = CommandeCyrus.objects.filter(
                date_commande=commande_legend.date_commande,
                numero_commande=commande_legend.numero_commande
            ).exists()
            if not cyrus_existe:
                # Fallback: présence dans Cyrus sur une autre date
                cyrus_existe = CommandeCyrus.objects.filter(
                    numero_commande=commande_legend.numero_commande
                ).exists()

            # Déterminer le type d'écart selon la règle consolidée
            type_ecart = None
            if not cyrus_existe:
                type_ecart = 'cyrus_absent'

            if type_ecart is None:
                # Pas d'écart : supprimer l'écart existant si nécessaire
                try:
                    ecart_existant = commande_legend.ecart
                    if ecart_existant.statut == 'ouvert':
                        ecart_existant.delete()
                        ecarts_legend_resolus += 1
                except EcartLegend.DoesNotExist:
                    pass
            else:
                # Écart détecté ou réouvert
                try:
                    ecart_existant = commande_legend.ecart
                    # Ne pas réouvrir un écart résolu manuellement
                    if ecart_existant.statut != 'ignore' and ecart_existant.statut != 'resolu':
                        ecart_existant.type_ecart = type_ecart
                        ecart_existant.save()
                except EcartLegend.DoesNotExist:
                    EcartLegend.objects.create(
                        commande_legend=commande_legend,
                        statut='ouvert',
                        type_ecart=type_ecart
                    )
                    ecarts_legend_crees += 1

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

