from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.models import AppPermission, UserProfile

PERMISSIONS = [
    ('actualiser_importer',  'Actualiser / Importer des fichiers',     'Déclencher l\'import des nouveaux fichiers CSV/Excel',          1),
    ('modifier_statuts',     'Modifier les statuts',                   'Changer le statut des écarts commandes, BR et factures',        2),
    ('gerer_magasins',       'Gérer les magasins',                     'Ajouter, modifier ou supprimer des magasins',                   3),
    ('configurer_systeme',   'Configurer le système',                  'Accéder aux paramètres de configuration',                       4),
    ('gerer_utilisateurs',   'Gérer les utilisateurs',                 'Créer, modifier et supprimer des comptes utilisateurs',         5),
    ('supprimer_donnees',    'Supprimer des données',                  'Supprimer des enregistrements de la base de données',           6),
]

# Permissions par défaut selon le rôle
ROLE_DEFAULTS = {
    'superadmin': ['actualiser_importer', 'modifier_statuts', 'gerer_magasins',
                   'configurer_systeme', 'gerer_utilisateurs', 'supprimer_donnees'],
    'admin':      ['actualiser_importer', 'modifier_statuts', 'gerer_magasins'],
    'user':       [],
}


class Command(BaseCommand):
    help = 'Crée les permissions de l\'application et les assigne aux utilisateurs existants'

    def handle(self, *args, **kwargs):
        # Créer les permissions
        for code, label, desc, ordre in PERMISSIONS:
            perm, created = AppPermission.objects.update_or_create(
                code=code,
                defaults={'label': label, 'description': desc, 'ordre': ordre}
            )
            status = 'créée' if created else 'mise à jour'
            self.stdout.write(f"Permission {status} : {code}")

        # Assigner les permissions par défaut aux utilisateurs existants
        for user in User.objects.all():
            profile, _ = UserProfile.objects.get_or_create(user=user)
            if profile.role == 'superadmin' or user.is_superuser:
                profile.role = 'superadmin'
                profile.save()
                # Le superadmin a tout via has_perm(), pas besoin d'assigner
                self.stdout.write(self.style.SUCCESS(
                    f"{user.username} (superadmin) → toutes permissions automatiques"
                ))
            else:
                codes = ROLE_DEFAULTS.get(profile.role, [])
                perms = AppPermission.objects.filter(code__in=codes)
                profile.permissions.set(perms)
                self.stdout.write(self.style.SUCCESS(
                    f"{user.username} ({profile.role}) → {list(codes)}"
                ))
