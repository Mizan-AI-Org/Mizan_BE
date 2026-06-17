# Generated manually for P1 payroll features

import uuid

import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("accounts", "0034_staffprofile_tags"),
    ]

    operations = [
        migrations.CreateModel(
            name="ComplianceReminder",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("code", models.CharField(max_length=64)),
                ("title", models.CharField(max_length=255)),
                ("description", models.TextField(blank=True, default="")),
                (
                    "category",
                    models.CharField(
                        choices=[("CNSS", "CNSS"), ("TAX", "Tax"), ("LABOR", "Labor"), ("OTHER", "Other")],
                        default="OTHER",
                        max_length=16,
                    ),
                ),
                ("due_date", models.DateField()),
                ("remind_days_before", models.PositiveSmallIntegerField(default=7)),
                (
                    "status",
                    models.CharField(
                        choices=[("UPCOMING", "Upcoming"), ("NOTIFIED", "Notified"), ("DONE", "Done")],
                        default="UPCOMING",
                        max_length=16,
                    ),
                ),
                ("last_notified_at", models.DateTimeField(blank=True, null=True)),
                ("external_id", models.CharField(blank=True, db_index=True, default="", max_length=128)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "restaurant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="compliance_reminders",
                        to="accounts.restaurant",
                    ),
                ),
            ],
            options={"ordering": ["due_date", "title"]},
        ),
        migrations.CreateModel(
            name="DeliveryMenuSnapshot",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "provider",
                    models.CharField(choices=[("GLOVO", "Glovo")], default="GLOVO", max_length=32),
                ),
                ("item_count", models.PositiveIntegerField(default=0)),
                ("payload", models.JSONField(blank=True, default=dict)),
                ("synced_at", models.DateTimeField(auto_now_add=True)),
                (
                    "restaurant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="delivery_menu_snapshots",
                        to="accounts.restaurant",
                    ),
                ),
            ],
            options={"ordering": ["-synced_at"]},
        ),
        migrations.CreateModel(
            name="Payslip",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("hours_worked", models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ("hourly_rate", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("gross_pay", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("currency", models.CharField(default="MAD", max_length=8)),
                (
                    "status",
                    models.CharField(
                        choices=[("DRAFT", "Draft"), ("ISSUED", "Issued")],
                        default="ISSUED",
                        max_length=12,
                    ),
                ),
                ("notes", models.TextField(blank=True, default="")),
                ("pdf_url", models.URLField(blank=True, default="", max_length=1024)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="payslips_created",
                        to="accounts.customuser",
                    ),
                ),
                (
                    "restaurant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payslips",
                        to="accounts.restaurant",
                    ),
                ),
                (
                    "staff",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payslips",
                        to="accounts.customuser",
                    ),
                ),
            ],
            options={"ordering": ["-period_end", "staff__last_name"]},
        ),
        migrations.CreateModel(
            name="TemperatureReading",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("equipment", models.CharField(max_length=120)),
                ("value_c", models.DecimalField(decimal_places=2, max_digits=5)),
                ("recorded_at", models.DateTimeField(default=django.utils.timezone.now)),
                (
                    "source",
                    models.CharField(
                        choices=[("WHATSAPP", "WhatsApp"), ("CHECKLIST", "Checklist"), ("MANUAL", "Manual")],
                        default="WHATSAPP",
                        max_length=20,
                    ),
                ),
                ("notes", models.TextField(blank=True, default="")),
                ("is_out_of_range", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "recorded_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="temperature_readings",
                        to="accounts.customuser",
                    ),
                ),
                (
                    "restaurant",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="temperature_readings",
                        to="accounts.restaurant",
                    ),
                ),
            ],
            options={"ordering": ["-recorded_at"]},
        ),
        migrations.AddIndex(
            model_name="compliancereminder",
            index=models.Index(fields=["restaurant", "due_date", "status"], name="payroll_com_rest_due_idx"),
        ),
        migrations.AddIndex(
            model_name="payslip",
            index=models.Index(fields=["restaurant", "period_start", "period_end"], name="payroll_pay_rest_per_idx"),
        ),
        migrations.AddIndex(
            model_name="payslip",
            index=models.Index(fields=["restaurant", "staff", "period_end"], name="payroll_pay_staff_idx"),
        ),
        migrations.AddIndex(
            model_name="temperaturereading",
            index=models.Index(fields=["restaurant", "recorded_at"], name="payroll_temp_rest_dt_idx"),
        ),
        migrations.AlterUniqueTogether(
            name="payslip",
            unique_together={("restaurant", "staff", "period_start", "period_end")},
        ),
    ]
