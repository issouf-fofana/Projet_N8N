from django.db.models.signals import post_save
from django.contrib.auth.models import User
from django.dispatch import receiver
from .models import UserProfile


@receiver(post_save, sender=User)
def create_user_profile(sender, instance, created, **kwargs):
    if created:
        role = 'superadmin' if instance.is_superuser else 'user'
        UserProfile.objects.get_or_create(user=instance, defaults={'role': role})
