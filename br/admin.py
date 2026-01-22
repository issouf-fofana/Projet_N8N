from django.contrib import admin
from .models import BRAsten


@admin.register(BRAsten)
class BRAstenAdmin(admin.ModelAdmin):
    list_display = ('numero_br', 'date_br', 'code_magasin', 'date_import', 'fichier_source')
    list_filter = ('date_br', 'code_magasin')
    search_fields = ('numero_br', 'code_magasin__code', 'code_magasin__nom')
    readonly_fields = ('date_import',)
    date_hierarchy = 'date_br'


