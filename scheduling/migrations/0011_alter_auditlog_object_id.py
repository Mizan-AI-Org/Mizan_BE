# Generated manually to fix UUID support in audit log

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduling', '0010_add_audit_log_model'),
    ]

    operations = [
        migrations.AlterField(
            model_name='auditlog',
            name='object_id',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]