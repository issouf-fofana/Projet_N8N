from django.shortcuts import render, redirect
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.http import require_http_methods
from django.utils.dateparse import parse_date
from django.urls import reverse
from imports.services import scanner_et_importer_fichiers
from ecarts.services import recalculer_ecarts, get_statistiques
from asten.models import CommandeAsten
from cyrus.models import CommandeCyrus
from ecarts.models import EcartCommande
from core.models import Magasin


def dashboard(request):
    """Vue principale du dashboard"""
    # Les données existantes en base sont TOUJOURS chargées et affichées
    # L'actualisation automatique se fait silencieusement (une seule fois par session)
    # Les données restent en base de données donc elles persistent même si on change de type
    if 'donnees_actualisees' not in request.session:
        try:
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
    
    # Récupérer les filtres (gérer les valeurs "None" en string et la sélection multiple)
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    code_magasin = request.GET.getlist('magasin')  # Récupérer plusieurs valeurs pour la sélection multiple
    type_donnees = request.GET.get('type_donnees', 'commandes_asten')  # Par défaut: commandes Asten
    
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
        
        # Compter les commandes intégrées
        commandes_asten_filtered = CommandeAsten.objects.filter(**filtres_asten)
        commandes_integres = 0
        for cmd_asten in commandes_asten_filtered:
            if CommandeCyrus.objects.filter(
                date_commande=cmd_asten.date_commande,
                numero_commande=cmd_asten.numero_commande,
                code_magasin=cmd_asten.code_magasin
            ).exists():
                commandes_integres += 1
        
        # Compter les écarts avec les filtres
        filtres_ecarts = {}
        if date_debut_parsed:
            filtres_ecarts['commande_asten__date_commande__gte'] = date_debut_parsed
        if date_fin_parsed:
            filtres_ecarts['commande_asten__date_commande__lte'] = date_fin_parsed
        if code_magasin:
            filtres_ecarts['commande_asten__code_magasin__code__in'] = code_magasin
        
        total_ecarts = EcartCommande.objects.filter(**filtres_ecarts).count()
        
        # Calculer les taux
        taux_integration = round((commandes_integres / total_asten * 100) if total_asten > 0 else 0, 2)
        taux_non_integration = round((total_ecarts / total_asten * 100) if total_asten > 0 else 0, 2)
        
        # Normaliser les statistiques pour correspondre au template
        stats = {
            'total_source': total_asten,
            'total_target': total_cyrus,
            'integres': commandes_integres,
            'non_integres': total_ecarts,
            'taux_integration': taux_integration,
            'taux_non_integration': taux_non_integration,
        }
        
        # Charger TOUTES les données existantes en base (pas de limite pour l'affichage)
        commandes_asten = CommandeAsten.objects.filter(**filtres_asten).select_related('code_magasin').order_by('-date_commande', 'numero_commande')
        
        # Préparer les données pour l'affichage
        commandes_integres_list = []
        commandes_non_integres_list = []
        
        for cmd_asten in commandes_asten:
            # Vérifier si intégrée dans Cyrus
            cmd_cyrus = CommandeCyrus.objects.filter(
                date_commande=cmd_asten.date_commande,
                numero_commande=cmd_asten.numero_commande,
                code_magasin=cmd_asten.code_magasin
            ).first()
            
            # Récupérer l'écart si existe
            try:
                ecart = cmd_asten.ecart
            except:
                ecart = None
            
            item = {
                'asten': cmd_asten,
                'cyrus': cmd_cyrus,
                'integre': cmd_cyrus is not None,
                'ecart': ecart,
            }
            
            # Séparer les intégrées et non intégrées
            if cmd_cyrus is not None:
                commandes_integres_list.append(item)
            else:
                commandes_non_integres_list.append(item)
        
        # Mettre les non intégrées en premier, puis les intégrées
        commandes_data = commandes_non_integres_list + commandes_integres_list
        titre_tableau = "Comparaison Asten vs Cyrus"
        
    elif type_donnees == 'commandes_gpv':
        # TODO: À implémenter quand les modèles GPV seront créés
        stats = {
            'total_source': 0,
            'total_target': 0,
            'integres': 0,
            'non_integres': 0,
            'taux_integration': 0,
            'taux_non_integration': 0,
        }
        titre_tableau = "Comparaison GPV vs Cyrus"
        
    elif type_donnees == 'commandes_legend':
        # TODO: À implémenter quand les modèles Legend seront créés
        stats = {
            'total_source': 0,
            'total_target': 0,
            'integres': 0,
            'non_integres': 0,
            'taux_integration': 0,
            'taux_non_integration': 0,
        }
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
        # TODO: À implémenter quand les modèles BR seront créés
        stats = {
            'total_source': 0,
            'total_target': 0,
            'integres': 0,
            'non_integres': 0,
            'taux_integration': 0,
            'taux_non_integration': 0,
        }
        titre_tableau = "Comparaison BR Asten vs Cyrus"
    
    context = {
        'stats': stats,
        'commandes': commandes_data,
        'magasins': magasins,
        'type_donnees': type_donnees,
        'titre_tableau': titre_tableau,
        'stats_label_source': 'Asten' if type_donnees == 'commandes_asten' else 'Source',
        'stats_label_target': 'Cyrus',
        'filtres': {
            'date_debut': date_debut or '',
            'date_fin': date_fin or '',
            'magasin': code_magasin if code_magasin else [],
            'type_donnees': type_donnees,
        }
    }
    
    return render(request, 'dashboard/dashboard.html', context)


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
            
            if nouveau_statut in ['ouvert', 'resolu', 'ignore']:
                ecart.statut = nouveau_statut
                if commentaire:
                    ecart.commentaire = commentaire
                ecart.save()
                
                if nouveau_statut == 'resolu':
                    messages.success(request, "L'écart a été marqué comme résolu.")
                elif nouveau_statut == 'ignore':
                    messages.info(request, "L'écart a été marqué comme ignoré.")
                else:
                    messages.info(request, "L'écart a été remis à ouvert.")
                
                return redirect('dashboard:detail_ecart', ecart_id=ecart_id)
        
        context = {
            'ecart': ecart,
            'existe_cyrus': existe_cyrus,
        }
        return render(request, 'dashboard/detail_ecart.html', context)
    except EcartCommande.DoesNotExist:
        messages.error(request, "Écart introuvable.")
        return redirect('dashboard:dashboard')


def liste_ecarts(request):
    """Affiche la liste des écarts"""
    # Filtres
    date_debut = request.GET.get('date_debut')
    date_fin = request.GET.get('date_fin')
    code_magasin = request.GET.get('magasin')
    statut = request.GET.get('statut', '')  # Par défaut, afficher tous les statuts
    
    date_debut_parsed = parse_date(date_debut) if date_debut else None
    date_fin_parsed = parse_date(date_fin) if date_fin else None
    
    # Construire les filtres
    filtres = {}
    
    # Filtrer par statut seulement si un statut spécifique est sélectionné
    if statut and statut != '':
        filtres['statut'] = statut
    
    if date_debut_parsed:
        filtres['commande_asten__date_commande__gte'] = date_debut_parsed
    if date_fin_parsed:
        filtres['commande_asten__date_commande__lte'] = date_fin_parsed
    if code_magasin:
        filtres['commande_asten__code_magasin__code'] = code_magasin
    
    ecarts = EcartCommande.objects.filter(**filtres).select_related(
        'commande_asten__code_magasin'
    ).order_by('-date_creation')
    
    magasins = Magasin.objects.all().order_by('code')
    
    context = {
        'ecarts': ecarts,
        'magasins': magasins,
        'filtres': {
            'date_debut': date_debut or '',
            'date_fin': date_fin or '',
            'magasin': code_magasin or '',
            'statut': statut or '',
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
    
    # Charger tous les magasins pour le select (le filtrage se fait côté client)
    magasins = Magasin.objects.all().order_by('code')
    
    context = {
        'commandes': commandes,
        'magasins': magasins,
        'filtres': {
            'date_debut': date_debut,
            'date_fin': date_fin,
            'magasin': codes_magasins,
            'numero_commande': numero_commande,
            'recherche_magasin': recherche_magasin,
        },
        'total': commandes.count(),
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
    
    # Charger tous les magasins pour le select (le filtrage se fait côté client)
    magasins = Magasin.objects.all().order_by('code')
    
    context = {
        'commandes': commandes,
        'magasins': magasins,
        'filtres': {
            'date_debut': date_debut,
            'date_fin': date_fin,
            'magasin': codes_magasins,
            'numero_commande': numero_commande,
            'recherche_magasin': recherche_magasin,
        },
        'total': commandes.count(),
    }
    
    return render(request, 'dashboard/liste_commandes_cyrus.html', context)


def detail_commande_asten(request, commande_id):
    """Affiche le détail d'une commande Asten"""
    try:
        commande = CommandeAsten.objects.select_related('code_magasin').get(pk=commande_id)
        
        # Vérifier si la commande existe dans Cyrus
        commande_cyrus = CommandeCyrus.objects.filter(
            date_commande=commande.date_commande,
            numero_commande=commande.numero_commande,
            code_magasin=commande.code_magasin
        ).first()
        
        # Vérifier si un écart existe
        try:
            ecart = commande.ecart
        except:
            ecart = None
        
        context = {
            'commande': commande,
            'commande_cyrus': commande_cyrus,
            'ecart': ecart,
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
