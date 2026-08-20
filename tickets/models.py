import os
from django.db import models
from django.utils import timezone
from core.models import Magasin


class CompteEmail(models.Model):
    """Compte Outlook 365 connecté via OAuth2 Microsoft Graph."""
    label = models.CharField(max_length=150, default="Boîte support")
    client_id = models.CharField(max_length=200, blank=True)
    client_secret = models.CharField(max_length=500, blank=True)
    tenant_id = models.CharField(max_length=200, blank=True)
    refresh_token = models.TextField(blank=True)
    delta_link = models.TextField(blank=True)  # curseur polling Graph delta
    is_active = models.BooleanField(default=False)
    last_sync = models.DateTimeField(null=True, blank=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Compte email"
        verbose_name_plural = "Comptes email"

    def __str__(self):
        return self.label

    @property
    def est_connecte(self):
        return bool(self.refresh_token)


class Technicien(models.Model):
    nom = models.CharField(max_length=150, unique=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Technicien"
        verbose_name_plural = "Techniciens"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class TicketCategorie(models.Model):
    nom = models.CharField(max_length=100, unique=True)
    actif = models.BooleanField(default=True)
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Catégorie de ticket"
        verbose_name_plural = "Catégories de tickets"
        ordering = ["nom"]

    def __str__(self):
        return self.nom


class Ticket(models.Model):
    TYPE_INCIDENT = "incident"
    TYPE_DEMANDE = "demande"
    TYPE_CHOICES = [
        (TYPE_INCIDENT, "Incident"),
        (TYPE_DEMANDE, "Demande"),
    ]

    STATUT_NOUVEAU = "nouveau"
    STATUT_EN_COURS = "en_cours"
    STATUT_EN_ATTENTE = "en_attente"
    STATUT_RESOLU = "resolu"
    STATUT_FERME = "ferme"
    STATUT_CHOICES = [
        (STATUT_NOUVEAU, "Nouveau"),
        (STATUT_EN_COURS, "En cours"),
        (STATUT_EN_ATTENTE, "En attente"),
        (STATUT_RESOLU, "Résolu"),
        (STATUT_FERME, "Fermé"),
    ]

    NIVEAU_TRES_BAS = "tres_basse"
    NIVEAU_BAS = "basse"
    NIVEAU_MOYEN = "moyenne"
    NIVEAU_HAUT = "haute"
    NIVEAU_CHOICES = [
        (NIVEAU_TRES_BAS, "Très basse"),
        (NIVEAU_BAS, "Basse"),
        (NIVEAU_MOYEN, "Moyenne"),
        (NIVEAU_HAUT, "Haute"),
    ]

    numero_ticket = models.CharField(max_length=30, unique=True, blank=True)
    type_demande = models.CharField(max_length=20, choices=TYPE_CHOICES)
    categorie = models.ForeignKey(
        TicketCategorie, on_delete=models.PROTECT, related_name="tickets", null=True, blank=True
    )
    statut = models.CharField(max_length=20, choices=STATUT_CHOICES, default=STATUT_NOUVEAU)
    urgence = models.CharField(max_length=20, choices=NIVEAU_CHOICES)
    impact = models.CharField(max_length=20, choices=NIVEAU_CHOICES)
    magasin = models.ForeignKey(Magasin, on_delete=models.PROTECT, related_name="tickets")
    demandeur = models.CharField(max_length=150, blank=True, default="")
    observateurs = models.ManyToManyField(Technicien, blank=True, related_name="tickets_observes")
    assigne_a = models.ManyToManyField(Technicien, blank=True, related_name="tickets_assignes")
    description = models.TextField(blank=True, default="")
    date_creation = models.DateTimeField(auto_now_add=True)
    date_mise_a_jour = models.DateTimeField(auto_now=True)
    date_fermeture = models.DateTimeField(null=True, blank=True)

    # ── Traçabilité email Outlook ──
    source_email = models.EmailField(blank=True, default="")          # expéditeur du mail d'origine
    outlook_conversation_id = models.CharField(max_length=500, blank=True, db_index=True)
    outlook_message_id = models.CharField(max_length=500, blank=True)  # id du dernier mail reçu (pour répondre dans le fil)
    cree_par_email = models.BooleanField(default=False)                # True = créé automatiquement via mail
    magasin_non_identifie = models.BooleanField(default=False)        # True = IA n'a pas trouvé le magasin

    class Meta:
        verbose_name = "Ticket"
        verbose_name_plural = "Tickets"
        ordering = ["-date_mise_a_jour"]

    def __str__(self):
        return self.numero_ticket or f"Ticket #{self.pk}"

    def save(self, *args, **kwargs):
        creating = self.pk is None
        super().save(*args, **kwargs)
        if creating and not self.numero_ticket:
            # Générer un numéro séquentiel simple (1, 2, 3...)
            # Trouver le numéro maximum existant et ajouter 1
            tickets_existants = Ticket.objects.exclude(pk=self.pk).exclude(numero_ticket__isnull=True).exclude(numero_ticket='')
            max_numero = 0
            for ticket in tickets_existants:
                try:
                    # Essayer de convertir le numéro en entier
                    num = int(ticket.numero_ticket)
                    if num > max_numero:
                        max_numero = num
                except (ValueError, TypeError):
                    # Si le numéro n'est pas un nombre, ignorer
                    pass
            numero = str(max_numero + 1)
            Ticket.objects.filter(pk=self.pk).update(numero_ticket=numero)
            self.numero_ticket = numero

    def set_statut(self, nouveau_statut, utilisateur=""):
        ancien_statut = self.statut
        if ancien_statut == nouveau_statut:
            return
        self.statut = nouveau_statut
        if nouveau_statut in {self.STATUT_RESOLU, self.STATUT_FERME}:
            self.date_fermeture = timezone.now()
        else:
            self.date_fermeture = None
        self.save(update_fields=["statut", "date_fermeture", "date_mise_a_jour"])
        HistoriqueStatut.objects.create(
            ticket=self,
            ancien_statut=ancien_statut,
            nouveau_statut=nouveau_statut,
            utilisateur=utilisateur or "",
        )


class HistoriqueStatut(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="historiques_statut")
    ancien_statut = models.CharField(max_length=20, blank=True)
    nouveau_statut = models.CharField(max_length=20)
    utilisateur = models.CharField(max_length=150, blank=True, default="")
    date_changement = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Historique de statut"
        verbose_name_plural = "Historiques de statut"
        ordering = ["-date_changement"]

    def __str__(self):
        return f"{self.ticket} {self.ancien_statut} → {self.nouveau_statut}"


class SuiviTicket(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name="suivis")
    auteur = models.CharField(max_length=150, blank=True, default="")
    message = models.TextField()
    date_creation = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Suivi de ticket"
        verbose_name_plural = "Suivis de tickets"
        ordering = ["-date_creation"]

    def __str__(self):
        return f"Suivi #{self.pk} - {self.ticket}"


def chemin_piece_jointe(instance, filename):
    return os.path.join("tickets", "suivis", timezone.now().strftime("%Y/%m/%d"), filename)


class PieceJointe(models.Model):
    TYPE_IMAGE = "image"
    TYPE_VIDEO = "video"
    TYPE_PDF = "pdf"
    TYPE_WORD = "word"
    TYPE_EXCEL = "excel"
    TYPE_AUTRE = "autre"
    TYPE_CHOICES = [
        (TYPE_IMAGE, "Image"),
        (TYPE_VIDEO, "Vidéo"),
        (TYPE_PDF, "PDF"),
        (TYPE_WORD, "Word"),
        (TYPE_EXCEL, "Excel"),
        (TYPE_AUTRE, "Autre"),
    ]

    suivi = models.ForeignKey(SuiviTicket, on_delete=models.CASCADE, related_name="pieces_jointes")
    fichier = models.FileField(upload_to=chemin_piece_jointe)
    type_fichier = models.CharField(max_length=20, choices=TYPE_CHOICES, default=TYPE_AUTRE)
    date_upload = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Pièce jointe"
        verbose_name_plural = "Pièces jointes"
        ordering = ["-date_upload"]

    def __str__(self):
        return os.path.basename(self.fichier.name)

    def save(self, *args, **kwargs):
        if self.fichier and (not self.type_fichier or self.type_fichier == self.TYPE_AUTRE):
            self.type_fichier = self.deduire_type_fichier()
        super().save(*args, **kwargs)

    def deduire_type_fichier(self):
        extension = os.path.splitext(self.fichier.name)[1].lower()
        if extension in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
            return self.TYPE_IMAGE
        if extension in {".mp4", ".avi", ".mkv", ".mov", ".webm"}:
            return self.TYPE_VIDEO
        if extension == ".pdf":
            return self.TYPE_PDF
        if extension in {".doc", ".docx"}:
            return self.TYPE_WORD
        if extension in {".xls", ".xlsx", ".csv"}:
            return self.TYPE_EXCEL
        return self.TYPE_AUTRE

    @property
    def est_image(self):
        extension = os.path.splitext(self.fichier.name)[1].lower()
        return extension in {".jpg", ".jpeg", ".png", ".gif", ".webp"}

