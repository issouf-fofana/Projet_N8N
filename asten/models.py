from django.db import models
from core.models import Magasin


class CommandeAsten(models.Model):
    """Modèle représentant une commande Asten"""
    date_commande = models.DateField(verbose_name="Date de commande")
    numero_commande = models.CharField(max_length=50, verbose_name="Numéro de commande")
    code_magasin = models.ForeignKey(
        Magasin,
        on_delete=models.PROTECT,
        to_field='code',
        db_column='code_magasin',
        verbose_name="Code magasin"
    )
    
    # Champs additionnels possibles (à adapter selon le format CSV réel)
    montant = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True, verbose_name="Montant")
    statut = models.CharField(max_length=50, null=True, blank=True, verbose_name="Statut")
    
    date_import = models.DateTimeField(auto_now_add=True, verbose_name="Date d'import")
    fichier_source = models.CharField(max_length=255, null=True, blank=True, verbose_name="Fichier source")

    class Meta:
        verbose_name = "Commande Asten"
        verbose_name_plural = "Commandes Asten"
        unique_together = [['date_commande', 'numero_commande', 'code_magasin']]
        indexes = [
            models.Index(fields=['date_commande', 'numero_commande', 'code_magasin']),
            models.Index(fields=['date_commande']),
            models.Index(fields=['code_magasin']),
        ]
        ordering = ['-date_commande', 'numero_commande']

    def __str__(self):
        return f"Asten - {self.numero_commande} - {self.code_magasin} - {self.date_commande}"
