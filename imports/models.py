from django.db import models


class ImportFichier(models.Model):
    """Modèle pour tracer les imports de fichiers"""
    TYPE_CHOICES = [
        ('asten', 'Asten'),
        ('cyrus', 'Cyrus'),
        ('gpv', 'GPV'),
        ('legend', 'Legend'),
        ('br_asten', 'BR Asten'),
        ('facture_backup', 'Facture Backup'),
    ]
    
    type_fichier = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name="Type de fichier")
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


class FactureSage(models.Model):
    """Fichiers Facture Sage (métadonnées uniquement)"""
    nom_fichier = models.CharField(max_length=255, unique=True, verbose_name="Nom du fichier")
    chemin_fichier = models.CharField(max_length=500, verbose_name="Chemin du fichier")
    date_depot = models.DateField(verbose_name="Date de dépôt")
    date_modif = models.DateTimeField(verbose_name="Date de modification")
    nombre_lignes = models.IntegerField(default=0, verbose_name="Nombre de lignes")
    date_import = models.DateTimeField(auto_now_add=True, verbose_name="Date d'import")
    date_maj = models.DateTimeField(auto_now=True, verbose_name="Date de mise à jour")

    class Meta:
        verbose_name = "Facture Sage"
        verbose_name_plural = "Factures Sage"
        ordering = ['-date_depot', '-date_modif', 'nom_fichier']

    def __str__(self):
        return f"Sage - {self.nom_fichier} - {self.date_depot}"


class FactureBackupCyrus(models.Model):
    """Factures Backup Cyrus (extraction depuis la première ligne du fichier)"""
    TYPE_CHOICES = [
        ('general', 'Générale'),
        ('promo', 'Promo'),
    ]

    code_magasin = models.CharField(max_length=3, verbose_name="Code magasin")
    numero_facture = models.CharField(max_length=30, verbose_name="Numéro facture")
    type_facture = models.CharField(max_length=10, choices=TYPE_CHOICES, verbose_name="Type facture")
    theme_promo = models.CharField(max_length=10, null=True, blank=True, verbose_name="Thème promo")
    nom_fichier = models.CharField(max_length=255, unique=True, verbose_name="Nom du fichier")
    chemin_fichier = models.CharField(max_length=500, verbose_name="Chemin du fichier")
    date_modif = models.DateTimeField(verbose_name="Date de modification")
    date_import = models.DateTimeField(auto_now_add=True, verbose_name="Date d'import")
    date_maj = models.DateTimeField(auto_now=True, verbose_name="Date de mise à jour")

    class Meta:
        verbose_name = "Facture Backup Cyrus"
        verbose_name_plural = "Factures Backup Cyrus"
        ordering = ['-date_modif', 'nom_fichier']

    def __str__(self):
        return f"Backup - {self.nom_fichier} - {self.code_magasin}"
