# Platform ops metadata on subscriptions (plan-change reasons, etc.)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0004_subscription_pending_upgrade"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscription",
            name="platform_ops",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
