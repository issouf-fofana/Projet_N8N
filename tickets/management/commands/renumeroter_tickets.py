from django.core.management.base import BaseCommand
from tickets.models import Ticket


class Command(BaseCommand):
    help = 'Renumérote tous les tickets avec des numéros séquentiels simples (1, 2, 3...)'

    def handle(self, *args, **options):
        tickets = Ticket.objects.all().order_by('id')
        numero = 1
        
        for ticket in tickets:
            ticket.numero_ticket = str(numero)
            ticket.save(update_fields=['numero_ticket'])
            self.stdout.write(
                self.style.SUCCESS(f'Ticket ID {ticket.id} → #{numero}')
            )
            numero += 1
        
        self.stdout.write(
            self.style.SUCCESS(f'\n✅ {numero - 1} tickets renumerotés avec succès!')
        )






