# Generated manually for checklist progress in DB

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('scheduling', '0010_assignedshift_is_recurring_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ShiftChecklistProgress',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('channel', models.CharField(blank=True, default='whatsapp', max_length=20)),
                ('phone', models.CharField(blank=True, db_index=True, help_text='WhatsApp phone for recovery', max_length=20)),
                ('task_ids', models.JSONField(default=list, help_text='Ordered list of ShiftTask IDs')),
                ('current_task_id', models.CharField(blank=True, max_length=36)),
                ('responses', models.JSONField(default=dict, help_text='task_id -> response (yes/no/n_a)')),
                ('status', models.CharField(choices=[('IN_PROGRESS', 'In Progress'), ('COMPLETED', 'Completed'), ('CANCELLED', 'Cancelled')], db_index=True, default='IN_PROGRESS', max_length=20)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('shift', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='checklist_progress', to='scheduling.assignedshift')),
                ('staff', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='shift_checklist_progress', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'shift_checklist_progress',
                'ordering': ['-created_at'],
                'unique_together': {('shift', 'staff')},
            },
        ),
        migrations.AddIndex(
            model_name='shiftchecklistprogress',
            index=models.Index(fields=['staff', 'status'], name='shift_check_staff_i_idx'),
        ),
        migrations.AddIndex(
            model_name='shiftchecklistprogress',
            index=models.Index(fields=['phone', 'status'], name='shift_check_phone__idx'),
        ),
    ]
