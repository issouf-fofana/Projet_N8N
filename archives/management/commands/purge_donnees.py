"""
purge_donnees — Supprime les données de détail de plus de N mois et archive un
résumé mensuel (compteurs, montants, répartition) dans `archives_archivemensuelle`.

But : alléger le serveur. La grosse table `imports_facturecyrusligne` (plusieurs
millions de lignes) est traitée en priorité.

Usage :
    python manage.py purge_donnees                 # rétention 2 mois (défaut)
    python manage.py purge_donnees --mois 3        # rétention 3 mois
    python manage.py purge_donnees --dry-run       # simulation, ne supprime rien
    python manage.py purge_donnees --vider-asten   # vide ENTIÈREMENT les factures Asten (tous les 2 jours)
    python manage.py purge_donnees --no-vacuum-full    # VACUUM ANALYZE simple (pas de récupération disque immédiate)
    python manage.py purge_donnees --no-purge-cache    # ne vide pas la table django_cache (cache DB mort)

Mode --vider-asten :
    Les factures Asten sont rechargées chaque jour depuis le SMB — elles ne
    servent qu'à la vérification immédiate. On vide donc la table entière
    (résumé cumulé archivé, TRUNCATE, puis refresh de mv_factures_joined).

Sécurité :
    - Chaque source est d'abord résumée par mois, PUIS supprimée (en lots de
      20 000 lignes pour ne pas bloquer la base).
    - Si toutes les lignes d'une table sont plus vieilles que la rétention,
      la table est TRUNCATE (instantané) au lieu de milliers de DELETE.
    - `mv_factures_joined` est rafraîchie après la purge des factures.
    - Les fichiers CSV entièrement purgés sont marqués dans `FichierPurge` :
      l'auto-import ne les ré-importera plus même s'ils sont recopiés depuis
      le SMB (sinon la purge serait annulée à l'import suivant).
    - Les commandes Asten/GPV/Legend/Cyrus ne sont PAS purgées : elles sont
      liées aux écarts (FK en cascade) et au recalcul automatique des écarts —
      les purger fausserait le rapprochement. Les tickets ne sont jamais touchés.
    - BR IC n'est pas purgé : l'import reconstruit intégralement cette table à
      partir du fichier source à chaque cycle (BRICLigne.objects.all().delete()
      puis ré-import) — purger ne ferait aucun effet durable.
"""

from datetime import datetime

from dateutil.relativedelta import relativedelta
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, models
from django.db.models import Count, F, Sum
from django.db.models.functions import Substr, TruncMonth
from django.utils import timezone

from archives.models import ArchiveMensuelle, FichierPurge
from br.models import BRAnomalie, BRAsten
from entree_journal.models import EntreeJournal
from imports.models import FactureAstenLigne, FactureCyrusLigne, FactureEcartStatut

# ─── Sources purgées : (cle, label, modèle, champ date, [répartition], [montant], [marqueur]) ──
# date_field = None + custom_date → champ texte YYMMDD (dfac_str des statuts factures)
# marqueur    = enregistre les fichiers entièrement purgés dans FichierPurge pour
#               empêcher l'auto-import de les ré-importer (re-copie SMB).
SOURCES = [
    dict(cle='facture_cyrus', label='Factures Cyrus (lignes)', modele=FactureCyrusLigne,
         date_field='dfac_date', breakdown='cidc', montant='pfth',
         marqueur='facture_cyrus', fichier_field='fichier'),
    dict(cle='facture_asten', label='Factures Asten (lignes)', modele=FactureAstenLigne,
         date_field='date_reception_date', breakdown='magasin', montant='valorisation_ttc',
         marqueur='facture_asten', fichier_field='fichier'),
    dict(cle='facture_ecart_statut', label='Statuts factures en écart', modele=FactureEcartStatut,
         custom_date='dfac_str', breakdown='statut'),
    dict(cle='br_asten', label='BR Asten', modele=BRAsten, date_field='date_br',
         breakdown='code_magasin_id'),
    dict(cle='br_anomalie', label='BR anomalies', modele=BRAnomalie, date_field='date_reception',
         marqueur='br_anomalie', fichier_field='fichier_source'),
    dict(cle='entree_journal', label='Journal POS (entrées)', modele=EntreeJournal,
         date_field='created_at', breakdown='status_label'),
]

BATCH = 20000  # taille des lots de suppression


def _valeur_seuil(spec, cutoff):
    """Valeur de comparaison selon le type de champ (date ou datetime aware)."""
    if spec.get('custom_date'):
        return cutoff.strftime('%y%m%d')
    champ = spec['modele']._meta.get_field(spec['date_field'])
    if isinstance(champ, models.DateTimeField):
        return timezone.make_aware(datetime.combine(cutoff, datetime.min.time()),
                                   timezone.get_current_timezone())
    return cutoff


def _qs_a_purger(spec, cutoff):
    """Queryset des lignes à purger (dates < cutoff)."""
    modele = spec['modele']
    if spec.get('custom_date'):
        # dfac_str au format YYMMDD — la comparaison de chaînes est chronologique
        return modele.objects.filter(**{f"{spec['custom_date']}__lt": cutoff.strftime('%y%m%d')})
    return modele.objects.filter(**{f"{spec['date_field']}__lt": _valeur_seuil(spec, cutoff)})


def _resume_par_mois(qs, spec, cutoff):
    """Résumé mensuel des lignes à purger : {mois: {'nb', 'montant', 'par_…'}}."""
    if spec.get('custom_date'):
        rows = (
            qs.annotate(mois_cle=Substr(spec['custom_date'], 1, 4), cle=F(spec['breakdown']))
              .values('mois_cle', 'cle')
              .annotate(nb=Count('id'))
        )
        result = {}
        for r in rows:
            mois = datetime.strptime('20' + r['mois_cle'] + '01', '%Y%m%d').date()
            d = result.setdefault(mois, {'nb': 0, 'par': {}})
            d['nb'] += r['nb']
            d['par'][str(r['cle'])] = r['nb']
        return result

    qs2 = qs.annotate(mois=TruncMonth(spec['date_field']))
    if spec.get('breakdown'):
        qs2 = qs2.annotate(cle=F(spec['breakdown']))
        rows = qs2.values('mois', 'cle').annotate(
            nb=Count('id'), **({'total': Sum(spec['montant'])} if spec.get('montant') else {}))
    else:
        rows = qs2.values('mois').annotate(
            nb=Count('id'), **({'total': Sum(spec['montant'])} if spec.get('montant') else {}))

    result = {}
    for r in rows:
        mois = r['mois']
        if isinstance(mois, datetime):
            mois = mois.date()
        d = result.setdefault(mois, {'nb': 0, 'par': {}})
        d['nb'] += r['nb']
        if spec.get('montant'):
            d['montant'] = round(float(d.get('montant', 0.0)) + float(r['total'] or 0), 2)
        if spec.get('breakdown'):
            d['par'][str(r['cle'])] = r['nb']
    return result


class Command(BaseCommand):
    help = 'Purge les données de détail de plus de N mois et archive un résumé mensuel.'

    def add_arguments(self, parser):
        parser.add_argument('--mois', type=int, default=2,
                            help='Rétention en mois (défaut : 2). On garde les données à partir de maintenant - N mois.')
        parser.add_argument('--dry-run', action='store_true',
                            help='Simulation : affiche ce qui serait archivé/supprimé, sans rien modifier.')
        parser.add_argument('--no-vacuum-full', action='store_true',
                            help='VACUUM ANALYZE simple au lieu de VACUUM FULL (récupère moins de disque).')
        parser.add_argument('--no-purge-cache', action='store_true',
                            help='Ne pas vider la table django_cache (cache base mort, ~40 Mo).')
        parser.add_argument('--vider-asten', action='store_true',
                            help='Vide entièrement la table des factures Asten (à lancer tous les 2 jours).')

    # ──────────────────────────────────────────────────────────────────────
    def handle(self, *args, **opts):
        if opts['vider_asten']:
            self._vider_asten(dry_run=opts['dry_run'])
            return

        mois = opts['mois']
        if mois < 1:
            raise CommandError('--mois doit être supérieur ou égal à 1.')

        aujourdhui = timezone.localdate()
        cutoff = aujourdhui - relativedelta(months=mois)
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Purge des données — rétention {mois} mois "
            f"(on garde les données à partir du {cutoff:%d/%m/%Y})"))
        if opts['dry_run']:
            self.stdout.write(self.style.WARNING('Mode DRY-RUN : aucune modification ne sera faite.'))

        lignes_totales = 0
        resultats = []  # (cle, label, nb supprimées, nb mois archivés)

        for spec in SOURCES:
            cle, label = spec['cle'], spec['label']
            qs = _qs_a_purger(spec, cutoff)
            resume = _resume_par_mois(qs, spec, cutoff)

            if not resume:
                self.stdout.write(f'  • {label} : rien à purger')
                continue

            nb = sum(d['nb'] for d in resume.values())
            nb_mois = len(resume)
            self.stdout.write(f'  • {label} : {nb:,} lignes sur {nb_mois} mois')

            if opts['dry_run']:
                for mois_arch in sorted(resume):
                    d = resume[mois_arch]
                    extra = f" — montant {d.get('montant', 0):,.2f}" if 'montant' in d else ''
                    self.stdout.write(f'      {mois_arch:%m/%Y} : {d["nb"]:,} lignes{extra}')
                lignes_totales += nb
                continue

            # 1. Archiver le résumé AVANT suppression
            for mois_arch in sorted(resume):
                d = resume[mois_arch]
                details = {'nb': d['nb']}
                if 'montant' in d:
                    details['montant'] = d['montant']
                if d['par']:
                    details[f"par_{spec['breakdown']}"] = d['par']
                ArchiveMensuelle.objects.update_or_create(
                    source=cle, mois=mois_arch,
                    defaults={'nb_lignes': d['nb'], 'details': details})

            # 2. Supprimer (TRUNCATE si tout est plus vieux, sinon lots)
            fichiers_avant = None
            if spec.get('marqueur'):
                fichiers_avant = set(qs.values_list(spec['fichier_field'], flat=True).distinct())

            supprimees = self._supprimer(spec, cutoff, qs, label, nb)
            lignes_totales += supprimees
            resultats.append((cle, label, supprimees, nb_mois))

            # 3. Marquer les fichiers entièrement purgés (anti-ré-import SMB)
            if spec.get('marqueur') and fichiers_avant:
                restants = set(spec['modele'].objects.values_list(
                    spec['fichier_field'], flat=True).distinct())
                purges = fichiers_avant - restants
                for nom in purges:
                    FichierPurge.objects.get_or_create(
                        type_fichier=spec['marqueur'], nom_fichier=nom)
                if purges:
                    self.stdout.write(self.style.SUCCESS(
                        f'      → {len(purges)} fichier(s) marqué(s) « purgé » (anti-ré-import)'))

        if opts['dry_run']:
            self.stdout.write(self.style.WARNING(f"\nDRY-RUN terminé — {lignes_totales:,} lignes seraient supprimées."))
            return

        # 3. Rafraîchir la vue matérialisée des factures
        if any(cle in ('facture_cyrus', 'facture_asten') for cle, *_ in resultats):
            self._refresh_mv()

        # 4. Vider le cache base mort (avant VACUUM pour récupérer son espace)
        cache_vide = False
        if not opts['no_purge_cache']:
            cache_vide = self._purge_cache()

        # 5. VACUUM pour récupérer l'espace disque
        if resultats or cache_vide:
            tables = [spec['modele']._meta.db_table for spec in SOURCES
                      if any(r[0] == spec['cle'] for r in resultats)]
            if cache_vide:
                tables.append('django_cache')
            self._vacuum(tables, full=not opts['no_vacuum_full'])

        self._afficher_synthese(resultats, lignes_totales)

    # ──────────────────────────────────────────────────────────────────────
    def _vider_asten(self, dry_run=False):
        """Vide entièrement imports_factureastenligne (rechargée depuis le SMB).
        Archive un résumé mensuel CUMULÉ puis TRUNCATE + refresh MV."""
        spec = next(s for s in SOURCES if s['cle'] == 'facture_asten')
        modele = spec['modele']
        nb = modele.objects.count()
        self.stdout.write(self.style.MIGRATE_HEADING('Vidange des factures Asten'))
        if nb == 0:
            self.stdout.write('  • Factures Asten : déjà vide')
            return

        resume = _resume_par_mois(modele.objects.all(), spec, None)
        if dry_run:
            for mois_arch in sorted(resume):
                d = resume[mois_arch]
                self.stdout.write(f'      {mois_arch:%m/%Y} : {d["nb"]:,} lignes seraient vidées')
            self.stdout.write(self.style.WARNING(f'\nDRY-RUN — {nb:,} lignes ne seront pas supprimées.'))
            return

        # Archiver en CUMUL (la table est vidée plusieurs fois par mois)
        for mois_arch in sorted(resume):
            d = resume[mois_arch]
            obj, _ = ArchiveMensuelle.objects.get_or_create(
                source='facture_asten', mois=mois_arch)
            obj.nb_lignes += d['nb']
            details = obj.details or {}
            details['nb'] = obj.nb_lignes
            if 'montant' in d:
                details['montant'] = round(float(details.get('montant', 0.0)) + d['montant'], 2)
            if d['par']:
                par = details.setdefault('par_magasin', {})
                for cle_mag, v in d['par'].items():
                    par[cle_mag] = par.get(cle_mag, 0) + v
            obj.details = details
            obj.save()

        with connection.cursor() as cur:
            cur.execute('TRUNCATE TABLE "imports_factureastenligne"')
        self.stdout.write(self.style.SUCCESS(f'  ✓ {nb:,} lignes vidées (TRUNCATE imports_factureastenligne)'))
        self._refresh_mv()
        self.stdout.write(self.style.SUCCESS('  ✓ mv_factures_joined rafraîchie (factures Asten vidées)'))

    def _supprimer(self, spec, cutoff, qs, label, nb_attendu):
        """TRUNCATE si aucune ligne à conserver, sinon suppression par lots."""
        modele = spec['modele']
        table = modele._meta.db_table

        # Y a-t-il des lignes à conserver (date >= cutoff) ?
        if spec.get('custom_date'):
            a_conserver = modele.objects.exclude(
                **{f"{spec['custom_date']}__lt": cutoff.strftime('%y%m%d')}).exists()
        else:
            a_conserver = modele.objects.exclude(
                **{f"{spec['date_field']}__lt": _valeur_seuil(spec, cutoff)}).exists()

        if not a_conserver:
            with connection.cursor() as cur:
                cur.execute(f'TRUNCATE TABLE "{table}"')
            self.stdout.write(self.style.SUCCESS(f'      → table {table} TRUNCATE (tout était plus vieux)'))
            return nb_attendu

        # Suppression par lots de BATCH lignes
        total = 0
        while True:
            ids = list(qs.values_list('pk', flat=True)[:BATCH])
            if not ids:
                break
            with connection.cursor() as cur:
                cur.execute(f'DELETE FROM "{table}" WHERE id = ANY(%s)', (ids,))
                total += cur.rowcount
            self.stdout.write(f'\r      {label} : {total:,} lignes supprimées...', ending='')
            self.stdout.flush()
        self.stdout.write('')
        return total

    def _refresh_mv(self):
        with connection.cursor() as cur:
            cur.execute("SELECT to_regclass('public.mv_factures_joined') IS NOT NULL")
            if cur.fetchone()[0]:
                self.stdout.write('  Rafraîchissement de mv_factures_joined…')
                cur.execute('REFRESH MATERIALIZED VIEW mv_factures_joined')
                self.stdout.write(self.style.SUCCESS('  ✓ mv_factures_joined rafraîchie'))

    def _vacuum(self, tables, full=True):
        connection.set_autocommit(True)
        mode = 'FULL, ANALYZE' if full else 'ANALYZE'
        for t in tables:
            try:
                with connection.cursor() as cur:
                    cur.execute(f'VACUUM ({mode}) "{t}"')
                self.stdout.write(self.style.SUCCESS(f'  ✓ VACUUM {"FULL " if full else ""}terminé : {t}'))
            except Exception as e:
                self.stderr.write(self.style.WARNING(f'  ! VACUUM ignoré ({t}) : {e}'))

    def _purge_cache(self):
        """Vide la table django_cache (cache base mort, non utilisé par les settings).
        Retourne True si la table existait avec des entrées."""
        with connection.cursor() as cur:
            cur.execute("SELECT to_regclass('public.django_cache') IS NOT NULL")
            if not cur.fetchone()[0]:
                return False
            cur.execute('SELECT COUNT(*) FROM django_cache')
            nb = cur.fetchone()[0]
            if nb:
                cur.execute('DELETE FROM django_cache')
                self.stdout.write(self.style.SUCCESS(f'  ✓ django_cache vidé ({nb:,} entrées — cache base mort)'))
                return True
        return False

    def _afficher_synthese(self, resultats, lignes_totales):
        self.stdout.write(self.style.MIGRATE_HEADING('\n── Synthèse de la purge ──'))
        for _cle, label, nb, nb_mois in resultats:
            self.stdout.write(f'  {label:<32} {nb:>12,} lignes supprimées ({nb_mois} mois archivés)')
        self.stdout.write(self.style.SUCCESS(f'\n  Total : {lignes_totales:,} lignes supprimées. '
                                             f'Résumés consultables dans l’admin → Archives mensuelles.'))
