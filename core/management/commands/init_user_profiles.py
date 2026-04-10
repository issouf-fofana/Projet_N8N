from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from core.models import UserProfile


class Command(BaseCommand):
    help = 'Crée les UserProfile manquants pour les utilisateurs existants'

    def handle(self, *args, **kwargs):
        for user in User.objects.all():
            profile, created = UserProfile.objects.get_or_create(user=user)
            if created:
                if user.is_superuser:
                    profile.role = 'superadmin'
                    profile.save()
                self.stdout.write(self.style.SUCCESS(
                    f"Profil créé : {user.username} → {profile.role}"
                ))
            else:
                self.stdout.write(f"Profil existant : {user.username} → {profile.role}")
