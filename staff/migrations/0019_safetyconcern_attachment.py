from django.db import migrations, models

import core.storage_paths


class Migration(migrations.Migration):

    dependencies = [
        ("staff", "0018_s3_org_scoped_upload_paths"),
    ]

    operations = [
        migrations.AddField(
            model_name="safetyconcernreport",
            name="attachment",
            field=models.FileField(
                blank=True,
                help_text="Non-image evidence (PDF, etc.)",
                null=True,
                upload_to=core.storage_paths.incident_attachment_upload_path,
            ),
        ),
        migrations.AddField(
            model_name="safetyconcernreport",
            name="attachment_content_type",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="safetyconcernreport",
            name="attachment_filename",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
