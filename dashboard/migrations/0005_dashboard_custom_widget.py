# Generated manually

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0025_customuser_dashboard_widget_order"),
        ("dashboard", "0004_rename_dashboard_s_restaur_idx_dashboard_s_restaur_3df112_idx"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DashboardCustomWidget",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("subtitle", models.TextField(blank=True)),
                ("link_url", models.CharField(blank=True, max_length=2048)),
                ("icon", models.CharField(default="sparkles", max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "restaurant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dashboard_custom_widgets",
                        to="accounts.restaurant",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dashboard_custom_widgets",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "dashboard_custom_widgets",
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="dashboardcustomwidget",
            index=models.Index(fields=["user", "created_at"], name="dashboard_c_user_id_2b8f91_idx"),
        ),
        migrations.AddIndex(
            model_name="dashboardcustomwidget",
            index=models.Index(fields=["restaurant", "created_at"], name="dashboard_c_restaur_6c1a02_idx"),
        ),
    ]
