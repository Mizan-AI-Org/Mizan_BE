"""
Add provenance fields (source / source_label / ai_summary) and the URGENT
priority choice to dashboard.Task so the new Tasks & Demands dashboard
widget can render WhatsApp / email / Miya-sourced rows with the AI summary
line you see under the title.

Backfill-safe:
- source defaults to MANUAL, matching historical behaviour
- source_label + ai_summary default to empty strings
- URGENT is additive to TASK_PRIORITY; existing HIGH rows are unchanged
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0007_dashboardcategory_customwidget_category'),
    ]

    operations = [
        migrations.AddField(
            model_name='task',
            name='source',
            field=models.CharField(
                blank=True,
                choices=[
                    ('MANUAL', 'Manual'),
                    ('WHATSAPP', 'WhatsApp'),
                    ('EMAIL', 'Email'),
                    ('MIYA', 'Miya AI'),
                    ('SYSTEM', 'System'),
                ],
                default='MANUAL',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='task',
            name='source_label',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='task',
            name='ai_summary',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='task',
            name='priority',
            field=models.CharField(
                choices=[
                    ('LOW', 'Low'),
                    ('MEDIUM', 'Medium'),
                    ('HIGH', 'High'),
                    ('URGENT', 'Urgent'),
                ],
                default='MEDIUM',
                max_length=10,
            ),
        ),
    ]
