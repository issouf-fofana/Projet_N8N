from django.contrib import admin
from .models import EcartCommande


@admin.register(EcartCommande)
class EcartCommandeAdmin(admin.ModelAdmin):
    list_display = ('commande_asten', 'statut', 'date_creation', 'date_modification')
    list_filter = ('statut', 'date_creation')
    search_fields = (
        'commande_asten__numero_commande',
        'commande_asten__code_magasin__code',
        'commande_asten__code_magasin__nom'
    )
    readonly_fields = ('date_creation', 'date_modification')
    date_hierarchy = 'date_creation'
