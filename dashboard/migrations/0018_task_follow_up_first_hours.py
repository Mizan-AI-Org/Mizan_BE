from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0017_task_validation_proof_order_station"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="follow_up_first_hours",
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text="Hours until first auto follow-up; null = use priority schedule.",
                null=True,
            ),
        ),
    ]
