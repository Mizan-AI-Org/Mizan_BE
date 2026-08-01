from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0022_s3_org_scoped_upload_paths"),
    ]

    operations = [
        migrations.AddField(
            model_name="shiftchecklistprogress",
            name="completion_summary",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text="Archived snapshot at completion: tasks, responses, photos, compliance",
            ),
        ),
    ]
