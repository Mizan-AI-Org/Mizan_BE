from django.db import migrations, models


def copy_primary_to_assignees(apps, schema_editor):
    Task = apps.get_model("dashboard", "Task")
    for task in Task.objects.filter(assigned_to_id__isnull=False).iterator():
        task.assignees.add(task.assigned_to_id)


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0024_task_routing_metadata"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="assignees",
            field=models.ManyToManyField(
                blank=True,
                help_text="All staff assigned to this task; primary FK mirrors assignees[0].",
                related_name="dashboard_task_assignments",
                to="accounts.customuser",
            ),
        ),
        migrations.RunPython(copy_primary_to_assignees, migrations.RunPython.noop),
    ]
