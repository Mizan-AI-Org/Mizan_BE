import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class PlatformAdminConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "platform_admin"
    verbose_name = "Platform Admin"

    def ready(self):
        # Sync PLATFORM_OPS_EMAILS → DB flags so Operators UI and grants stay consistent.
        try:
            from django.conf import settings

            emails = [e for e in (getattr(settings, "PLATFORM_OPS_EMAILS", None) or []) if e]
            if not emails:
                return

            from django.contrib.auth import get_user_model

            from .permissions import platform_ops_superuser_emails

            User = get_user_model()
            supers = platform_ops_superuser_emails()
            for email in emails:
                user = User.objects.filter(email__iexact=email).first()
                if not user:
                    logger.info(
                        "PLATFORM_OPS_EMAILS: no user for %s — create the account, then restart",
                        email,
                    )
                    continue
                dirty = []
                if not user.is_platform_operator:
                    user.is_platform_operator = True
                    dirty.append("is_platform_operator")
                if not user.is_staff:
                    user.is_staff = True
                    dirty.append("is_staff")
                if email in supers and not user.is_superuser:
                    user.is_superuser = True
                    dirty.append("is_superuser")
                # Dedicated ops account — detach restaurant unless already none.
                if user.restaurant_id is not None:
                    user.restaurant = None
                    dirty.append("restaurant")
                if dirty:
                    fields = list(dict.fromkeys([*dirty, "updated_at"]))
                    try:
                        user.save(update_fields=fields)
                    except Exception:
                        user.save()
                    logger.info("PLATFORM_OPS_EMAILS: granted ops to %s (%s)", email, ", ".join(dirty))
        except Exception:
            # Never block startup (migrate, etc.).
            logger.exception("PLATFORM_OPS_EMAILS sync skipped")
