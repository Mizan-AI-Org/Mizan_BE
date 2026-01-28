from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("scheduling", "0007_assignedshift_shift_reminder_sent"),
    ]

    operations = [
        migrations.AddField(
            model_name="tasktemplate",
            name="i18n",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]

