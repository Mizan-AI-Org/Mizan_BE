# Generated manually for Phase 14.3.1 document versioning

from django.db import migrations, models
import django.db.models.deletion


def backfill_document_families(apps, schema_editor):
    TenantDocument = apps.get_model("miya", "TenantDocument")
    for doc in TenantDocument.objects.filter(document_family_id__isnull=True).iterator():
        TenantDocument.objects.filter(pk=doc.pk).update(
            document_family_id=doc.pk,
            is_current=True,
            version_number=1,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("miya", "0005_remove_tenantdocument_miya_tenant_vend_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantdocument",
            name="content_hash",
            field=models.CharField(
                blank=True,
                db_index=True,
                default="",
                help_text="SHA-256 hex digest of stored file bytes.",
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="tenantdocument",
            name="supersedes",
            field=models.ForeignKey(
                blank=True,
                help_text="Prior version this upload replaces (same logical document family).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="superseded_by_versions",
                to="miya.tenantdocument",
            ),
        ),
        migrations.AddField(
            model_name="tenantdocument",
            name="document_family_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="tenantdocument",
            name="is_current",
            field=models.BooleanField(db_index=True, default=True),
        ),
        migrations.AddField(
            model_name="tenantdocument",
            name="version_number",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="tenantdocument",
            name="processing_status",
            field=models.CharField(
                choices=[
                    ("pending", "Pending"),
                    ("ok", "OK"),
                    ("failed", "Failed"),
                    ("skipped", "Skipped"),
                ],
                default="pending",
                max_length=16,
            ),
        ),
        migrations.AddIndex(
            model_name="tenantdocument",
            index=models.Index(
                fields=["restaurant", "content_hash"],
                name="miya_tenant_rest_hash_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="tenantdocument",
            index=models.Index(
                fields=["restaurant", "document_family_id", "is_current"],
                name="miya_tenant_fam_cur_idx",
            ),
        ),
        migrations.RunPython(backfill_document_families, migrations.RunPython.noop),
    ]
