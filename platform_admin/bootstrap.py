"""Bootstrap platform operators from PLATFORM_OPS_* env vars."""
from __future__ import annotations

import logging

from django.conf import settings
from django.contrib.auth import get_user_model

logger = logging.getLogger(__name__)


def sync_platform_ops_from_env(*, force_password: bool = True) -> list[str]:
    """Create/update ops users from PLATFORM_OPS_EMAILS (+ optional password).

    Returns a list of human-readable action lines.
    """
    emails = [e for e in (getattr(settings, "PLATFORM_OPS_EMAILS", None) or []) if e]
    if not emails:
        return ["PLATFORM_OPS_EMAILS is empty — nothing to do"]

    from .permissions import platform_ops_superuser_emails

    User = get_user_model()
    supers = platform_ops_superuser_emails()
    bootstrap_password = (getattr(settings, "PLATFORM_OPS_PASSWORD", None) or "").strip()
    actions: list[str] = []

    for email in emails:
        user = User.objects.filter(email__iexact=email).first()
        if not user:
            if not bootstrap_password:
                msg = (
                    f"{email}: no user — set PLATFORM_OPS_PASSWORD to auto-create"
                )
                logger.info("PLATFORM_OPS: %s", msg)
                actions.append(msg)
                continue
            local = (email.split("@")[0] or "Ops")[:30]
            user = User(
                email=email.lower(),
                first_name=local or "Ops",
                last_name="Admin",
                role="SUPER_ADMIN",
                restaurant=None,
                is_active=True,
                is_staff=True,
                is_platform_operator=True,
                is_superuser=email in supers,
                is_verified=True,
            )
            user.set_password(bootstrap_password)
            user.failed_login_attempts = 0
            user.account_locked_until = None
            user.save()
            msg = f"{email}: created ops user"
            logger.info("PLATFORM_OPS: %s", msg)
            actions.append(msg)
            continue

        dirty: list[str] = []
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

        # Always clear lockouts for env-listed ops so .env password works after failed tries.
        if user.failed_login_attempts or user.account_locked_until:
            user.failed_login_attempts = 0
            user.account_locked_until = None
            dirty.append("unlock")

        if bootstrap_password and force_password:
            user.set_password(bootstrap_password)
            dirty.append("password")

        if dirty:
            user.save()
            msg = f"{email}: updated ({', '.join(dirty)})"
            logger.info("PLATFORM_OPS: %s", msg)
            actions.append(msg)
        else:
            actions.append(f"{email}: already up to date")

    return actions
