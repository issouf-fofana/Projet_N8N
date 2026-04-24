from django.db import models
from core.models import Magasin


class CommandeCyrus(models.Model):
    """Modèle représentant une commande Cyrus"""
    date_commande = models.DateField(verbose_name="Date de commande")
    numero_commande = models.CharField(max_length=50, verbose_name="Numéro de commande")
    code_magasin = models.ForeignKey(
        Magasin,
        on_delete=models.PROTECT,
        to_field='code',
        db_column='code_magasin',
        verbose_name="Code magasin"
    )
    
    date_reception = models.DateField(null=True, blank=True, verbose_name="Date de réception")
    nom_magasin    = models.CharField(max_length=255, null=True, blank=True, verbose_name="Nom magasin")
    type_commande  = models.CharField(max_length=50, null=True, blank=True, verbose_name="Type commande")
    montant        = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Montant")
    statut         = models.CharField(max_length=50, null=True, blank=True, verbose_name="Statut")
    
    date_import = models.DateTimeField(auto_now_add=True, verbose_name="Date d'import")
    fichier_source = models.CharField(max_length=255, null=True, blank=True, verbose_name="Fichier source")

    class Meta:
        verbose_name = "Commande Cyrus"
        verbose_name_plural = "Commandes Cyrus"
        unique_together = [['date_commande', 'numero_commande', 'code_magasin']]
        indexes = [
            models.Index(fields=['date_commande', 'numero_commande', 'code_magasin']),
            models.Index(fields=['date_commande']),
            models.Index(fields=['code_magasin']),
            models.Index(fields=['numero_commande']),
        ]
        ordering = ['-date_commande', 'numero_commande']

    def __str__(self):
        return f"Cyrus - {self.numero_commande} - {self.code_magasin} - {self.date_commande}"
