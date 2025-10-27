# Generated migration for Task models

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0001_initial'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('accounts', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='TaskCategory',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=100)),
                ('description', models.TextField(blank=True, null=True)),
                ('color', models.CharField(default='#3B82F6', max_length=7)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('restaurant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='task_categories', to='accounts.restaurant')),
            ],
            options={
                'verbose_name_plural': 'Task Categories',
                'db_table': 'task_categories',
            },
        ),
        migrations.CreateModel(
            name='ShiftTask',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True, null=True)),
                ('priority', models.CharField(choices=[('LOW', 'Low'), ('MEDIUM', 'Medium'), ('HIGH', 'High'), ('URGENT', 'Urgent')], default='MEDIUM', max_length=20)),
                ('status', models.CharField(choices=[('TODO', 'To Do'), ('IN_PROGRESS', 'In Progress'), ('COMPLETED', 'Completed'), ('CANCELLED', 'Cancelled')], default='TODO', max_length=20)),
                ('estimated_duration', models.DurationField(blank=True, null=True)),
                ('notes', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('assigned_to', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_tasks', to=settings.AUTH_USER_MODEL)),
                ('category', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='tasks', to='scheduling.taskcategory')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_tasks', to=settings.AUTH_USER_MODEL)),
                ('parent_task', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='subtasks', to='scheduling.shifttask')),
                ('shift', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tasks', to='scheduling.assignedshift')),
            ],
            options={
                'db_table': 'shift_tasks',
                'ordering': ['-priority', 'created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='shifttask',
            index=models.Index(fields=['shift', 'status'], name='shift_tasks_shift_id_status_idx'),
        ),
        migrations.AddIndex(
            model_name='shifttask',
            index=models.Index(fields=['assigned_to', 'status'], name='shift_tasks_assigned_to_status_idx'),
        ),
        migrations.AlterUniqueTogether(
            name='taskcategory',
            unique_together={('restaurant', 'name')},
        ),
    ]