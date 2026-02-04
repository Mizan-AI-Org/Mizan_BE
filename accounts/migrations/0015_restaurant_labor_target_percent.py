# Generated for sales -> labor recommendation

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0014_restaurant_max_weekly_hours_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='restaurant',
            name='labor_target_percent',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
        ),
    ]
