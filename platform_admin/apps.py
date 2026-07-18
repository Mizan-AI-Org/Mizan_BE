import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class PlatformAdminConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "platform_admin"
    verbose_name = "Platform Admin"

    def ready(self):
        try:
            from .bootstrap import sync_platform_ops_from_env

            sync_platform_ops_from_env()
        except Exception:
            # Never block startup (migrate, etc.).
            logger.exception("PLATFORM_OPS_EMAILS sync skipped")
