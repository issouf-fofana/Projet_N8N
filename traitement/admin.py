from django.contrib import admin
from .models import Controle, Ecart, FichierSource


@admin.register(Controle)
class ControleAdmin(admin.ModelAdmin):
    list_display = ('type_controle', 'periode', 'date_execution', 'total_lignes', 'total_ecarts', 'statut', 'taux_conformite')
    list_filter = ('type_controle', 'statut', 'date_execution')
    search_fields = ('periode', 'type_controle')
    readonly_fields = ('id', 'date_execution', 'taux_conformite')
    date_hierarchy = 'date_execution'
    
    fieldsets = (
        ('Informations générales', {
            'fields': ('id', 'type_controle', 'periode', 'date_execution', 'statut')
        }),
        ('Statistiques', {
            'fields': ('total_lignes', 'total_ecarts', 'taux_conformite')
        }),
    )


@admin.register(Ecart)
class EcartAdmin(admin.ModelAdmin):
    list_display = ('controle', 'reference', 'type_ecart', 'date_creation')
    list_filter = ('type_ecart', 'controle__type_controle', 'date_creation')
    search_fields = ('reference', 'controle__periode')
    readonly_fields = ('id', 'date_creation')
    date_hierarchy = 'date_creation'
    
    fieldsets = (
        ('Informations générales', {
            'fields': ('id', 'controle', 'reference', 'type_ecart', 'date_creation')
        }),
        ('Valeurs', {
            'fields': ('valeur_source_a', 'valeur_source_b', 'details')
        }),
    )


@admin.register(FichierSource)
class FichierSourceAdmin(admin.ModelAdmin):
    list_display = ('nom_fichier', 'type_controle', 'origine', 'date_import', 'traite', 'controle')
    list_filter = ('type_controle', 'origine', 'traite', 'date_import')
    search_fields = ('nom_fichier', 'chemin')
    readonly_fields = ('id', 'date_import')
    date_hierarchy = 'date_import'
