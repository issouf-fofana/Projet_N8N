from django.db import models
from core.models import Magasin


class BRAsten(models.Model):
    """BR provenant d'Asten"""
    numero_br = models.CharField(max_length=50, verbose_name="Numéro BR")
    date_br = models.DateField(verbose_name="Date BR")
    date_validation = models.DateField(
        null=True,
        blank=True,
        verbose_name="Date validation"
    )
    commande_fournisseur = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        verbose_name="Commande fournisseur"
    )
    statut_ic = models.CharField(max_length=50, null=True, blank=True, verbose_name="Statut IC")
    ic_integre = models.BooleanField(default=False, verbose_name="Intégré IC")
    override_statut_ic = models.BooleanField(
        default=False,
        verbose_name="Statut IC modifié manuellement"
    )
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

    # Champs anomalie (depuis le fichier Anomalies_BR_ASTEN_IC.csv)
    en_anomalie = models.BooleanField(default=False, verbose_name="En anomalie")
    nom_fichier_integ = models.CharField(max_length=255, null=True, blank=True, verbose_name="Nom fichier intégration")
    type_mouvement = models.CharField(max_length=10, null=True, blank=True, verbose_name="Type (1=BR, 2=Retour)")
    fournisseur_anomalie = models.CharField(max_length=255, null=True, blank=True, verbose_name="Fournisseur (anomalie)")
    montant_ht_anomalie = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True, verbose_name="Montant HT (anomalie)")
    rejet_csm_entete = models.CharField(max_length=500, null=True, blank=True, verbose_name="Rejet CSM entête")
    rejet_csm_detail = models.CharField(max_length=500, null=True, blank=True, verbose_name="Rejet CSM détail article")
    rejet_ic_entete = models.CharField(max_length=500, null=True, blank=True, verbose_name="Rejet IC entête")
    facture_anomalie = models.CharField(max_length=10, null=True, blank=True, verbose_name="Facture (OUI/NON)")

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


class BRICLigne(models.Model):
    """Une ligne de réception/retour provenant du fichier BR IC (Sage IC).
    Importée en base jour par jour — remplace la lecture fichier à la volée."""
    numero_br = models.CharField(max_length=50, verbose_name="N° Réc./Ret.")
    commande_fournisseur = models.CharField(max_length=100, verbose_name="N° Cde fournisseur")
    date_reception = models.DateField(verbose_name="Date réception")
    fichier_source = models.CharField(max_length=255, verbose_name="Fichier source")
    date_import = models.DateTimeField(auto_now_add=True, verbose_name="Date d'import")

    class Meta:
        verbose_name = "BR IC Ligne"
        verbose_name_plural = "BR IC Lignes"
        unique_together = [['numero_br', 'commande_fournisseur', 'date_reception']]
        indexes = [
            models.Index(fields=['numero_br', 'commande_fournisseur', 'date_reception']),
            models.Index(fields=['date_reception']),
        ]
        ordering = ['-date_reception', 'numero_br']

    def __str__(self):
        return f"BR IC - {self.numero_br} - {self.date_reception}"

