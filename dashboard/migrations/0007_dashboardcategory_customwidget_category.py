import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0027_alter_customuser_role_and_more"),
        ("dashboard", "0006_rename_dashboard_c_user_id_2b8f91_idx_dashboard_c_user_id_ab3ae4_idx_and_more"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="DashboardCategory",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=80)),
                ("order_index", models.IntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "restaurant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="dashboard_categories",
                        to="accounts.restaurant",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="created_dashboard_categories",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "db_table": "dashboard_categories",
                "ordering": ["order_index", "name"],
            },
        ),
        migrations.AddConstraint(
            model_name="dashboardcategory",
            constraint=models.UniqueConstraint(
                fields=("restaurant", "name"),
                name="uniq_dashboard_category_per_restaurant",
            ),
        ),
        migrations.AddIndex(
            model_name="dashboardcategory",
            index=models.Index(
                fields=["restaurant", "order_index"],
                name="dashboard_c_restaur_89def6_idx",
            ),
        ),
        migrations.AddField(
            model_name="dashboardcustomwidget",
            name="category",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="widgets",
                to="dashboard.dashboardcategory",
            ),
        ),
        migrations.AddIndex(
            model_name="dashboardcustomwidget",
            index=models.Index(
                fields=["category"],
                name="dashboard_c_categor_e933cd_idx",
            ),
        ),
    ]
