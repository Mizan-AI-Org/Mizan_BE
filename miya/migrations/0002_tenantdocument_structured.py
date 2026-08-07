# Generated manually for Phase 6 structured document intelligence

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("miya", "0001_tenant_document"),
        ("finance", "0010_invoice_audit_event_choices"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantdocument",
            name="structured_fields",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="tenantdocument",
            name="vendor_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="tenantdocument",
            name="amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name="tenantdocument",
            name="currency",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AddField(
            model_name="tenantdocument",
            name="invoice_number",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="tenantdocument",
            name="expiry_date",
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tenantdocument",
            name="invoice",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tenant_documents",
                to="finance.invoice",
            ),
        ),
        migrations.AddIndex(
            model_name="tenantdocument",
            index=models.Index(fields=["restaurant", "vendor_name"], name="miya_tenant_vend_idx"),
        ),
        migrations.AddIndex(
            model_name="tenantdocument",
            index=models.Index(fields=["restaurant", "expiry_date"], name="miya_tenant_exp_idx"),
        ),
    ]
