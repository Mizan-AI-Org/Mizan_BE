# Generated manually for location_encrypted blank/default

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('timeclock', '0001_initial'),
    ]

    operations = [
        migrations.AlterField(
            model_name='clockevent',
            name='location_encrypted',
            field=models.TextField(blank=True, db_column='location_encrypted', default=''),
        ),
    ]
