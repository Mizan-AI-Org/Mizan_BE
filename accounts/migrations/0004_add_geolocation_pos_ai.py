# Generated migration for geolocation, POS, and AI features

from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_delete_staffavailability'),
    ]

    operations = [
        # Update Restaurant model
        migrations.AddField(
            model_name='restaurant',
            name='geofence_enabled',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='restaurant',
            name='geofence_polygon',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterField(
            model_name='restaurant',
            name='radius',
            field=models.DecimalField(blank=True, decimal_places=2, default=500, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='restaurant',
            name='pos_provider',
            field=models.CharField(choices=[('STRIPE', 'Stripe'), ('SQUARE', 'Square'), ('CLOVER', 'Clover'), ('CUSTOM', 'Custom API'), ('NONE', 'Not Configured')], default='NONE', max_length=50),
        ),
        migrations.AddField(
            model_name='restaurant',
            name='pos_merchant_id',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='restaurant',
            name='pos_api_key',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='restaurant',
            name='pos_is_connected',
            field=models.BooleanField(default=False),
        ),
        
        # Update StaffProfile model
        migrations.AddField(
            model_name='staffprofile',
            name='last_location_latitude',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='staffprofile',
            name='last_location_longitude',
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name='staffprofile',
            name='last_location_timestamp',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='staffprofile',
            name='geofence_alerts_enabled',
            field=models.BooleanField(default=True),
        ),
        
        # Create POSIntegration model
        migrations.CreateModel(
            name='POSIntegration',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('last_sync_time', models.DateTimeField(blank=True, null=True)),
                ('sync_status', models.CharField(choices=[('CONNECTED', 'Connected'), ('DISCONNECTED', 'Disconnected'), ('ERROR', 'Error'), ('SYNCING', 'Syncing')], default='DISCONNECTED', max_length=20)),
                ('total_transactions_synced', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('restaurant', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='pos_integration', to='accounts.restaurant')),
            ],
            options={
                'db_table': 'pos_integrations',
            },
        ),
        
        # Create AIAssistantConfig model
        migrations.CreateModel(
            name='AIAssistantConfig',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('enabled', models.BooleanField(default=True)),
                ('ai_provider', models.CharField(choices=[('GROQ', 'Groq'), ('OPENAI', 'OpenAI'), ('CLAUDE', 'Claude')], default='GROQ', max_length=50)),
                ('api_key', models.CharField(blank=True, max_length=500, null=True)),
                ('features_enabled', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('restaurant', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='ai_config', to='accounts.restaurant')),
            ],
            options={
                'db_table': 'ai_assistant_configs',
            },
        ),
    ]