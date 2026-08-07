"""
PostgreSQL test settings — uses a pre-provisioned database (no CREATE DATABASE).

Environment variables (defaults for local dev):
  TEST_DB_NAME=mizan_test
  TEST_DB_USER=macbookpro  (or POSTGRES_USER)
  TEST_DB_PASSWORD=        (empty for local trust auth)
  TEST_DB_HOST=localhost
  TEST_DB_PORT=5432

Setup once:
  createdb mizan_test   # or CREATE DATABASE mizan_test;
  python manage.py migrate --settings=mizan.test_settings_postgres

Run E2E:
  python manage.py test miya.tests.e2e --settings=mizan.test_settings_postgres --keepdb -v 2
"""
from __future__ import annotations

import os

from .settings import *  # noqa: F403

_TEST_DB = os.environ.get("TEST_DB_NAME", "mizan_test")
_TEST_USER = os.environ.get("TEST_DB_USER", os.environ.get("POSTGRES_USER", "macbookpro"))
_TEST_PASSWORD = os.environ.get("TEST_DB_PASSWORD", os.environ.get("POSTGRES_PASSWORD", ""))
_TEST_HOST = os.environ.get("TEST_DB_HOST", os.environ.get("POSTGRES_HOST", "localhost"))
_TEST_PORT = os.environ.get("TEST_DB_PORT", os.environ.get("POSTGRES_PORT", "5432"))

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": _TEST_DB,
        "USER": _TEST_USER,
        "PASSWORD": _TEST_PASSWORD,
        "HOST": _TEST_HOST,
        "PORT": _TEST_PORT,
        "TEST": {
            "NAME": _TEST_DB,
            "MIRROR": None,
        },
        "OPTIONS": {"connect_timeout": 10},
    }
}

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "postgres-e2e-tests",
    }
}

# Disable external side effects in E2E
MIYA_MASTRA_API_KEY = os.environ.get("MIYA_MASTRA_API_KEY", "")
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# Phase 14.3.3 — FIXTURE_PROVIDER at external OCR/vision boundary only (PostgreSQL E2E)
MULTIMODAL_EXTRACTION_PROVIDER = "FIXTURE"
