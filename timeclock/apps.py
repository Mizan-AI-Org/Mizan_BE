from django.apps import AppConfig


class TimeclockConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'timeclock'

    def ready(self):
        import timeclock.signals  # noqa: F401 — register ClockEvent → cache bust
