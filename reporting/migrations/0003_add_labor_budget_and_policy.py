# Generated for labor budget and policy models

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0014_restaurant_max_weekly_hours_and_more'),
        ('reporting', '0002_incident'),
    ]

    operations = [
        migrations.CreateModel(
            name='LaborBudget',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('period_start', models.DateField()),
                ('period_end', models.DateField()),
                ('target_hours', models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True)),
                ('target_amount', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
                ('currency', models.CharField(default='USD', max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('restaurant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='labor_budgets', to='accounts.restaurant')),
            ],
            options={
                'db_table': 'labor_budgets',
                'ordering': ['-period_end'],
            },
        ),
        migrations.CreateModel(
            name='LaborPolicy',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('max_hours_per_week', models.DecimalField(blank=True, decimal_places=2, default=40, max_digits=5, null=True)),
                ('max_hours_per_day', models.DecimalField(blank=True, decimal_places=2, default=8, max_digits=4, null=True)),
                ('min_rest_hours_between_shifts', models.DecimalField(blank=True, decimal_places=2, default=11, max_digits=4, null=True)),
                ('break_required_after_hours', models.DecimalField(blank=True, decimal_places=2, default=6, max_digits=4, null=True)),
                ('overtime_after_hours_per_week', models.DecimalField(blank=True, decimal_places=2, default=40, max_digits=5, null=True)),
                ('late_threshold_minutes', models.IntegerField(default=15, help_text='Minutes after shift start to count as late')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('restaurant', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='labor_policy', to='accounts.restaurant')),
            ],
            options={
                'db_table': 'labor_policies',
            },
        ),
        migrations.AddIndex(
            model_name='laborbudget',
            index=models.Index(fields=['restaurant', 'period_start', 'period_end'], name='labor_budge_restaur_idx'),
        ),
    ]
