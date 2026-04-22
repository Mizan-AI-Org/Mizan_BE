from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('billing', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='subscriptionplan',
            name='tier',
            field=models.CharField(
                choices=[
                    ('FREE', 'Free'),
                    ('STARTER', 'Starter'),
                    ('GROWTH', 'Growth'),
                    ('ENTERPRISE', 'Enterprise'),
                ],
                db_index=True,
                default='STARTER',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='subscriptionplan',
            name='description',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AddField(
            model_name='subscriptionplan',
            name='price_monthly',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='subscriptionplan',
            name='price_yearly',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='subscriptionplan',
            name='stripe_price_id_monthly',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='subscriptionplan',
            name='stripe_price_id_yearly',
            field=models.CharField(blank=True, default='', max_length=120),
        ),
        migrations.AddField(
            model_name='subscriptionplan',
            name='feature_keys',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='subscriptionplan',
            name='max_locations',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='subscriptionplan',
            name='max_staff',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='subscriptionplan',
            name='badge',
            field=models.CharField(
                blank=True,
                default='',
                help_text="e.g. 'Most popular' — shown above the card on the pricing page.",
                max_length=40,
            ),
        ),
        migrations.AddField(
            model_name='subscriptionplan',
            name='highlight',
            field=models.BooleanField(
                default=False,
                help_text='Highlight this tier on the pricing page (ring, shadow).',
            ),
        ),
        migrations.AddField(
            model_name='subscriptionplan',
            name='cta_label',
            field=models.CharField(blank=True, default='', max_length=40),
        ),
        migrations.AddField(
            model_name='subscriptionplan',
            name='sort_order',
            field=models.PositiveSmallIntegerField(db_index=True, default=0),
        ),
        migrations.AddField(
            model_name='subscriptionplan',
            name='contact_sales',
            field=models.BooleanField(
                default=False,
                help_text="If true, the CTA opens a 'Contact sales' flow instead of Stripe checkout.",
            ),
        ),
        migrations.AddField(
            model_name='subscriptionplan',
            name='trial_days',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AlterModelOptions(
            name='subscriptionplan',
            options={'ordering': ['sort_order', 'price_monthly']},
        ),
        migrations.AddField(
            model_name='subscription',
            name='billing_interval',
            field=models.CharField(blank=True, default='', max_length=10),
        ),
        migrations.AddField(
            model_name='subscription',
            name='trial_ends_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
