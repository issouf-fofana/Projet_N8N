"""
Signaux Django — invalide le cache conformité quand un contrôle est sauvegardé.
Le pre-calcul se fait en arrière-plan via un thread pour ne pas bloquer l'écriture.
"""
import threading
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver


def _precalculer_cache():
    """Recalcule et met en cache les stats conformité (lancé en thread)."""
    try:
        from django.core.cache import cache
        from entree_journal.models import ControleConformite, ArticleConformite
        from django.db.models import Max, Sum, Count

        derniers_ids = list(
            ControleConformite.objects.values('magasin')
                .annotate(last_id=Max('id')).values_list('last_id', flat=True)
        )
        controles = ControleConformite.objects.filter(id__in=derniers_ids)

        # Stats globales
        stats = controles.aggregate(total=Sum('total'), ok=Sum('ok'), ko=Sum('ko'))
        total_c = stats['total'] or 0
        ok_c    = stats['ok'] or 0
        ko_c    = stats['ko'] or 0
        taux_c  = round(ok_c / total_c * 100, 1) if total_c else 0
        dernier = controles.order_by('-date_controle').first()

        # Compteurs motifs globaux
        motifs_ko = dict(
            ArticleConformite.objects.filter(controle_id__in=derniers_ids, resultat='KO')
                .values('motif').annotate(n=Count('id')).values_list('motif', 'n')
        )
        conformite_stats = {
            'total': total_c, 'ok': ok_c, 'ko': ko_c, 'taux': taux_c,
            'date': dernier.date_controle.strftime('%d/%m/%Y %H:%M') if dernier else '',
            'nb_non_cmd_asten': motifs_ko.get('Non commandable Asten', 0) + motifs_ko.get('Article absent dans Asten', 0),
            'nb_statut_na':     motifs_ko.get('Statut NA', 0) + motifs_ko.get('Statut Product NA', 0),
            'nb_statut_es':     motifs_ko.get('Statut ES', 0) + motifs_ko.get('Statut Product ES', 0),
            'nb_nc_ld':         motifs_ko.get('NC/LD', 0) + motifs_ko.get('Article NC avec CODE MAK LD', 0),
        }
        cache.set('conformite_stats', conformite_stats, timeout=3600)

        # Rapports par magasin
        rapports_ac = []
        for c in controles.order_by('-date_controle'):
            motifs = dict(
                ArticleConformite.objects.filter(controle=c, resultat='KO')
                    .values('motif').annotate(n=Count('id')).values_list('motif', 'n')
            )
            rapports_ac.append({
                "filename": "",
                "magasin":  c.magasin,
                "date":     c.date_controle.strftime('%Y%m%d_%H%M%S'),
                "total":    c.total,
                "ok":       c.ok,
                "ko":       c.ko,
                "taux":     c.taux_conformite,
                "nb_non_cmd_asten": motifs.get("Non commandable Asten", 0) + motifs.get("Article absent dans Asten", 0),
                "nb_statut_na":     motifs.get("Statut NA", 0) + motifs.get("Statut Product NA", 0),
                "nb_statut_es":     motifs.get("Statut ES", 0) + motifs.get("Statut Product ES", 0),
                "nb_nc_ld":         motifs.get("NC/LD", 0) + motifs.get("Article NC avec CODE MAK LD", 0),
            })
        cache.set('conformite_rapports_ac', rapports_ac, timeout=3600)

        # Invalider le cache semaine_comparison pour forcer recalcul
        for periode in ('semaine', 'mois', '3mois', '6mois', 'annee'):
            cache.delete(f'semaine_comparison_{periode}')

    except Exception:
        pass


def _invalidate_and_refresh():
    """Invalide immédiatement puis recalcule en arrière-plan."""
    from django.core.cache import cache
    cache.delete('conformite_stats')
    cache.delete('conformite_rapports_ac')
    t = threading.Thread(target=_precalculer_cache, daemon=True)
    t.start()


@receiver(post_save, sender='entree_journal.ControleConformite')
def on_controle_save(sender, instance, **kwargs):
    _invalidate_and_refresh()


@receiver(post_delete, sender='entree_journal.ControleConformite')
def on_controle_delete(sender, instance, **kwargs):
    _invalidate_and_refresh()
