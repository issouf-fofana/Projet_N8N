from django.db.models.signals import post_save
from django.contrib.auth.models import User
from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver
from django.utils import timezone
from .models import UserProfile


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        role = 'superadmin' if instance.is_superuser else 'user'
        UserProfile.objects.get_or_create(user=instance, defaults={'role': role})


@receiver(user_logged_in)
def mettre_a_jour_derniere_connexion(sender, request, user, **kwargs):
    User.objects.filter(pk=user.pk).update(last_login=timezone.now())
