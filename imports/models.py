from django.db import models


class ImportFichier(models.Model):
    """Modèle pour tracer les imports de fichiers"""
    TYPE_CHOICES = [
        ('asten', 'Asten'),
        ('cyrus', 'Cyrus'),
        ('gpv', 'GPV'),
        ('legend', 'Legend'),
        ('br_asten', 'BR Asten'),
    ]
    
    type_fichier = models.CharField(max_length=10, choices=TYPE_CHOICES, verbose_name="Type de fichier")
    nom_fichier = models.CharField(max_length=255, verbose_name="Nom du fichier")
    chemin_fichier = models.CharField(max_length=500, verbose_name="Chemin du fichier")
    
    date_import = models.DateTimeField(auto_now_add=True, verbose_name="Date d'import")
    nombre_lignes = models.IntegerField(default=0, verbose_name="Nombre de lignes importées")
    nombre_nouveaux = models.IntegerField(default=0, verbose_name="Nombre de nouvelles commandes")
    nombre_dupliques = models.IntegerField(default=0, verbose_name="Nombre de doublons ignorés")
    
    statut = models.CharField(
        max_length=20,
        choices=[
            ('en_cours', 'En cours'),
            ('termine', 'Terminé'),
            ('erreur', 'Erreur'),
        ],
        default='en_cours',
        verbose_name="Statut"
    )
    
    message_erreur = models.TextField(null=True, blank=True, verbose_name="Message d'erreur")

    class Meta:
        verbose_name = "Import de fichier"
        verbose_name_plural = "Imports de fichiers"
        ordering = ['-date_import']

    def __str__(self):
        return f"{self.type_fichier.upper()} - {self.nom_fichier} - {self.date_import}"
