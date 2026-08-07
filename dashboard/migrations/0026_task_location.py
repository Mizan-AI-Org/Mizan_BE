from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0038_restaurant_automatic_clock_out_default_true"),
        ("dashboard", "0025_task_assignees"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="location",
            field=models.ForeignKey(
                blank=True,
                help_text="Establishment/branch this task belongs to (multi-site scoping).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="dashboard_tasks",
                to="accounts.businesslocation",
            ),
        ),
        migrations.AddIndex(
            model_name="task",
            index=models.Index(fields=["restaurant", "location"], name="dash_task_rest_loc_idx"),
        ),
    ]
