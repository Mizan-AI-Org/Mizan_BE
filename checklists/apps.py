from django.apps import AppConfig


class ChecklistsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'checklists'
    verbose_name = 'Checklist Management'

    def ready(self):
        # Import signals to register post_save hooks at startup
        try:
            from . import signals  # noqa: F401
        except Exception:
            # Avoid crashing startup if import errors occur; they will be logged
            pass