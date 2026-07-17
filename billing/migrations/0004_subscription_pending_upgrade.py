# Generated manually for pending upgrade fields

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("billing", "0003_seed_subscription_plans"),
    ]

    operations = [
        migrations.AddField(
            model_name="subscription",
            name="pending_billing_interval",
            field=models.CharField(blank=True, default="", max_length=10),
        ),
        migrations.AddField(
            model_name="subscription",
            name="pending_plan",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pending_subscriptions",
                to="billing.subscriptionplan",
            ),
        ),
    ]
