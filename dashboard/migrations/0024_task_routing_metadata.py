from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0023_task_created_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="routing_metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
