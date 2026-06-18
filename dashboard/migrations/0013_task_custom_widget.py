from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0012_task_escalated_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="custom_widget",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tasks",
                to="dashboard.dashboardcustomwidget",
            ),
        ),
        migrations.AddIndex(
            model_name="task",
            index=models.Index(
                fields=["restaurant", "custom_widget", "status"],
                name="dashboard_t_rest_cu_status_idx",
            ),
        ),
    ]
