# Generated manually for ACCEPTED / UNABLE_TO_COMPLETE + completion audit fields.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("dashboard", "0018_task_follow_up_first_hours"),
    ]

    operations = [
        migrations.AlterField(
            model_name="task",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pending"),
                    ("ACCEPTED", "Accepted"),
                    ("IN_PROGRESS", "In Progress"),
                    ("COMPLETED", "Completed"),
                    ("UNABLE_TO_COMPLETE", "Unable to Complete"),
                    ("CANCELLED", "Cancelled"),
                ],
                default="PENDING",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="completed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="task",
            name="completed_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="dashboard_tasks_completed",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="attachment",
            field=models.FileField(
                blank=True,
                null=True,
                upload_to="task-attachments/%Y/%m/",
            ),
        ),
        migrations.AddField(
            model_name="task",
            name="attachment_url",
            field=models.URLField(blank=True, default="", max_length=1000),
        ),
    ]
