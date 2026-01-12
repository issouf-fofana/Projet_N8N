import uuid
from django.db import models
from django.utils import timezone


class Controle(models.Model):
    """Modèle représentant un contrôle effectué"""
    
    TYPE_CHOICES = [
        ('commande', 'Commande'),
        ('facture', 'Facture'),
        ('br', 'BR'),
        ('legend', 'Legend'),
    ]
    
    STATUT_CHOICES = [
        ('en_cours', 'En cours'),
        ('termine', 'Terminé'),
        ('erreur', 'Erreur'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type_controle = models.CharField(max_length=50, choices=TYPE_CHOICES)
    periode = models.CharField(max_length=50, help_text="Période du contrôle (ex: 2026-01)")
    date_execution = models.DateTimeField(default=timezone.now)
    total_lignes = models.IntegerField(default=0)
    total_ecarts = models.IntegerField(default=0)
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default='en_cours')
    
    class Meta:
        ordering = ['-date_execution']
        verbose_name = "Contrôle"
        verbose_name_plural = "Contrôles"
    
    def __str__(self):
        return f"{self.get_type_controle_display()} - {self.periode} - {self.date_execution.strftime('%Y-%m-%d %H:%M')}"
    
    @property
    def taux_conformite(self):
        """Calcule le taux de conformité en pourcentage"""
        if self.total_lignes == 0:
            return 0
        return round((1 - self.total_ecarts / self.total_lignes) * 100, 2)


class FichierSource(models.Model):
    """Modèle représentant un fichier source importé"""
    
    TYPE_CHOICES = [
        ('commande', 'Commande'),
        ('facture', 'Facture'),
        ('br', 'BR'),
        ('legend', 'Legend'),
    ]
    
    ORIGINE_CHOICES = [
        ('asten', 'Asten (Source A)'),
        ('cyrus', 'Cyrus (Source B)'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    type_controle = models.CharField(max_length=50, choices=TYPE_CHOICES)
    origine = models.CharField(max_length=20, choices=ORIGINE_CHOICES)
    nom_fichier = models.CharField(max_length=255)
    chemin = models.CharField(max_length=500)
    date_import = models.DateTimeField(default=timezone.now)
    traite = models.BooleanField(default=False)
    controle = models.ForeignKey(
        Controle, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='fichiers'
    )
    
    class Meta:
        ordering = ['-date_import']
        verbose_name = "Fichier source"
        verbose_name_plural = "Fichiers sources"
    
    def __str__(self):
        return f"{self.get_type_controle_display()} - {self.get_origine_display()} - {self.nom_fichier}"


class Ecart(models.Model):
    """Modèle représentant un écart détecté lors d'un contrôle"""
    
    TYPE_ECART_CHOICES = [
        ('absent_a', 'Présent dans B mais absent dans A'),
        ('absent_b', 'Présent dans A mais absent dans B'),
        ('valeur_differente', 'Valeurs différentes'),
        ('corrige', 'Écart corrigé'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    controle = models.ForeignKey(
        Controle, 
        on_delete=models.CASCADE, 
        related_name='ecarts'
    )
    reference = models.CharField(max_length=255, help_text="Clé de référence pour la comparaison")
    valeur_source_a = models.TextField(null=True, blank=True, help_text="Valeur dans la source A")
    valeur_source_b = models.TextField(null=True, blank=True, help_text="Valeur dans la source B")
    type_ecart = models.CharField(max_length=50, choices=TYPE_ECART_CHOICES)
    date_creation = models.DateTimeField(default=timezone.now)
    details = models.JSONField(null=True, blank=True, help_text="Détails supplémentaires de l'écart")
    
    class Meta:
        ordering = ['-date_creation']
        verbose_name = "Écart"
        verbose_name_plural = "Écarts"
        indexes = [
            models.Index(fields=['controle', 'type_ecart']),
            models.Index(fields=['reference']),
        ]
    
    def __str__(self):
        return f"{self.controle.get_type_controle_display()} - {self.reference} - {self.get_type_ecart_display()}"
