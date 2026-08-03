# Generated manually for Invoice assignee + OCR confidence

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0007_s3_org_scoped_upload_paths"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="assigned_to",
            field=models.ForeignKey(
                blank=True,
                help_text="Staff responsible for paying / chasing this invoice.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="invoices_assigned",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="assigned_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="invoice",
            name="assigned_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="invoices_assigned_by",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="ocr_confidence",
            field=models.DecimalField(
                blank=True,
                decimal_places=3,
                help_text="0–1 classification/extraction confidence when created from a photo/scan.",
                max_digits=4,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="ocr_fields",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Raw extracted fields + per-field notes from OCR/vision.",
            ),
        ),
    ]
