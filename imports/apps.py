from django.apps import AppConfig
import logging

logger = logging.getLogger(__name__)


class ImportsConfig(AppConfig):
    name = 'imports'

    def ready(self):
        # Ne pas démarrer le scheduler dans les commandes de gestion (migrations, etc.)
        import sys
        if 'runserver' not in sys.argv and 'gunicorn' not in ' '.join(sys.argv) and 'uvicorn' not in ' '.join(sys.argv):
            return
        # Éviter le double démarrage avec le reloader Django
        import os
        if os.environ.get('RUN_MAIN') == 'true' or 'gunicorn' in ' '.join(sys.argv):
            self._start_scheduler()

    def _start_scheduler(self):
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.interval import IntervalTrigger
            from django.conf import settings

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
            logger.info("Scheduler d'import automatique démarré (toutes les 2 minutes)")
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
        log.debug("Import automatique : début du scan")
        fichiers = scanner_et_importer_fichiers()
        log.debug(f"Import automatique : {len(fichiers)} fichier(s) importé(s)")
        recalculer_ecarts()
        log.debug("Import automatique : écarts recalculés")
    except Exception as e:
        log.error(f"Erreur import automatique : {e}", exc_info=True)
    finally:
        _lock.release()
