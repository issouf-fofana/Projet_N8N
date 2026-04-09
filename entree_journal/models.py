from django.db import models


class FichierImporte(models.Model):
    """Suivi des fichiers CSV déjà importés."""
    nom_fichier  = models.CharField(max_length=255, unique=True)
    pos_id       = models.CharField(max_length=20)
    nb_lignes    = models.IntegerField(default=0)
    importe_le   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-importe_le']
        verbose_name = "Fichier importé"

    def __str__(self):
        return self.nom_fichier


class EntreeJournal(models.Model):
    """Une entrée du journal POS (import de données)."""

    STATUS_CHOICES = [
        (0, 'Erreur'),
        (1, 'En cours'),
        (2, 'Succès'),
        (3, 'Indéfini'),
    ]

    # Identifiant unique de l'entrée (UUID depuis le POS)
    entry_id     = models.CharField(max_length=36, unique=True, db_index=True)

    # Infos POS
    pos_id       = models.CharField(max_length=20, db_index=True)
    pos_name     = models.CharField(max_length=100)

    # Dates
    created_at   = models.DateTimeField(db_index=True)
    updated_at   = models.DateTimeField(null=True, blank=True)
    collected_at = models.DateTimeField()

    # Type d'entrée
    entry_type_value       = models.IntegerField(default=30)
    entry_type_label       = models.CharField(max_length=100)
    entry_type_detail_type = models.CharField(max_length=100, db_index=True)
    entry_type_detail_text = models.CharField(max_length=200)

    # Statut
    status_value = models.IntegerField(choices=STATUS_CHOICES, default=2)
    status_label = models.CharField(max_length=50)

    # Utilisateur & rapport
    username     = models.CharField(max_length=100, blank=True)
    report       = models.TextField(blank=True)

    # Magasin
    shop_reference = models.CharField(max_length=20, db_index=True)
    shop_name      = models.CharField(max_length=200)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['created_at', 'shop_reference', 'entry_type_detail_type']),
            models.Index(fields=['pos_id', 'created_at']),
        ]
        verbose_name = "Entrée journal"

    def __str__(self):
        return f"{self.pos_id} | {self.shop_reference} | {self.entry_type_detail_type} | {self.status_label}"
