from django.db import models


class ArchiveMensuelle(models.Model):
    """Résumé mensuel des données de détail supprimées par la purge.

    Garde les « infos » (compteurs, montants, répartition) sans conserver
    le détail de chaque ligne — c'est ce qui allège la base.
    """

    SOURCE_CHOICES = [
        ('facture_cyrus',       'Factures Cyrus (lignes)'),
        ('facture_asten',       'Factures Asten (lignes)'),
        ('facture_ecart_statut', 'Statuts factures en écart'),
        ('br_ic',               'BR IC (lignes)'),
        ('br_asten',            'BR Asten'),
        ('br_anomalie',         'BR anomalies'),
        ('entree_journal',      'Journal POS (entrées)'),
    ]

    source      = models.CharField(max_length=40, choices=SOURCE_CHOICES, verbose_name='Source')
    mois        = models.DateField(verbose_name='Mois', help_text='Premier jour du mois concerné')
    nb_lignes   = models.BigIntegerField(default=0, verbose_name='Lignes supprimées')
    details     = models.JSONField(default=dict, blank=True, verbose_name='Résumé détaillé')
    archive_le  = models.DateTimeField(auto_now_add=True, verbose_name='Archivé le')

    class Meta:
        verbose_name = 'Archive mensuelle'
        verbose_name_plural = 'Archives mensuelles'
        unique_together = [('source', 'mois')]
        ordering = ['-mois', 'source']

    def __str__(self):
        return f'{self.get_source_display()} — {self.mois:%m/%Y} ({self.nb_lignes:,} lignes)'


class FichierPurge(models.Model):
    """Marqueur « fichier déjà purgé » : empêche l'auto-import de ré-importer
    un fichier CSV dont toutes les lignes ont été purgées (sinon une simple
    re-copie depuis le SMB recréerait toutes les données anciennes)."""

    TYPE_CHOICES = [
        ('facture_cyrus', 'Factures Cyrus (CSV)'),
        ('facture_asten', 'Factures Asten (CSV)'),
        ('br_anomalie',   'Anomalies BR (CSV)'),
    ]

    type_fichier = models.CharField(max_length=20, choices=TYPE_CHOICES, verbose_name='Type de fichier')
    nom_fichier  = models.CharField(max_length=255, verbose_name='Nom du fichier')
    nb_lignes    = models.BigIntegerField(default=0, verbose_name='Lignes purgées')
    purge_le     = models.DateTimeField(auto_now_add=True, verbose_name='Purgé le')

    class Meta:
        verbose_name = 'Fichier purgé'
        verbose_name_plural = 'Fichiers purgés'
        unique_together = [('type_fichier', 'nom_fichier')]
        ordering = ['-purge_le']

    def __str__(self):
        return f'{self.get_type_fichier_display()} — {self.nom_fichier}'
