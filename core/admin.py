from django.contrib import admin
from .models import Magasin


@admin.register(Magasin)
class MagasinAdmin(admin.ModelAdmin):
    list_display = ('code', 'nom', 'date_creation', 'date_modification')
    search_fields = ('code', 'nom')
    list_filter = ('date_creation',)
