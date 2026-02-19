from django.contrib import admin
from .models import ImportFichier, FactureBackupCyrus


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


@admin.register(FactureBackupCyrus)
class FactureBackupCyrusAdmin(admin.ModelAdmin):
    list_display = (
        'code_magasin', 'numero_facture', 'type_facture',
        'theme_promo', 'nom_fichier', 'date_modif', 'date_import'
    )
    list_filter = ('type_facture', 'code_magasin', 'date_modif')
    search_fields = ('numero_facture', 'nom_fichier', 'code_magasin')
    readonly_fields = ('date_import', 'date_maj')
