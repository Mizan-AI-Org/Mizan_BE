# Generated for manager override audit (performed_by, override_reason)

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('timeclock', '0002_alter_clockevent_location_encrypted'),
    ]

    operations = [
        migrations.AddField(
            model_name='clockevent',
            name='override_reason',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='clockevent',
            name='performed_by',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='clock_events_performed',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
