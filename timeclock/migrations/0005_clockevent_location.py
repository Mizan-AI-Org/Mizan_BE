import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0029_business_location'),
        ('timeclock', '0004_morocco_features'),
    ]

    operations = [
        migrations.AddField(
            model_name='clockevent',
            name='location',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='clock_events',
                to='accounts.businesslocation',
            ),
        ),
    ]
