from django.contrib import admin
from .models import ImportFichier


@admin.register(ImportFichier)
class ImportFichierAdmin(admin.ModelAdmin):
    list_display = (
        'type_fichier', 'nom_fichier', 'statut', 
        'nombre_lignes', 'nombre_nouveaux', 'nombre_dupliques', 'date_import'
    )
    list_filter = ('type_fichier', 'statut', 'date_import')
    search_fields = ('nom_fichier',)
    readonly_fields = ('date_import', 'nombre_lignes', 'nombre_nouveaux', 'nombre_dupliques')
    date_hierarchy = 'date_import'
