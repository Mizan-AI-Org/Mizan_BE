# Generated manually — simplify SafetyConcernReport status values

from django.db import migrations, models


def migrate_statuses_forwards(apps, schema_editor):
    SafetyConcernReport = apps.get_model("staff", "SafetyConcernReport")
    MAP = {
        "REPORTED": "OPEN",
        "UNDER_REVIEW": "OPEN",
        "ADDRESSED": "RESOLVED",
        "RESOLVED": "RESOLVED",
        "DISMISSED": "DISMISSED",
    }
    for row in SafetyConcernReport.objects.all().iterator():
        new_status = MAP.get(row.status, "OPEN")
        if row.status != new_status:
            row.status = new_status
            row.save(update_fields=["status"])


def migrate_statuses_backwards(apps, schema_editor):
    SafetyConcernReport = apps.get_model("staff", "SafetyConcernReport")
    MAP = {
        "OPEN": "REPORTED",
        "RESOLVED": "RESOLVED",
        "DISMISSED": "DISMISSED",
    }
    for row in SafetyConcernReport.objects.all().iterator():
        old = MAP.get(row.status, "REPORTED")
        if row.status != old:
            row.status = old
            row.save(update_fields=["status"])


class Migration(migrations.Migration):

    dependencies = [
        ("staff", "0010_safetyconcernreport_assigned_to"),
    ]

    operations = [
        migrations.RunPython(migrate_statuses_forwards, migrate_statuses_backwards),
        migrations.AlterField(
            model_name="safetyconcernreport",
            name="status",
            field=models.CharField(
                choices=[
                    ("OPEN", "Open"),
                    ("RESOLVED", "Resolved"),
                    ("DISMISSED", "Dismissed"),
                ],
                default="OPEN",
                max_length=20,
            ),
        ),
    ]
