from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('timeclock', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='clockevent',
            name='location_encrypted',
            field=models.TextField(blank=True),
        ),
    ]