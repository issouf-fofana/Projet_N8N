from django.contrib import admin
from .models import Ticket, TicketCategorie, SuiviTicket, PieceJointe, HistoriqueStatut, Technicien


@admin.register(TicketCategorie)
class TicketCategorieAdmin(admin.ModelAdmin):
    list_display = ("nom", "actif", "date_creation")
    search_fields = ("nom",)


class PieceJointeInline(admin.TabularInline):
    model = PieceJointe
    extra = 0
    readonly_fields = ("date_upload",)


class SuiviInline(admin.TabularInline):
    model = SuiviTicket
    extra = 0
    readonly_fields = ("date_creation",)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "numero_ticket",
        "type_demande",
        "statut",
        "urgence",
        "impact",
        "magasin",
        "demandeur",
        "assignees",
        "date_creation",
    )
    list_filter = ("statut", "type_demande", "urgence", "impact", "magasin")
    search_fields = ("numero_ticket", "demandeur", "assigne_a__nom")
    inlines = [SuiviInline]

    @admin.display(description="Assigné à")
    def assignees(self, obj):
        return ", ".join(obj.assigne_a.values_list("nom", flat=True))


@admin.register(SuiviTicket)
class SuiviTicketAdmin(admin.ModelAdmin):
    list_display = ("ticket", "auteur", "date_creation")
    search_fields = ("ticket__numero_ticket", "auteur")
    inlines = [PieceJointeInline]


@admin.register(PieceJointe)
class PieceJointeAdmin(admin.ModelAdmin):
    list_display = ("suivi", "type_fichier", "date_upload")
    list_filter = ("type_fichier",)


@admin.register(HistoriqueStatut)
class HistoriqueStatutAdmin(admin.ModelAdmin):
    list_display = ("ticket", "ancien_statut", "nouveau_statut", "utilisateur", "date_changement")
    list_filter = ("nouveau_statut",)


@admin.register(Technicien)
class TechnicienAdmin(admin.ModelAdmin):
    list_display = ("nom", "actif", "date_creation")
    search_fields = ("nom",)

