from django.db import models


class Magasin(models.Model):
    """Modèle représentant un magasin"""
    code = models.CharField(max_length=10, unique=True, primary_key=True, verbose_name="Code magasin")
    nom = models.CharField(max_length=200, verbose_name="Nom du magasin")
    date_creation = models.DateTimeField(auto_now_add=True, verbose_name="Date de création")
    date_modification = models.DateTimeField(auto_now=True, verbose_name="Date de modification")

    class Meta:
        verbose_name = "Magasin"
        verbose_name_plural = "Magasins"
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.nom}"
