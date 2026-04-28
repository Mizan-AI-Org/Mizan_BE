"""Add ``Task.category`` so dashboard widgets can bucket Miya-created tasks.

The Human Resources / Finance / Maintenance / Meetings dashboard widgets
each show top tasks for one bucket. This new column stores that bucket
explicitly so the widget query is a cheap indexed filter rather than a
re-classification at read time.

Auto-populated by ``staff.intent_router.classify_request`` when the agent
endpoint creates a task; nullable so legacy rows (and manually created
tasks where the manager didn't pick a category) don't fail the migration.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0009_drop_attendance_shift_reviews'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='category',
            field=models.CharField(
                blank=True,
                choices=[
                    ('DOCUMENT', 'Document'),
                    ('HR', 'HR'),
                    ('SCHEDULING', 'Scheduling'),
                    ('PAYROLL', 'Payroll'),
                    ('FINANCE', 'Finance'),
                    ('OPERATIONS', 'Operations'),
                    ('MAINTENANCE', 'Maintenance'),
                    ('RESERVATIONS', 'Reservations'),
                    ('INVENTORY', 'Inventory'),
                    ('MEETING', 'Meeting / Reminder'),
                    ('OTHER', 'Other'),
                ],
                db_index=True,
                max_length=20,
                null=True,
            ),
        ),
    ]
