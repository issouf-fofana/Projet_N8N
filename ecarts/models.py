from django.db import models
from asten.models import CommandeAsten


class EcartCommande(models.Model):
    """Modèle représentant un écart (commande Asten non intégrée dans Cyrus)"""
    commande_asten = models.OneToOneField(
        CommandeAsten,
        on_delete=models.CASCADE,
        related_name='ecart',
        verbose_name="Commande Asten"
    )
    
    date_creation = models.DateTimeField(auto_now_add=True, verbose_name="Date de création de l'écart")
    date_modification = models.DateTimeField(auto_now=True, verbose_name="Date de modification")
    
    # Statut de l'écart
    STATUT_CHOICES = [
        ('ouvert', 'Ouvert'),
        ('resolu', 'Résolu'),
        ('ignore', 'Ignoré'),
    ]
    statut = models.CharField(
        max_length=20,
        choices=STATUT_CHOICES,
        default='ouvert',
        verbose_name="Statut"
    )
    
    commentaire = models.TextField(null=True, blank=True, verbose_name="Commentaire")

    class Meta:
        verbose_name = "Écart de commande"
        verbose_name_plural = "Écarts de commandes"
        ordering = ['-date_creation']

    def __str__(self):
        return f"Écart - {self.commande_asten.numero_commande} - {self.commande_asten.code_magasin}"
