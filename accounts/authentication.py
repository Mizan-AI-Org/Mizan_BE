"""JWT auth that rejects suspended / deactivated tenant accounts."""
from __future__ import annotations

from rest_framework_simplejwt.authentication import JWTAuthentication
from rest_framework_simplejwt.exceptions import AuthenticationFailed

from platform_admin.lifecycle import user_tenant_access_denied_reason


class MizanJWTAuthentication(JWTAuthentication):
    """Standard JWT auth plus tenant lifecycle / inactive-user gates."""

    def get_user(self, validated_token):
        user = super().get_user(validated_token)
        reason = user_tenant_access_denied_reason(user)
        if reason:
            raise AuthenticationFailed(reason, code="tenant_access_denied")
        return user
