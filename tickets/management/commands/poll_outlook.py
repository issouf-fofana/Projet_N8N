"""
python manage.py poll_outlook
Lance le polling Graph pour tous les comptes Outlook connectés.
"""
from django.core.management.base import BaseCommand
from tickets.outlook_service import poll_all_accounts


class Command(BaseCommand):
    help = "Polling Microsoft Graph → création/mise à jour des remontées"

    def handle(self, *args, **options):
        self.stdout.write("Polling Microsoft Graph…")
        stats = poll_all_accounts()
        self.stdout.write(self.style.SUCCESS(
            f"✓ {stats['comptes']} compte(s) | "
            f"{stats['messages']} message(s) | "
            f"{stats['crees']} remontée(s) créée(s) | "
            f"{stats['followups']} suivi(s) | "
            f"{stats['erreurs']} erreur(s)"
        ))
