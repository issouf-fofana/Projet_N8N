import json
import os
from django.core.management.base import BaseCommand
from core.models import Magasin


class Command(BaseCommand):
    help = 'Charge les magasins depuis le fichier magasin.json'

    def handle(self, *args, **options):
        # Chemin vers le fichier magasin.json
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        json_file = os.path.join(base_dir, 'magasin.json')
        
        if not os.path.exists(json_file):
            self.stdout.write(self.style.ERROR(f'Fichier {json_file} introuvable'))
            return
        
        try:
            with open(json_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            created_count = 0
            updated_count = 0
            
            for code, info in data.items():
                magasin, created = Magasin.objects.update_or_create(
                    code=code,
                    defaults={
                        'nom': info.get('name', '')
                    }
                )
                
                if created:
                    created_count += 1
                    self.stdout.write(self.style.SUCCESS(f'✓ Magasin créé: {code} - {magasin.nom}'))
                else:
                    updated_count += 1
                    self.stdout.write(self.style.WARNING(f'↻ Magasin mis à jour: {code} - {magasin.nom}'))
            
            self.stdout.write(self.style.SUCCESS(
                f'\nTerminé ! {created_count} créé(s), {updated_count} mis à jour(s).'
            ))
            
        except Exception as e:
            self.stdout.write(self.style.ERROR(f'Erreur lors du chargement: {str(e)}'))


