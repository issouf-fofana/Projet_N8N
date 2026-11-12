from django.apps import AppConfig


class EntreeJournalConfig(AppConfig):
    name = 'entree_journal'

    def ready(self):
        import entree_journal.signals  # noqa: F401
