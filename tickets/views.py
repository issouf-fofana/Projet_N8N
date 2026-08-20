from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.dateparse import parse_date
from django.core.paginator import Paginator
from django.db.models import Case, When, IntegerField, Count
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from .forms import TicketForm, SuiviTicketForm, StatutTicketForm, AssignationTicketForm, ModifierTicketForm
from .models import Ticket, SuiviTicket, PieceJointe, HistoriqueStatut, CompteEmail
from .utils import charger_techniciens_si_vide


def user_can_view_ticket(user, ticket):
    return True


def user_can_update_ticket(user, ticket):
    return True


def liste_tickets(request):
    queryset = Ticket.objects.select_related(
        "categorie", "magasin"
    ).prefetch_related("assigne_a")

    type_demande = request.GET.get("type_demande") or ""
    statut = request.GET.get("statut") or ""
    urgence = request.GET.get("urgence") or ""
    magasin = request.GET.get("magasin") or ""

    if type_demande:
        queryset = queryset.filter(type_demande=type_demande)
    if statut:
        queryset = queryset.filter(statut=statut)
    if urgence:
        queryset = queryset.filter(urgence=urgence)
    if magasin:
        queryset = queryset.filter(magasin__code=magasin)

    date_debut = parse_date(request.GET.get("date_debut") or "")
    date_fin = parse_date(request.GET.get("date_fin") or "")
    if date_debut:
        queryset = queryset.filter(date_creation__date__gte=date_debut)
    if date_fin:
        queryset = queryset.filter(date_creation__date__lte=date_fin)

    # Calculer les magasins les plus touchés
    # Par défaut, utiliser TOUS les tickets (sans filtres) pour les magasins
    # Sauf si des filtres de date sont explicitement appliqués
    queryset_magasins = Ticket.objects.select_related("magasin")
    
    # Appliquer uniquement les filtres de date si présents (pour la période)
    # Les autres filtres (type, statut, urgence, magasin) ne s'appliquent pas aux magasins
    # car on veut toujours voir les magasins les plus touchés globalement
    if date_debut:
        queryset_magasins = queryset_magasins.filter(date_creation__date__gte=date_debut)
    if date_fin:
        queryset_magasins = queryset_magasins.filter(date_creation__date__lte=date_fin)
    
    # Calculer les 4 magasins les plus touchés
    # Forcer l'évaluation de la requête pour éviter les problèmes de cache
    magasins_touches_raw = list(queryset_magasins.values('magasin__code', 'magasin__nom').annotate(
        count=Count('id')
    ).order_by('-count')[:4])
    
    # Ajouter les couleurs selon le rang
    def get_magasin_colors(rank):
        if rank == 0:
            return {
                'border_color': '#dc2626',
                'color_start': '#dc2626',
                'color_end': '#991b1b',
                'bg_start': '#fef2f2',
                'bg_end': '#fee2e2',
                'shadow': 'rgba(220, 38, 38, 0.3)'
            }
        elif rank == 1:
            return {
                'border_color': '#ef4444',
                'color_start': '#ef4444',
                'color_end': '#dc2626',
                'bg_start': '#fef2f2',
                'bg_end': '#fee2e2',
                'shadow': 'rgba(239, 68, 68, 0.3)'
            }
        elif rank == 2:
            return {
                'border_color': '#f97316',
                'color_start': '#f97316',
                'color_end': '#ea580c',
                'bg_start': '#fff7ed',
                'bg_end': '#ffedd5',
                'shadow': 'rgba(249, 115, 22, 0.3)'
            }
        elif rank == 3:
            return {
                'border_color': '#fb923c',
                'color_start': '#fb923c',
                'color_end': '#f97316',
                'bg_start': '#fff7ed',
                'bg_end': '#ffedd5',
                'shadow': 'rgba(251, 146, 60, 0.3)'
            }
        else:
            return {
                'border_color': '#fbbf24',
                'color_start': '#fbbf24',
                'color_end': '#f59e0b',
                'bg_start': '#fffbeb',
                'bg_end': '#fef3c7',
                'shadow': 'rgba(251, 191, 36, 0.3)'
            }
    
    magasins_touches = []
    for idx, magasin_data in enumerate(magasins_touches_raw):
        colors = get_magasin_colors(idx)
        magasin_data.update(colors)
        magasins_touches.append(magasin_data)

    # Trier : tickets non résolus/fermés en premier, puis par date de mise à jour
    queryset = queryset.annotate(
        priority=Case(
            When(statut__in=[Ticket.STATUT_RESOLU, Ticket.STATUT_FERME], then=1),
            default=0,
            output_field=IntegerField()
        )
    ).order_by("priority", "-date_mise_a_jour")
    
    paginator = Paginator(queryset, 20)
    page_obj = paginator.get_page(request.GET.get("page"))

    # Calculer les stats sur le queryset filtré (pour correspondre à la liste affichée)
    total_filtre = queryset.count()
    resolu_filtre = queryset.filter(statut=Ticket.STATUT_RESOLU).count()
    en_cours_filtre = queryset.filter(statut=Ticket.STATUT_EN_COURS).count()
    en_attente_filtre = queryset.filter(statut=Ticket.STATUT_EN_ATTENTE).count()
    
    # Calculer les pourcentages basés sur les tickets filtrés
    taux_resolu = round((resolu_filtre / total_filtre * 100) if total_filtre > 0 else 0, 1)
    taux_en_cours = round((en_cours_filtre / total_filtre * 100) if total_filtre > 0 else 0, 1)
    taux_en_attente = round((en_attente_filtre / total_filtre * 100) if total_filtre > 0 else 0, 1)
    
    # Calculer aussi les stats globales (sans filtres) pour référence
    all_tickets = Ticket.objects.all()
    total_global = all_tickets.count()
    
    context = {
        "page_obj": page_obj,
        "type_choices": Ticket.TYPE_CHOICES,
        "statut_choices": Ticket.STATUT_CHOICES,
        "urgence_choices": Ticket.NIVEAU_CHOICES,
        "type_demande": type_demande,
        "statut": statut,
        "urgence": urgence,
        "magasin": magasin,
        "date_debut": date_debut,
        "date_fin": date_fin,
        "date_debut_str": request.GET.get("date_debut", ""),
        "date_fin_str": request.GET.get("date_fin", ""),
        "stats": {
            "total": total_filtre,
            "resolu": resolu_filtre,
            "en_cours": en_cours_filtre,
            "en_attente": en_attente_filtre,
            "taux_resolu": taux_resolu,
            "taux_en_cours": taux_en_cours,
            "taux_en_attente": taux_en_attente,
            "total_global": total_global,
        },
        "magasins_touches": magasins_touches,
    }
    return render(request, "tickets/liste.html", context)


def nouveau_ticket(request):
    charger_techniciens_si_vide()
    if request.method == "POST":
        form = TicketForm(request.POST, request.FILES)
        
        # Exclure le champ fichiers de la validation pour éviter les erreurs avec plusieurs fichiers
        if 'fichiers' in form.errors:
            del form.errors['fichiers']
        
        if form.is_valid():
            ticket = form.save(commit=False)
            ticket.demandeur = form.cleaned_data.get("demandeur", "").strip()
            ticket.save()
            form.save_m2m()
            HistoriqueStatut.objects.create(
                ticket=ticket,
                ancien_statut="",
                nouveau_statut=ticket.statut,
                utilisateur=form.cleaned_data.get("demandeur", "").strip(),
            )
            description = form.cleaned_data.get("description", "").strip()
            fichiers = request.FILES.getlist("fichiers")
            if description or fichiers:
                suivi = SuiviTicket.objects.create(
                    ticket=ticket,
                    auteur=form.cleaned_data.get("demandeur", "").strip(),
                    message=description or "Création du ticket",
                )
                for fichier in fichiers:
                    PieceJointe.objects.create(suivi=suivi, fichier=fichier)
            messages.success(request, "Ticket créé avec succès.")
            return redirect("tickets:detail", ticket_id=ticket.id)
    else:
        form = TicketForm()
    return render(request, "tickets/nouveau.html", {"form": form})


def detail_ticket(request, ticket_id):
    ticket = get_object_or_404(
        Ticket.objects.select_related("categorie", "magasin")
        .prefetch_related("assigne_a", "suivis__pieces_jointes", "historiques_statut"),
        pk=ticket_id,
    )
    if not user_can_view_ticket(request.user, ticket):
        messages.error(request, "Vous n'avez pas accès à ce ticket.")
        return redirect("tickets:liste")

    suivi_form = SuiviTicketForm()
    statut_form = StatutTicketForm(initial={"statut": ticket.statut})
    assignation_form = AssignationTicketForm(initial={"assigne_a": ticket.assigne_a.all()})
    modifier_form = ModifierTicketForm(instance=ticket)

    if request.method == "POST":
        if "modifier_suivi" in request.POST:
            suivi_id = request.POST.get("modifier_suivi") or request.POST.get("suivi_id")
            suivi = get_object_or_404(SuiviTicket, pk=suivi_id, ticket=ticket)
            message = request.POST.get(f"message_{suivi_id}", "").strip()
            if message:
                suivi.message = message
                suivi.save(update_fields=["message"])
            fichiers = request.FILES.getlist(f"fichiers_{suivi_id}")
            for fichier in fichiers:
                PieceJointe.objects.create(suivi=suivi, fichier=fichier)
            messages.success(request, "Suivi mis à jour.")
            return redirect("tickets:detail", ticket_id=ticket.id)

        if "ajouter_suivi" in request.POST:
            if not user_can_update_ticket(request.user, ticket):
                messages.error(request, "Vous ne pouvez pas ajouter de suivi.")
                return redirect("tickets:detail", ticket_id=ticket.id)
            message = request.POST.get("message", "").strip()
            fichiers = request.FILES.getlist("fichiers") or request.FILES.getlist("fichiers[]")
            if not message and not fichiers:
                messages.error(request, "Ajoutez un message ou un fichier.")
                return redirect("tickets:detail", ticket_id=ticket.id)
            suivi = SuiviTicket.objects.create(
                ticket=ticket,
                auteur="Utilisateur",
                message=message or "Suivi sans texte",
            )
            for fichier in fichiers:
                PieceJointe.objects.create(suivi=suivi, fichier=fichier)
            messages.success(request, "Suivi ajouté avec succès.")
            return redirect("tickets:detail", ticket_id=ticket.id)

        if "changer_statut" in request.POST:
            if not user_can_update_ticket(request.user, ticket):
                messages.error(request, "Vous ne pouvez pas modifier le statut.")
                return redirect("tickets:detail", ticket_id=ticket.id)
            statut_form = StatutTicketForm(request.POST)
            if statut_form.is_valid():
                ticket.set_statut(statut_form.cleaned_data["statut"], utilisateur="Utilisateur")
                messages.success(request, "Statut mis à jour.")
                return redirect("tickets:detail", ticket_id=ticket.id)

        if "changer_assignes" in request.POST:
            assignation_form = AssignationTicketForm(request.POST)
            if assignation_form.is_valid():
                ticket.assigne_a.set(assignation_form.cleaned_data["assigne_a"])
                messages.success(request, "Assignation mise à jour.")
                return redirect("tickets:detail", ticket_id=ticket.id)

        if "modifier_ticket" in request.POST:
            if not user_can_update_ticket(request.user, ticket):
                messages.error(request, "Vous ne pouvez pas modifier ce ticket.")
                return redirect("tickets:detail", ticket_id=ticket.id)
            modifier_form = ModifierTicketForm(request.POST, instance=ticket)
            if modifier_form.is_valid():
                modifier_form.save()
                messages.success(request, "Informations du ticket mises à jour.")
                return redirect("tickets:detail", ticket_id=ticket.id)
            else:
                messages.error(request, "Erreur lors de la modification. Veuillez vérifier les champs.")

    context = {
        "ticket": ticket,
        "suivi_form": suivi_form,
        "statut_form": statut_form,
        "assignation_form": assignation_form,
        "modifier_form": modifier_form,
        "can_update": user_can_update_ticket(request.user, ticket),
    }
    return render(request, "tickets/detail.html", context)


def supprimer_ticket(request, ticket_id):
    """Supprimer un ticket"""
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    if request.method == "POST":
        ticket.delete()
        messages.success(request, "Ticket supprimé avec succès.")
        return redirect("tickets:liste")
    return redirect("tickets:detail", ticket_id=ticket.id)


def supprimer_tickets_multiple(request):
    """Supprimer plusieurs tickets en une seule fois"""
    if request.method == "POST":
        ticket_ids = request.POST.getlist("ticket_ids")
        if not ticket_ids:
            messages.error(request, "Aucun ticket sélectionné.")
            return redirect("tickets:liste")
        
        # Convertir les IDs en entiers et filtrer les valeurs valides
        try:
            ticket_ids_int = [int(tid) for tid in ticket_ids if tid]
        except (ValueError, TypeError):
            messages.error(request, "IDs de tickets invalides.")
            return redirect("tickets:liste")
        
        if not ticket_ids_int:
            messages.error(request, "Aucun ticket valide sélectionné.")
            return redirect("tickets:liste")
        
        tickets = Ticket.objects.filter(pk__in=ticket_ids_int)
        count = tickets.count()
        
        if count == 0:
            messages.error(request, "Aucun ticket trouvé à supprimer.")
            return redirect("tickets:liste")
        
        tickets.delete()
        
        if count == 1:
            messages.success(request, f"{count} ticket supprimé avec succès.")
        else:
            messages.success(request, f"{count} tickets supprimés avec succès.")
        
        return redirect("tickets:liste")
    
    messages.error(request, "Méthode non autorisée.")
    return redirect("tickets:liste")


# ─────────────────────────────────────────────────────────────────────────────
# Configuration Outlook 365
# ─────────────────────────────────────────────────────────────────────────────

@login_required
def config_email(request):
    """Page de configuration du compte Outlook."""
    compte, _ = CompteEmail.objects.get_or_create(pk=1, defaults={"label": "Boîte support"})

    if request.method == "POST":
        compte.label = request.POST.get("label", compte.label)
        compte.client_id = request.POST.get("client_id", "").strip()
        compte.client_secret = request.POST.get("client_secret", "").strip()
        compte.tenant_id = request.POST.get("tenant_id", "").strip()
        compte.is_active = "is_active" in request.POST
        compte.save()
        messages.success(request, "Configuration sauvegardée.")
        return redirect("tickets:config_email")

    redirect_uri = request.build_absolute_uri("/tickets/email/callback/")
    return render(request, "tickets/config_email.html", {"compte": compte, "redirect_uri": redirect_uri})


@login_required
def outlook_connect(request):
    """Redirige vers la page de connexion Microsoft OAuth2."""
    from tickets.outlook_service import get_authorize_url
    compte = get_object_or_404(CompteEmail, pk=1)
    if not compte.client_id or not compte.tenant_id or not compte.client_secret:
        messages.error(request, "Remplis d'abord le Client ID, Tenant ID et Client Secret.")
        return redirect("tickets:config_email")

    redirect_uri = request.build_absolute_uri("/tickets/email/callback/")
    url = get_authorize_url(compte, redirect_uri)
    return redirect(url)


def outlook_callback(request):
    """Callback OAuth2 Microsoft : échange le code et sauvegarde le token."""
    from tickets.outlook_service import exchange_code_for_token
    code = request.GET.get("code")
    state = request.GET.get("state")
    error = request.GET.get("error")

    if error:
        messages.error(request, f"Erreur Microsoft : {request.GET.get('error_description', error)}")
        return redirect("tickets:config_email")

    try:
        compte = CompteEmail.objects.get(pk=int(state))
        redirect_uri = request.build_absolute_uri("/tickets/email/callback/")
        exchange_code_for_token(compte, code, redirect_uri)
        messages.success(request, f"✅ Compte '{compte.label}' connecté à Outlook avec succès !")
    except Exception as e:
        messages.error(request, f"Erreur lors de la connexion : {e}")

    return redirect("tickets:config_email")


@login_required
@require_POST
def outlook_disconnect(request):
    """Déconnecte le compte Outlook (efface le refresh token)."""
    compte = get_object_or_404(CompteEmail, pk=1)
    compte.refresh_token = ""
    compte.delta_link = ""
    compte.is_active = False
    compte.save()
    messages.success(request, "Compte Outlook déconnecté.")
    return redirect("tickets:config_email")


@login_required
@require_POST
def outlook_poll_now(request):
    """Déclenche manuellement un polling immédiat."""
    from tickets.outlook_service import poll_all_accounts
    try:
        stats = poll_all_accounts()
        return JsonResponse({
            "ok": True,
            "message": (
                f"{stats['messages']} message(s) traité(s) — "
                f"{stats['crees']} remontée(s) créée(s) — "
                f"{stats['followups']} suivi(s)"
            ),
            "stats": stats,
        })
    except Exception as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
