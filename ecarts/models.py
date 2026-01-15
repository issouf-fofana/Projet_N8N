from django.db import models
from asten.models import CommandeAsten
from gpv.models import CommandeGPV
from legend.models import CommandeLegend


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


class EcartGPV(models.Model):
    """Modèle représentant un écart (commande GPV non intégrée dans Cyrus)"""
    commande_gpv = models.OneToOneField(
        CommandeGPV,
        on_delete=models.CASCADE,
        related_name='ecart',
        verbose_name="Commande GPV"
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
        verbose_name = "Écart de commande GPV"
        verbose_name_plural = "Écarts de commandes GPV"
        ordering = ['-date_creation']

    def __str__(self):
        return f"Écart GPV - {self.commande_gpv.numero_commande} - {self.commande_gpv.code_magasin}"


class EcartLegend(models.Model):
    """Modèle représentant un écart (commande Legend non intégrée dans Cyrus)"""
    commande_legend = models.OneToOneField(
        CommandeLegend,
        on_delete=models.CASCADE,
        related_name='ecart',
        verbose_name="Commande Legend"
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

    TYPE_CHOICES = [
        ('gpv_absent', 'Absent dans GPV'),
        ('cyrus_absent', 'Absent dans Cyrus'),
        ('coherence', 'Écart de cohérence globale'),
    ]
    type_ecart = models.CharField(
        max_length=20,
        choices=TYPE_CHOICES,
        default='gpv_absent',
        verbose_name="Type d'écart"
    )

    commentaire = models.TextField(null=True, blank=True, verbose_name="Commentaire")

    class Meta:
        verbose_name = "Écart de commande Legend"
        verbose_name_plural = "Écarts de commandes Legend"
        ordering = ['-date_creation']

    def __str__(self):
        return f"Écart Legend - {self.commande_legend.numero_commande} - {self.commande_legend.depot_origine}"
