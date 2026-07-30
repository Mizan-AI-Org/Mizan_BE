# Generated manually for platform WhatsApp admin

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PlatformWhatsAppConfig",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.UUID("00000000-0000-4000-8000-000000000001"),
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("phone_number_id", models.CharField(blank=True, default="", max_length=64)),
                ("business_account_id", models.CharField(blank=True, default="", max_length=64)),
                ("access_token_encrypted", models.TextField(blank=True, default="")),
                ("verify_token", models.CharField(blank=True, default="", max_length=255)),
                ("activation_phone", models.CharField(blank=True, default="212784476751", max_length=32)),
                ("api_version", models.CharField(blank=True, default="v22.0", max_length=16)),
                ("miya_whatsapp_enabled", models.BooleanField(default=True)),
                ("miya_voice_default", models.BooleanField(default=False)),
                ("display_phone_number", models.CharField(blank=True, default="", max_length=32)),
                ("verified_name", models.CharField(blank=True, default="", max_length=255)),
                ("last_probe_at", models.DateTimeField(blank=True, null=True)),
                ("last_probe_ok", models.BooleanField(blank=True, null=True)),
                ("last_probe_message", models.TextField(blank=True, default="")),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "updated_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="whatsapp_config_updates",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "Platform WhatsApp config",
                "verbose_name_plural": "Platform WhatsApp config",
            },
        ),
        migrations.CreateModel(
            name="WhatsAppMessageTemplate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("meta_id", models.CharField(db_index=True, max_length=64, unique=True)),
                ("name", models.CharField(db_index=True, max_length=128)),
                ("language", models.CharField(default="en_US", max_length=16)),
                ("category", models.CharField(blank=True, default="", max_length=32)),
                ("status", models.CharField(blank=True, default="", max_length=32)),
                ("body_text", models.TextField(blank=True, default="")),
                ("footer_text", models.TextField(blank=True, default="")),
                ("header_text", models.TextField(blank=True, default="")),
                ("components_json", models.JSONField(blank=True, default=list)),
                ("synced_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "WhatsApp message template",
                "verbose_name_plural": "WhatsApp message templates",
                "ordering": ["name", "language"],
            },
        ),
    ]
