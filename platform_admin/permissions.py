from django.conf import settings
from rest_framework.permissions import BasePermission


def platform_ops_emails() -> set[str]:
    return {e.lower() for e in (getattr(settings, "PLATFORM_OPS_EMAILS", None) or [])}


def platform_ops_superuser_emails() -> set[str]:
    return {e.lower() for e in (getattr(settings, "PLATFORM_OPS_SUPERUSER_EMAILS", None) or [])}


def email_is_platform_ops(email: str | None) -> bool:
    if not email:
        return False
    return email.strip().lower() in platform_ops_emails()


PLATFORM_OPS_USE_ADMIN_LOGIN = {
    "error": (
        "Platform operator accounts sign in at /admin only. "
        "This page is for restaurant staff and managers."
    ),
    "code": "platform_ops_use_admin_login",
}


def user_is_platform_ops_account(user) -> bool:
    """Account reserved for Platform Admin (/admin) — not tenant /auth.

    True when the email is in ``PLATFORM_OPS_EMAILS`` /
    ``PLATFORM_OPS_SUPERUSER_EMAILS``, or the DB flag
    ``is_platform_operator`` is set. Used to split login surfaces.
    """
    if not user:
        return False
    email = (getattr(user, "email", None) or "").strip().lower()
    if email and email in platform_ops_emails():
        return True
    if email and email in platform_ops_superuser_emails():
        return True
    return bool(getattr(user, "is_platform_operator", False))


def user_is_platform_operator(user) -> bool:
    """True for explicit platform ops — never restaurant SUPER_ADMIN alone.

    Honors either the DB flag ``is_platform_operator`` or membership in
    ``PLATFORM_OPS_EMAILS`` from the environment.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if email_is_platform_ops(getattr(user, "email", None)):
        return True
    if not getattr(user, "is_platform_operator", False):
        return False
    # Keep is_staff as a secondary guard (Django convention for back-office).
    return bool(getattr(user, "is_staff", False))


def user_is_platform_superuser(user) -> bool:
    if not user_is_platform_operator(user):
        return False
    email = (getattr(user, "email", None) or "").strip().lower()
    if email and email in platform_ops_superuser_emails():
        return True
    return bool(getattr(user, "is_superuser", False))


class IsPlatformOperator(BasePermission):
    """Internal Mizan operators only — explicit is_platform_operator / PLATFORM_OPS_EMAILS."""

    message = "Platform operator access required."

    def has_permission(self, request, view):
        return user_is_platform_operator(getattr(request, "user", None))


class IsPlatformSuperuser(BasePermission):
    """Destructive / privilege-granting actions."""

    message = "Platform superuser access required."

    def has_permission(self, request, view):
        return user_is_platform_superuser(getattr(request, "user", None))
