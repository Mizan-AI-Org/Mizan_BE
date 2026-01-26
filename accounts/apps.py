from django.apps import AppConfig
import sys


class AccountsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'accounts'

    def ready(self):
        try:
            import accounts.signals  # noqa: F401
            print("[AccountsConfig] Signals loaded successfully", file=sys.stderr)
        except Exception as e:
            print(f"[AccountsConfig] ERROR loading signals: {e}", file=sys.stderr)
            import traceback
            traceback.print_exc()
