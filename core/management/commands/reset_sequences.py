from io import StringIO
from django.core.management.base import BaseCommand
from django.core.management import call_command
from django.apps import apps
from django.db import connection


class Command(BaseCommand):
    help = "Reset PostgreSQL sequences for all installed apps"

    def handle(self, *args, **options):
        app_labels = [app.label for app in apps.get_app_configs()]
        if not app_labels:
            self.stdout.write(self.style.WARNING("No apps found to reset sequences."))
            return

        self.stdout.write("Generating sequence reset SQL for: %s" % ", ".join(app_labels))
        out = StringIO()
        call_command('sqlsequencereset', *app_labels, stdout=out)
        sql = out.getvalue().strip()

        if not sql:
            self.stdout.write(self.style.WARNING("No SQL generated; sequences may already be aligned."))
            return

        self.stdout.write("Executing sequence reset SQL...")
        with connection.cursor() as cursor:
            cursor.execute(sql)

        self.stdout.write(self.style.SUCCESS("Sequences reset successfully."))