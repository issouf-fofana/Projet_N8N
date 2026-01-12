import os
from django.core.management.base import BaseCommand
from traitement.models import Controle, Ecart, FichierSource
from django.conf import settings
import logging

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Nettoie toutes les comparaisons enregistrées dans la base de données'

    def add_arguments(self, parser):
        parser.add_argument(
            '--confirm',
            action='store_true',
            help='Confirmer la suppression (obligatoire pour exécuter)',
        )
        parser.add_argument(
            '--type',
            type=str,
            choices=['commande', 'facture', 'br', 'legend', 'all'],
            default='all',
            help='Type de contrôle à supprimer (default: all)',
        )

    def handle(self, *args, **options):
        confirm = options['confirm']
        type_controle = options['type']
        
        if not confirm:
            self.stdout.write(
                self.style.WARNING(
                    '⚠️  ATTENTION : Cette commande va supprimer toutes les données de comparaison !\n'
                    'Pour confirmer, utilisez : python manage.py clean_database --confirm'
                )
            )
            return
        
        self.stdout.write(self.style.WARNING('🗑️  Début du nettoyage de la base de données...'))
        
        # Compter les enregistrements avant suppression
        if type_controle == 'all':
            total_ecarts = Ecart.objects.all().count()
            total_controles = Controle.objects.all().count()
            total_fichiers = FichierSource.objects.all().count()
        else:
            total_ecarts = Ecart.objects.filter(controle__type_controle=type_controle).count()
            total_controles = Controle.objects.filter(type_controle=type_controle).count()
            total_fichiers = FichierSource.objects.filter(type_controle=type_controle).count()
        
        self.stdout.write(f'  📊 Statistiques avant suppression :')
        self.stdout.write(f'     • Contrôles : {total_controles}')
        self.stdout.write(f'     • Écarts : {total_ecarts}')
        self.stdout.write(f'     • Fichiers sources : {total_fichiers}')
        
        # Supprimer les écarts
        if type_controle == 'all':
            ecarts_deleted, _ = Ecart.objects.all().delete()
        else:
            ecarts_deleted, _ = Ecart.objects.filter(controle__type_controle=type_controle).delete()
        
        self.stdout.write(self.style.SUCCESS(f'  ✅ {ecarts_deleted} écarts supprimés'))
        
        # Supprimer les fichiers sources
        if type_controle == 'all':
            fichiers_deleted, _ = FichierSource.objects.all().delete()
        else:
            fichiers_deleted, _ = FichierSource.objects.filter(type_controle=type_controle).delete()
        
        self.stdout.write(self.style.SUCCESS(f'  ✅ {fichiers_deleted} fichiers sources supprimés'))
        
        # Supprimer les contrôles
        if type_controle == 'all':
            controles_deleted, _ = Controle.objects.all().delete()
        else:
            controles_deleted, _ = Controle.objects.filter(type_controle=type_controle).delete()
        
        self.stdout.write(self.style.SUCCESS(f'  ✅ {controles_deleted} contrôles supprimés'))
        
        # Vérifier les enregistrements restants
        remaining_controles = Controle.objects.all().count()
        remaining_ecarts = Ecart.objects.all().count()
        remaining_fichiers = FichierSource.objects.all().count()
        
        self.stdout.write(self.style.SUCCESS(
            f'\n🎉 Nettoyage terminé avec succès !\n'
            f'   • Contrôles restants : {remaining_controles}\n'
            f'   • Écarts restants : {remaining_ecarts}\n'
            f'   • Fichiers restants : {remaining_fichiers}'
        ))

