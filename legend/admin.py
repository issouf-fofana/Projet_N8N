from django.contrib import admin
from .models import CommandeLegend


@admin.register(CommandeLegend)
class CommandeLegendAdmin(admin.ModelAdmin):
    list_display = ('numero_commande', 'date_commande', 'depot_origine', 'exportee', 'fichier_source')
    list_filter = ('exportee', 'date_commande')
    search_fields = ('numero_commande', 'numero_brut', 'depot_origine', 'depot_destination')
    readonly_fields = ('date_import',)
    date_hierarchy = 'date_commande'







