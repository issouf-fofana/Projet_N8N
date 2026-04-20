from django.db import models
from django.contrib.auth.models import User


class Magasin(models.Model):
    """Modèle représentant un magasin"""
    code = models.CharField(max_length=10, unique=True, primary_key=True, verbose_name="Code magasin")
    nom = models.CharField(max_length=200, verbose_name="Nom du magasin")
    date_creation = models.DateTimeField(auto_now_add=True, verbose_name="Date de création")
    date_modification = models.DateTimeField(auto_now=True, verbose_name="Date de modification")

    class Meta:
        verbose_name = "Magasin"
        verbose_name_plural = "Magasins"
        ordering = ['code']

    def __str__(self):
        return f"{self.code} - {self.nom}"


class AppPermission(models.Model):
    """Permissions disponibles dans l'application."""
    PERMISSIONS = [
        ('actualiser_importer',  'Actualiser / Importer des fichiers'),
        ('modifier_statuts',     'Modifier les statuts des écarts et factures'),
        ('gerer_magasins',       'Gérer les magasins'),
        ('configurer_systeme',   'Configurer le système'),
        ('gerer_utilisateurs',   'Gérer les utilisateurs'),
        ('supprimer_donnees',    'Supprimer des données'),
    ]
    code = models.CharField(max_length=50, unique=True)
    label = models.CharField(max_length=200)
    description = models.TextField(blank=True, default='')
    ordre = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['ordre', 'code']
        verbose_name = "Permission"
        verbose_name_plural = "Permissions"

    def __str__(self):
        return self.label


class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('user', 'Utilisateur'),
        ('admin', 'Administrateur'),
        ('superadmin', 'Super-Administrateur'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='user')
    permissions = models.ManyToManyField(AppPermission, blank=True, related_name='profiles')
    must_change_password = models.BooleanField(default=False)

    class Meta:
        verbose_name = "Profil utilisateur"
        verbose_name_plural = "Profils utilisateurs"

    def __str__(self):
        return f"{self.user.username} ({self.get_role_display()})"

    def has_perm(self, code):
        """Vérifie si l'utilisateur a une permission spécifique.
        Le superadmin a toujours toutes les permissions."""
        if self.role == 'superadmin':
            return True
        return self.permissions.filter(code=code).exists()

    def is_admin_or_above(self):
        return self.role in ('admin', 'superadmin')

    def is_superadmin(self):
        return self.role == 'superadmin'
