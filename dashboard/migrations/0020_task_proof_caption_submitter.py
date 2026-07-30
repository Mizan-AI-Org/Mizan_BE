# Generated manually for photo-proof caption + submitter audit fields.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("dashboard", "0019_task_lifecycle_statuses"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="proof_caption",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="task",
            name="proof_submitted_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="dashboard_tasks_proofs_submitted",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
