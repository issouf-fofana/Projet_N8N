from django.db import transaction
from asten.models import CommandeAsten
from cyrus.models import CommandeCyrus
from ecarts.models import EcartCommande


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
            existe_cyrus = CommandeCyrus.objects.filter(
                date_commande=commande_asten.date_commande,
                numero_commande=commande_asten.numero_commande,
                code_magasin=commande_asten.code_magasin
            ).exists()
            
            # Vérifier si un écart existe déjà pour cette commande
            try:
                ecart_existant = commande_asten.ecart
                
                # Si la commande existe maintenant dans Cyrus
                if existe_cyrus:
                    # Si l'écart était "ouvert", le marquer comme "résolu"
                    if ecart_existant.statut == 'ouvert':
                        ecart_existant.statut = 'resolu'
                        ecart_existant.save()
                        ecarts_resolus += 1
                    # Si l'écart était "ignore", on le garde tel quel (ignoré manuellement)
                    # Si l'écart était déjà "resolu", on ne fait rien
                else:
                    # Si la commande n'existe toujours pas dans Cyrus
                    # Si l'écart était "resolu", le remettre à "ouvert" (le problème est revenu)
                    if ecart_existant.statut == 'resolu':
                        ecart_existant.statut = 'ouvert'
                        ecart_existant.save()
                    # Si l'écart était "ignore", on le garde tel quel
                    # Si l'écart était déjà "ouvert", on ne fait rien
                    
            except EcartCommande.DoesNotExist:
                # Aucun écart existant, créer un nouveau si la commande n'existe pas dans Cyrus
                if not existe_cyrus:
                    EcartCommande.objects.create(
                        commande_asten=commande_asten,
                        statut='ouvert'
                    )
                    ecarts_crees += 1
                # Si la commande existe dans Cyrus, pas besoin de créer un écart
        
        return {
            'ecarts_crees': ecarts_crees,
            'ecarts_resolus': ecarts_resolus
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

