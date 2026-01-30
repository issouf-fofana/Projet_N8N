from django.core.management.base import BaseCommand
from br.models import BRAsten
from django.db.models import Q


class Command(BaseCommand):
    help = 'Affiche le nombre de BR non intégrés'

    def handle(self, *args, **options):
        # Compter tous les BR
        total = BRAsten.objects.count()

        # Compter les BR non intégrés (en excluant les "Quantité 0")
        non_integres = BRAsten.objects.filter(ic_integre=False).exclude(
            Q(statut_ic__icontains='Quantité 0') | 
            Q(statut_ic__icontains='quantite_0') |
            Q(statut_ic__icontains='Quantite 0')
        ).count()

        # Compter les BR intégrés (en excluant les "Quantité 0")
        integres = BRAsten.objects.filter(ic_integre=True).exclude(
            Q(statut_ic__icontains='Quantité 0') | 
            Q(statut_ic__icontains='quantite_0') |
            Q(statut_ic__icontains='Quantite 0')
        ).count()

        # Compter les BR "Quantité 0" (exclus des statistiques)
        quantite_0 = BRAsten.objects.filter(
            Q(statut_ic__icontains='Quantité 0') | 
            Q(statut_ic__icontains='quantite_0') |
            Q(statut_ic__icontains='Quantite 0')
        ).count()

        # Afficher les résultats
        self.stdout.write("=" * 50)
        self.stdout.write(self.style.SUCCESS("STATISTIQUES DES BR ASTEN"))
        self.stdout.write("=" * 50)
        self.stdout.write(f"Total BR: {total}")
        self.stdout.write(self.style.SUCCESS(f"BR Intégrés: {integres}"))
        self.stdout.write(self.style.ERROR(f"BR Non Intégrés: {non_integres}"))
        self.stdout.write(f"BR Quantité 0 (exclus): {quantite_0}")
        self.stdout.write("=" * 50)

        # Calculer les pourcentages
        if total > quantite_0:
            total_pour_stats = total - quantite_0
            taux_integration = round((integres / total_pour_stats * 100) if total_pour_stats > 0 else 0, 2)
            taux_non_integration = round((non_integres / total_pour_stats * 100) if total_pour_stats > 0 else 0, 2)
            self.stdout.write(f"Taux d'intégration: {taux_integration}%")
            self.stdout.write(f"Taux de non-intégration: {taux_non_integration}%")
        self.stdout.write("=" * 50)


