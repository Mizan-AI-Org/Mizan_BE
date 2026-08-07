# Generated manually for Phase 2 operational memory

import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0038_restaurant_automatic_clock_out_default_true"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("miya", "0003_tenantdocument_location"),
    ]

    operations = [
        migrations.CreateModel(
            name="OperationalEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("event_type", models.CharField(db_index=True, max_length=64)),
                ("entity_type", models.CharField(blank=True, db_index=True, default="", max_length=32)),
                ("entity_id", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("entity_label", models.CharField(blank=True, default="", max_length=255)),
                ("summary", models.CharField(blank=True, default="", max_length=512)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("channel", models.CharField(blank=True, default="", max_length=32)),
                ("operation_id", models.CharField(blank=True, db_index=True, default="", max_length=128)),
                ("message_id", models.CharField(blank=True, default="", max_length=128)),
                ("conversation_id", models.CharField(blank=True, default="", max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="operational_events_acted",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="operational_events",
                        to="accounts.businesslocation",
                    ),
                ),
                (
                    "restaurant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="operational_events",
                        to="accounts.restaurant",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="WorkingMemorySnapshot",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("establishment_id", models.CharField(blank=True, default="", max_length=64)),
                ("establishment_name", models.CharField(blank=True, default="", max_length=255)),
                ("department", models.CharField(blank=True, default="", max_length=128)),
                ("current_task_id", models.CharField(blank=True, default="", max_length=64)),
                ("current_task_label", models.CharField(blank=True, default="", max_length=255)),
                ("current_incident_id", models.CharField(blank=True, default="", max_length=64)),
                ("current_incident_label", models.CharField(blank=True, default="", max_length=255)),
                ("current_document_id", models.CharField(blank=True, default="", max_length=64)),
                ("current_document_label", models.CharField(blank=True, default="", max_length=255)),
                ("current_invoice_id", models.CharField(blank=True, default="", max_length=64)),
                ("current_invoice_label", models.CharField(blank=True, default="", max_length=255)),
                ("current_workflow", models.CharField(blank=True, default="", max_length=128)),
                ("extra", models.JSONField(blank=True, default=dict)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "restaurant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="working_memory_snapshots",
                        to="accounts.restaurant",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="working_memory_snapshots",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="operationalevent",
            index=models.Index(fields=["restaurant", "-created_at"], name="miya_operat_restaur_7a1b01_idx"),
        ),
        migrations.AddIndex(
            model_name="operationalevent",
            index=models.Index(
                fields=["restaurant", "entity_type", "entity_id"],
                name="miya_operat_restaur_9c2d02_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="operationalevent",
            index=models.Index(
                fields=["restaurant", "event_type", "-created_at"],
                name="miya_operat_restaur_3e4f03_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="workingmemorysnapshot",
            index=models.Index(fields=["restaurant", "user"], name="miya_workin_restaur_5a6b04_idx"),
        ),
        migrations.AddConstraint(
            model_name="workingmemorysnapshot",
            constraint=models.UniqueConstraint(
                fields=("restaurant", "user"),
                name="miya_working_memory_unique_user_org",
            ),
        ),
    ]
