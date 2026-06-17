from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0011_task_follow_up_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="escalated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
