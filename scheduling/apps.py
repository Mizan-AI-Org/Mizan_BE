from django.apps import AppConfig


class SchedulingConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'scheduling'
    
    def ready(self):
        """Import signals when the app is ready"""
        import scheduling.signals  # noqa: F401
