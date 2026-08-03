# Generated manually — Task.created_by for Operations Live "From"

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0022_staff_daily_progress_report"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                help_text="Person who asked Miya / created this demand (From column).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="dashboard_tasks_created",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
