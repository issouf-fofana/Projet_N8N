from django.db import models


class CommandeLegend(models.Model):
    """Modèle représentant une commande Legend"""
    numero_brut = models.CharField(max_length=100, verbose_name="Numéro brut")
    numero_commande = models.CharField(max_length=50, verbose_name="Numéro de commande")
    depot_origine = models.CharField(max_length=100, verbose_name="Dépôt d'origine")
    depot_destination = models.CharField(max_length=100, null=True, blank=True, verbose_name="Dépôt de destination")
    date_commande = models.DateField(verbose_name="Date de commande")

    observation = models.TextField(null=True, blank=True, verbose_name="Observation")
    transfert = models.CharField(max_length=100, null=True, blank=True, verbose_name="Transfert entre dépôt")
    exportee = models.BooleanField(default=False, verbose_name="Exportée")
    code_client = models.CharField(max_length=50, null=True, blank=True, verbose_name="Code client")
    code_depot = models.CharField(max_length=50, null=True, blank=True, verbose_name="Code dépôt")
    date_livraison_prevue = models.DateField(null=True, blank=True, verbose_name="Date livraison prévue")

    date_import = models.DateTimeField(auto_now_add=True, verbose_name="Date d'import")
    fichier_source = models.CharField(max_length=255, null=True, blank=True, verbose_name="Fichier source")

    class Meta:
        verbose_name = "Commande Legend"
        verbose_name_plural = "Commandes Legend"
        unique_together = [['date_commande', 'numero_commande', 'depot_origine']]
        indexes = [
            models.Index(fields=['date_commande', 'numero_commande']),
            models.Index(fields=['date_commande']),
            models.Index(fields=['numero_commande']),
        ]
        ordering = ['-date_commande', 'numero_commande']

    def __str__(self):
        return f"Legend - {self.numero_commande} - {self.depot_origine} - {self.date_commande}"







