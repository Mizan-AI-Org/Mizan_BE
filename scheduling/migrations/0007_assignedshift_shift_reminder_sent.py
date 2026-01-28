from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0006_remove_assignedshift_assigned_sh_staff_i_dec37e_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="assignedshift",
            name="shift_reminder_sent",
            field=models.BooleanField(default=False),
        ),
    ]

