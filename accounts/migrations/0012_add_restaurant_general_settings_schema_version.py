# Generated migration to add Restaurant.general_settings and settings_schema_version

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0011_customuser_account_locked_until_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='restaurant',
            name='general_settings',
            field=models.JSONField(default=dict, blank=True),
        ),
        migrations.AddField(
            model_name='restaurant',
            name='settings_schema_version',
            field=models.IntegerField(default=1),
        ),
    ]