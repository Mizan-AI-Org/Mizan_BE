from django.core.management.base import BaseCommand

from platform_admin.bootstrap import sync_platform_ops_from_env


class Command(BaseCommand):
    help = (
        "Create/update Platform Admin users from PLATFORM_OPS_EMAILS and "
        "PLATFORM_OPS_PASSWORD (unlocks accounts, sets password)."
    )

    def handle(self, *args, **options):
        actions = sync_platform_ops_from_env(force_password=True)
        for line in actions:
            self.stdout.write(line)
        self.stdout.write(self.style.SUCCESS("Done. Sign in at /admin with email + PLATFORM_OPS_PASSWORD."))
