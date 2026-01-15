from django.contrib import admin
from .models import EcartCommande, EcartGPV, EcartLegend


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


@admin.register(EcartGPV)
class EcartGPVAdmin(admin.ModelAdmin):
    list_display = ('commande_gpv', 'statut', 'date_creation', 'date_modification')
    list_filter = ('statut', 'date_creation')
    search_fields = (
        'commande_gpv__numero_commande',
        'commande_gpv__code_magasin__code',
        'commande_gpv__code_magasin__nom'
    )
    readonly_fields = ('date_creation', 'date_modification')
    date_hierarchy = 'date_creation'


@admin.register(EcartLegend)
class EcartLegendAdmin(admin.ModelAdmin):
    list_display = ('commande_legend', 'type_ecart', 'statut', 'date_creation', 'date_modification')
    list_filter = ('type_ecart', 'statut', 'date_creation')
    search_fields = (
        'commande_legend__numero_commande',
        'commande_legend__numero_brut',
        'commande_legend__depot_origine',
        'commande_legend__depot_destination'
    )
    readonly_fields = ('date_creation', 'date_modification')
    date_hierarchy = 'date_creation'
