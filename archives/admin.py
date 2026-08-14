from django.contrib import admin

from .models import ArchiveMensuelle, FichierPurge


@admin.register(ArchiveMensuelle)
class ArchiveMensuelleAdmin(admin.ModelAdmin):
    list_display = ('source', 'mois', 'nb_lignes', 'archive_le')
    list_filter = ('source', 'mois')
    search_fields = ('source',)
    date_hierarchy = 'mois'
    readonly_fields = ('source', 'mois', 'nb_lignes', 'details', 'archive_le')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(FichierPurge)
class FichierPurgeAdmin(admin.ModelAdmin):
    list_display = ('type_fichier', 'nom_fichier', 'nb_lignes', 'purge_le')
    list_filter = ('type_fichier',)
    search_fields = ('nom_fichier',)
    readonly_fields = ('type_fichier', 'nom_fichier', 'nb_lignes', 'purge_le')

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
