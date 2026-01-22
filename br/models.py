from django.db import models
from core.models import Magasin


class BRAsten(models.Model):
    """BR provenant d'Asten"""
    numero_br = models.CharField(max_length=50, verbose_name="Numéro BR")
    date_br = models.DateField(verbose_name="Date BR")
    statut_ic = models.CharField(max_length=50, null=True, blank=True, verbose_name="Statut IC")
    ic_integre = models.BooleanField(default=False, verbose_name="Intégré IC")
    code_magasin = models.ForeignKey(
        Magasin,
        on_delete=models.PROTECT,
        to_field='code',
        db_column='code_magasin',
        verbose_name="Code magasin"
    )

    date_import = models.DateTimeField(auto_now_add=True, verbose_name="Date d'import")
    fichier_source = models.CharField(max_length=255, null=True, blank=True, verbose_name="Fichier source")
    avis = models.TextField(null=True, blank=True, verbose_name="Avis/Commentaire")

    class Meta:
        verbose_name = "BR Asten"
        verbose_name_plural = "BR Asten"
        unique_together = [['numero_br', 'date_br', 'code_magasin']]
        indexes = [
            models.Index(fields=['numero_br', 'date_br', 'code_magasin']),
            models.Index(fields=['date_br']),
            models.Index(fields=['code_magasin']),
        ]
        ordering = ['-date_br', 'numero_br']

    def __str__(self):
        return f"BR Asten - {self.numero_br} - {self.code_magasin} - {self.date_br}"


