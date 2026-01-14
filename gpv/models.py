from django.db import models
from core.models import Magasin


class CommandeGPV(models.Model):
    """Modèle représentant une commande GPV"""
    numero_commande = models.CharField(max_length=50, verbose_name="Numéro de commande")
    code_magasin = models.ForeignKey(
        Magasin,
        on_delete=models.PROTECT,
        to_field='code',
        db_column='code_magasin',
        verbose_name="Code magasin"
    )
    nom_magasin = models.CharField(max_length=255, null=True, blank=True, verbose_name="Nom magasin")
    
    # Dates
    date_creation = models.DateField(verbose_name="Date de création")
    date_validation = models.DateField(null=True, blank=True, verbose_name="Date de validation")
    date_transfert = models.DateField(null=True, blank=True, verbose_name="Date de transfert")
    
    # Utiliser date_creation comme date_commande pour la comparaison avec Cyrus
    # (on peut aussi créer un champ date_commande dérivé si nécessaire)
    
    statut = models.CharField(max_length=50, null=True, blank=True, verbose_name="Statut")
    
    date_import = models.DateTimeField(auto_now_add=True, verbose_name="Date d'import")
    fichier_source = models.CharField(max_length=255, null=True, blank=True, verbose_name="Fichier source")

    class Meta:
        verbose_name = "Commande GPV"
        verbose_name_plural = "Commandes GPV"
        # Utiliser date_creation, numero_commande, code_magasin comme clé unique
        unique_together = [['date_creation', 'numero_commande', 'code_magasin']]
        indexes = [
            models.Index(fields=['date_creation', 'numero_commande', 'code_magasin']),
            models.Index(fields=['date_creation']),
            models.Index(fields=['code_magasin']),
        ]
        ordering = ['-date_creation', 'numero_commande']

    def __str__(self):
        return f"GPV - {self.numero_commande} - {self.code_magasin} - {self.date_creation}"
    
    @property
    def date_commande(self):
        """Propriété pour compatibilité avec le système de comparaison"""
        return self.date_creation
