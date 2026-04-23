from django.db import models
from django.db.models import JSONField


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


# ─── Snapshots versions Asten (sauvegarde depuis SMB → base de données) ────────

class VersionAstenSnap(models.Model):
    """
    Snapshot d'une version prdP2A (dossier SMB) sauvegardée en base.
    Permet de consulter les données même quand le SMB est déconnecté.
    """
    nom          = models.CharField(max_length=120, unique=True, verbose_name="Nom du dossier")
    date         = models.DateField(verbose_name="Date de génération")
    heure        = models.CharField(max_length=10, verbose_name="Heure")
    statut       = models.CharField(max_length=20, verbose_name="Statut global")  # ok/warning/error
    conformite_pct = models.IntegerField(default=0, verbose_name="Conformité %")
    nb_fichiers_total = models.IntegerField(default=0, verbose_name="Nb fichiers total")
    taille_raw   = models.BigIntegerField(default=0, verbose_name="Taille (octets)")
    taille_fmt   = models.CharField(max_length=20, default='', verbose_name="Taille formatée")
    nb_ok        = models.IntegerField(default=0, verbose_name="Assortiments OK")
    nb_incomplet = models.IntegerField(default=0, verbose_name="Assortiments incomplets")
    nb_absent    = models.IntegerField(default=0, verbose_name="Assortiments absents")
    assortiments_manquants = JSONField(default=list, verbose_name="Codes assortiments manquants")
    assortiments_extra     = JSONField(default=list, verbose_name="Codes assortiments extra")
    synced_at    = models.DateTimeField(auto_now=True, verbose_name="Dernière sync")

    class Meta:
        verbose_name = "Version Asten (snap)"
        verbose_name_plural = "Versions Asten (snaps)"
        ordering = ['-date', '-heure']

    def __str__(self):
        return self.nom


class AssortimentVersionSnap(models.Model):
    """
    Détail d'un assortiment au sein d'un snapshot de version.
    Les fichiers sont stockés en JSON pour éviter une troisième table.
    """
    version              = models.ForeignKey(VersionAstenSnap, on_delete=models.CASCADE,
                                             related_name='assortiments')
    code                 = models.CharField(max_length=20, verbose_name="Code assortiment")
    statut               = models.CharField(max_length=20, verbose_name="Statut")  # ok/incomplet/absent
    nb_fichiers_presents = models.IntegerField(default=0)
    nb_manquants         = models.IntegerField(default=0)
    total_articles       = models.IntegerField(default=0)
    fichiers             = JSONField(default=list, verbose_name="Détail fichiers")

    class Meta:
        verbose_name = "Assortiment (snap)"
        verbose_name_plural = "Assortiments (snaps)"
        unique_together = ('version', 'code')
        ordering = ['code']

    def __str__(self):
        return f"{self.version.nom} / {self.code}"


class FactureCyrusLigne(models.Model):
    """Une ligne article du fichier CSV Cyrus (RUN58489…csv)."""
    cle_facture = models.CharField(max_length=50, db_index=True)
    nsee        = models.CharField(max_length=10)
    nfac        = models.CharField(max_length=20)
    dfac_str    = models.CharField(max_length=10)
    dfac_date   = models.DateField(null=True, blank=True)
    cidc        = models.CharField(max_length=10, db_index=True)
    lart        = models.CharField(max_length=255, blank=True, default='')
    nart        = models.CharField(max_length=50, blank=True, default='')
    qlvu        = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    pvtc        = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    ptvc        = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    pfth        = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    fichier     = models.CharField(max_length=255)
    date_import = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ligne facture Cyrus"
        indexes = [models.Index(fields=['cle_facture', 'cidc'])]


class FactureAstenLigne(models.Model):
    """Une ligne du CSV Asten (receptions_centrales…csv)."""
    n_bon_livraison     = models.CharField(max_length=50, db_index=True)
    magasin             = models.CharField(max_length=20, db_index=True)
    fournisseur         = models.CharField(max_length=255, blank=True, default='')
    statut_commande     = models.CharField(max_length=100, blank=True, default='')
    date_reception_str  = models.CharField(max_length=30, blank=True, default='')
    date_reception_date = models.DateField(null=True, blank=True)
    quantite_totale     = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    valorisation_ht     = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    valorisation_ttc    = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    type_reception      = models.CharField(max_length=50, blank=True, default='')
    fichier             = models.CharField(max_length=255)
    date_import         = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Ligne facture Asten"
        indexes = [models.Index(fields=['n_bon_livraison', 'magasin'])]


class FactureEcartStatut(models.Model):
    """
    Statut manuel pour les factures Cyrus en écart (non trouvées dans Asten).
    Identifiée par (cle_facture, dfac_str, cidc) — clé unique de la facture Cyrus.
    """
    STATUT_CHOICES = [
        ('non_integre', 'Non intégré'),
        ('integre',     'Intégré'),
        ('ignore',      'Ignoré'),
    ]

    cle_facture = models.CharField(max_length=50, verbose_name="Clé facture")
    dfac_str    = models.CharField(max_length=10, verbose_name="Date DFAC (YYMMDD)")
    cidc        = models.CharField(max_length=10, verbose_name="Code magasin (CIDC)")
    statut      = models.CharField(
        max_length=15,
        choices=STATUT_CHOICES,
        default='non_integre',
        verbose_name="Statut"
    )
    note        = models.TextField(blank=True, default='', verbose_name="Note")
    date_modif  = models.DateTimeField(auto_now=True, verbose_name="Dernière modification")

    class Meta:
        verbose_name = "Statut facture en écart"
        verbose_name_plural = "Statuts factures en écart"
        unique_together = ('cle_facture', 'dfac_str', 'cidc')
        ordering = ['-date_modif']

    def __str__(self):
        return f"{self.cle_facture} / {self.cidc} → {self.statut}"
