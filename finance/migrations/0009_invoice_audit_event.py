import uuid

from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0001_initial"),
        ("finance", "0008_invoice_assigned_to_ocr"),
    ]

    operations = [
        migrations.CreateModel(
            name="InvoiceAuditEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("event_type", models.CharField(db_index=True, max_length=32)),
                ("actor_label", models.CharField(blank=True, default="", max_length=120)),
                ("channel", models.CharField(blank=True, default="system", max_length=20)),
                ("summary", models.TextField(blank=True, default="")),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
                (
                    "actor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=models.deletion.SET_NULL,
                        related_name="invoice_audit_actions",
                        to="accounts.customuser",
                    ),
                ),
                (
                    "invoice",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="audit_events",
                        to="finance.invoice",
                    ),
                ),
                (
                    "restaurant",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="invoice_audit_events",
                        to="accounts.restaurant",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="invoiceauditevent",
            index=models.Index(fields=["invoice", "created_at"], name="finance_inv_invoice_6a1b0d_idx"),
        ),
        migrations.AddIndex(
            model_name="invoiceauditevent",
            index=models.Index(fields=["restaurant", "event_type"], name="finance_inv_restaur_8c4f2a_idx"),
        ),
    ]
