# Generated manually for StaffCapturedOrder

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("dashboard", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="StaffCapturedOrder",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("customer_name", models.CharField(blank=True, max_length=255)),
                ("customer_phone", models.CharField(blank=True, max_length=40)),
                (
                    "order_type",
                    models.CharField(
                        choices=[
                            ("DINE_IN", "Dine in"),
                            ("TAKEOUT", "Takeout"),
                            ("DELIVERY", "Delivery"),
                            ("OTHER", "Other"),
                        ],
                        default="DINE_IN",
                        max_length=20,
                    ),
                ),
                ("table_or_location", models.CharField(blank=True, max_length=120)),
                ("items_summary", models.TextField()),
                ("dietary_notes", models.TextField(blank=True)),
                ("special_instructions", models.TextField(blank=True)),
                (
                    "channel",
                    models.CharField(
                        choices=[
                            ("VOICE", "Voice (Miya)"),
                            ("TEXT", "Text (Miya)"),
                            ("MANUAL", "Manual form"),
                        ],
                        default="MANUAL",
                        max_length=20,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="staff_captured_orders",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "restaurant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="staff_captured_orders",
                        to="accounts.restaurant",
                    ),
                ),
            ],
            options={
                "db_table": "dashboard_staff_captured_orders",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="staffcapturedorder",
            index=models.Index(fields=["restaurant", "created_at"], name="dashboard_s_restaur_idx"),
        ),
    ]
