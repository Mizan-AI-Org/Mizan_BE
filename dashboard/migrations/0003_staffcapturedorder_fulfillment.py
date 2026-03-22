# Generated manually

import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0002_staff_captured_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffcapturedorder",
            name="fulfillment_status",
            field=models.CharField(
                choices=[
                    ("NEW", "New"),
                    ("IN_PROGRESS", "In progress"),
                    ("FULFILLED", "Fulfilled"),
                    ("CANCELLED", "Cancelled"),
                ],
                default="NEW",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="staffcapturedorder",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=django.utils.timezone.now),
            preserve_default=False,
        ),
    ]
