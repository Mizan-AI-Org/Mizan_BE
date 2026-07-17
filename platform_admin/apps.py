import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class PlatformAdminConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "platform_admin"
    verbose_name = "Platform Admin"

    def ready(self):
        # Sync PLATFORM_OPS_EMAILS (+ optional PLATFORM_OPS_PASSWORD) so /admin login works from .env.
        try:
            from django.conf import settings

            emails = [e for e in (getattr(settings, "PLATFORM_OPS_EMAILS", None) or []) if e]
            if not emails:
                return

            from django.contrib.auth import get_user_model

            from .permissions import platform_ops_superuser_emails

            User = get_user_model()
            supers = platform_ops_superuser_emails()
            bootstrap_password = (getattr(settings, "PLATFORM_OPS_PASSWORD", None) or "").strip()

            for email in emails:
                user = User.objects.filter(email__iexact=email).first()
                created = False
                if not user:
                    if not bootstrap_password:
                        logger.info(
                            "PLATFORM_OPS_EMAILS: no user for %s — set PLATFORM_OPS_PASSWORD "
                            "to auto-create, or create the account then restart",
                            email,
                        )
                        continue
                    local = email.split("@")[0] or "Ops"
                    user = User(
                        email=email,
                        username=email,
                        first_name=local[:30] or "Ops",
                        last_name="Admin",
                        role="SUPER_ADMIN",
                        restaurant=None,
                        is_active=True,
                        is_staff=True,
                        is_platform_operator=True,
                        is_superuser=email in supers,
                    )
                    user.set_password(bootstrap_password)
                    user.save()
                    created = True
                    logger.info("PLATFORM_OPS_EMAILS: created ops user %s", email)
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
                if not user.is_admin_role():
                    user.role = "SUPER_ADMIN"
                    dirty.append("role")
                if user.restaurant_id is not None:
                    user.restaurant = None
                    dirty.append("restaurant")
                if not user.is_active:
                    user.is_active = True
                    dirty.append("is_active")

                if bootstrap_password:
                    user.set_password(bootstrap_password)
                    dirty.append("password")

                if dirty:
                    # password hash lives in `password` field; save full row if password changed
                    if "password" in dirty:
                        user.save()
                    else:
                        fields = list(dict.fromkeys([*dirty, "updated_at"]))
                        try:
                            user.save(update_fields=fields)
                        except Exception:
                            user.save()
                    logger.info(
                        "PLATFORM_OPS_EMAILS: updated ops user %s (%s)",
                        email,
                        ", ".join(dirty),
                    )
                elif created:
                    pass
        except Exception:
            # Never block startup (migrate, etc.).
            logger.exception("PLATFORM_OPS_EMAILS sync skipped")
