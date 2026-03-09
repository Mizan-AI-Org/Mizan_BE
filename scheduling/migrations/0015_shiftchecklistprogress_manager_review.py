# Generated migration for manager review fields on ShiftChecklistProgress

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('scheduling', '0014_morocco_features'),
    ]

    operations = [
        migrations.AddField(
            model_name='shiftchecklistprogress',
            name='manager_notes',
            field=models.TextField(blank=True, null=True, help_text='Manager comments or feedback on this submission'),
        ),
        migrations.AddField(
            model_name='shiftchecklistprogress',
            name='supervisor_approved',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='shiftchecklistprogress',
            name='approved_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='approved_shift_checklists', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='shiftchecklistprogress',
            name='approved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
