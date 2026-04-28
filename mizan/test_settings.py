"""Test settings — uses an in-memory SQLite database so unit tests don't
need to touch the real Postgres instance.

Imports the real settings and overrides only what's needed for tests."""

from .settings import *  # noqa: F401, F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]
