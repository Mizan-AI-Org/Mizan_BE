from django.core.management.base import BaseCommand
from django.apps import apps
from django.db import connections


class Command(BaseCommand):
    help = "Report row counts for all registered models on the default database"

    def add_arguments(self, parser):
        parser.add_argument(
            '--limit', type=int, default=0,
            help='Limit to first N app labels (0 = all)'
        )

    def handle(self, *args, **options):
        limit = options['limit']
        app_configs = list(apps.get_app_configs())
        if limit and limit > 0:
            app_configs = app_configs[:limit]

        default_conn = connections['default']
        self.stdout.write(self.style.NOTICE(f"Using DB: {default_conn.settings_dict.get('NAME')}"))

        total_models = 0
        total_rows = 0
        for app in app_configs:
            self.stdout.write(self.style.NOTICE(f"\nApp: {app.label}"))
            for model in app.get_models():
                try:
                    count = model.objects.count()
                    total_models += 1
                    total_rows += count
                    self.stdout.write(f"  {model.__name__}: {count}")
                except Exception as e:
                    self.stdout.write(self.style.WARNING(f"  {model.__name__}: error -> {e}"))

        self.stdout.write(self.style.SUCCESS(f"\nSummary: models={total_models}, rows={total_rows}"))