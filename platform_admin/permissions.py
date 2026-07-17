from rest_framework.permissions import BasePermission


def user_is_platform_operator(user) -> bool:
    """True only for explicit platform ops — never restaurant SUPER_ADMIN alone."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if not getattr(user, "is_platform_operator", False):
        return False
    # Keep is_staff as a secondary guard (Django convention for back-office).
    return bool(getattr(user, "is_staff", False))


class IsPlatformOperator(BasePermission):
    """Internal Mizan operators only — explicit is_platform_operator, not restaurant SUPER_ADMIN."""

    message = "Platform operator access required."

    def has_permission(self, request, view):
        return user_is_platform_operator(getattr(request, "user", None))


class IsPlatformSuperuser(BasePermission):
    """Destructive / privilege-granting actions."""

    message = "Platform superuser access required."

    def has_permission(self, request, view):
        user = getattr(request, "user", None)
        return bool(
            user_is_platform_operator(user) and getattr(user, "is_superuser", False)
        )
