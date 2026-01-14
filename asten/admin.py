from django.contrib import admin
from .models import CommandeAsten


@admin.register(CommandeAsten)
class CommandeAstenAdmin(admin.ModelAdmin):
    list_display = ('numero_commande', 'date_commande', 'code_magasin', 'montant', 'date_import')
    list_filter = ('date_commande', 'code_magasin', 'date_import')
    search_fields = ('numero_commande', 'code_magasin__code', 'code_magasin__nom')
    date_hierarchy = 'date_commande'
    readonly_fields = ('date_import',)
