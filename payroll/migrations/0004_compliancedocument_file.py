from django.db import migrations, models

import core.storage_paths


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0003_compliance_document"),
    ]

    operations = [
        migrations.AddField(
            model_name="compliancedocument",
            name="file",
            field=models.FileField(
                blank=True,
                default="",
                help_text="Scanned certificate / permit file (PDF or image).",
                upload_to=core.storage_paths.compliance_document_upload_path,
            ),
        ),
    ]
