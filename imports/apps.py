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
            scheduler.start()
            logger.info("Scheduler d'import automatique démarré (toutes les 2 minutes, worker unique)")
        except Exception as e:
            logger.error(f"Impossible de démarrer le scheduler : {e}")


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
