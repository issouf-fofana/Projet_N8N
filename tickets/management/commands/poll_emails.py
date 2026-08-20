"""
Commande Django : python manage.py poll_emails
Lance le polling IMAP et crée les remontées automatiquement.

Usage :
  python manage.py poll_emails
  python manage.py poll_emails --host imap.gmail.com --port 993 --user mon@mail.com --password monmotdepasse

Les paramètres peuvent aussi être définis dans config.env :
  IMAP_HOST, IMAP_PORT, IMAP_USER, IMAP_PASSWORD, IMAP_FOLDER
"""

from django.core.management.base import BaseCommand
from django.conf import settings
from tickets.email_pipeline import process_imap_inbox


class Command(BaseCommand):
    help = "Polling IMAP → création automatique des remontées"

    def add_arguments(self, parser):
        parser.add_argument("--host",     default=getattr(settings, "IMAP_HOST", ""))
        parser.add_argument("--port",     type=int, default=getattr(settings, "IMAP_PORT", 993))
        parser.add_argument("--user",     default=getattr(settings, "IMAP_USER", ""))
        parser.add_argument("--password", default=getattr(settings, "IMAP_PASSWORD", ""))
        parser.add_argument("--folder",   default=getattr(settings, "IMAP_FOLDER", "INBOX"))
        parser.add_argument("--no-ssl",   action="store_true")

    def handle(self, *args, **options):
        host = options["host"]
        port = options["port"]
        user = options["user"]
        password = options["password"]
        folder = options["folder"]
        use_ssl = not options["no_ssl"]

        if not host or not user or not password:
            self.stderr.write(self.style.ERROR(
                "Paramètres IMAP manquants. Définissez IMAP_HOST, IMAP_USER, IMAP_PASSWORD "
                "dans config.env ou passez --host --user --password"
            ))
            return

        self.stdout.write(f"Connexion IMAP → {user}@{host}:{port} ({folder})")

        stats = process_imap_inbox(
            host=host, port=port,
            username=user, password=password,
            use_ssl=use_ssl, folder=folder,
        )

        self.stdout.write(self.style.SUCCESS(
            f"✓ {stats['traites']} mail(s) traité(s) — "
            f"{stats['crees']} remontée(s) créée(s) — "
            f"{stats['erreurs']} erreur(s)"
        ))

        for d in stats.get("details", []):
            if "erreur" in d:
                self.stdout.write(self.style.ERROR(f"  ✗ {d['erreur']}"))
            else:
                flag = "✓" if d["magasin"] != "NON IDENTIFIÉ" else "⚠"
                self.stdout.write(f"  {flag} #{d['ticket']} | {d['magasin']} | {d['statut']}")

        if "erreur_connexion" in stats:
            self.stderr.write(self.style.ERROR(f"Erreur connexion : {stats['erreur_connexion']}"))
