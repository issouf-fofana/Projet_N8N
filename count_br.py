#!/usr/bin/env python
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'verification_commande.settings')
django.setup()

from br.models import BRAsten
from django.db.models import Q

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
print("=" * 50)
print("STATISTIQUES DES BR ASTEN")
print("=" * 50)
print(f"Total BR: {total}")
print(f"BR Intégrés: {integres}")
print(f"BR Non Intégrés: {non_integres}")
print(f"BR Quantité 0 (exclus): {quantite_0}")
print("=" * 50)

# Calculer les pourcentages
if total > quantite_0:
    total_pour_stats = total - quantite_0
    taux_integration = round((integres / total_pour_stats * 100) if total_pour_stats > 0 else 0, 2)
    taux_non_integration = round((non_integres / total_pour_stats * 100) if total_pour_stats > 0 else 0, 2)
    print(f"Taux d'intégration: {taux_integration}%")
    print(f"Taux de non-intégration: {taux_non_integration}%")
print("=" * 50)


