from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class ImportsConfig(AppConfig):
    name = 'imports'

    def ready(self):
        # Ne démarrer le scheduler que dans le worker gunicorn numéro 1 (pas tous les workers)
        import sys, os
        is_gunicorn = 'gunicorn' in ' '.join(sys.argv) or os.environ.get('SERVER_SOFTWARE', '').startswith('gunicorn')
        is_runserver = 'runserver' in sys.argv and os.environ.get('RUN_MAIN') == 'true'
        if not (is_gunicorn or is_runserver):
            return
        # Utiliser un fichier verrou pour qu'un seul worker démarre le scheduler
        lock_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), '.scheduler.lock')
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            import atexit
            atexit.register(lambda: os.path.exists(lock_path) and os.remove(lock_path))
        except FileExistsError:
            return  # Un autre worker a déjà démarré le scheduler
        self._start_scheduler()

    def _start_scheduler(self):
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.interval import IntervalTrigger

            scheduler = BackgroundScheduler(timezone='UTC')
            scheduler.add_job(
                _job_import_fichiers,
                trigger=IntervalTrigger(minutes=2),
                id='auto_import_fichiers',
                name='Import automatique des fichiers',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=60,
            )
            scheduler.add_job(
                _job_poll_outlook,
                trigger=IntervalTrigger(minutes=2),
                id='auto_poll_outlook',
                name='Polling Outlook automatique',
                replace_existing=True,
                max_instances=1,
                misfire_grace_time=60,
            )
            scheduler.start()
            logger.info("Scheduler démarré (import fichiers + polling Outlook, toutes les 2 min, worker unique)")
        except Exception as e:
            logger.error(f"Impossible de démarrer le scheduler : {e}")


def _job_poll_outlook():
    """Tâche planifiée : polling Microsoft Graph pour les comptes Outlook actifs."""
    import threading
    import logging
    _lock = getattr(_job_poll_outlook, '_lock', None)
    if _lock is None:
        _job_poll_outlook._lock = threading.Lock()
        _lock = _job_poll_outlook._lock

    if not _lock.acquire(blocking=False):
        return  # Polling déjà en cours, on skip

    log = logging.getLogger(__name__)
    try:
        from tickets.models import ConfigPipeline
        try:
            config = ConfigPipeline.objects.get(pk=1)
            if not config.polling_actif:
                log.debug("Polling Outlook désactivé dans ConfigPipeline, skip")
                return
        except Exception:
            log.debug("Pas de ConfigPipeline, skip polling Outlook")
            return

        from tickets.outlook_service import poll_all_accounts
        stats = poll_all_accounts()
        if stats.get('messages', 0) > 0 or stats.get('erreurs', 0) > 0:
            log.info(
                f"Polling Outlook : {stats['comptes']} compte(s) | "
                f"{stats['messages']} message(s) | "
                f"{stats['crees']} créé(s) | "
                f"{stats['followups']} suivi(s) | "
                f"{stats['erreurs']} erreur(s)"
            )
        else:
            log.debug("Polling Outlook : aucun nouveau message")
    except Exception as e:
        log.error(f"Erreur polling Outlook automatique : {e}", exc_info=True)
    finally:
        _lock.release()


def _job_import_fichiers():
    """Tâche planifiée : scanner, importer les nouveaux fichiers, puis recalculer les écarts."""
    import threading
    import logging
    _lock = getattr(_job_import_fichiers, '_lock', None)
    if _lock is None:
        _job_import_fichiers._lock = threading.Lock()
        _lock = _job_import_fichiers._lock

    if not _lock.acquire(blocking=False):
        return  # Import déjà en cours, on skip

    log = logging.getLogger(__name__)
    try:
        from imports.services import scanner_et_importer_fichiers
        from ecarts.services import recalculer_ecarts
        fichiers = scanner_et_importer_fichiers()
        if fichiers:
            log.info(f"Import automatique : {len(fichiers)} fichier(s) importé(s), recalcul écarts…")
            recalculer_ecarts()
            from django.core.cache import cache
            cache.clear()
        else:
            log.debug("Import automatique : aucun nouveau fichier, skip écarts")
    except Exception as e:
        log.error(f"Erreur import automatique : {e}", exc_info=True)
    finally:
        _lock.release()
