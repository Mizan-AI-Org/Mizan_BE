# Generated manually for WhatsApp idempotency and unique phone on WhatsAppSession

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('notifications', '0004_alter_notification_notification_type'),
    ]

    operations = [
        migrations.CreateModel(
            name='WhatsAppMessageProcessed',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('wamid', models.CharField(db_index=True, max_length=255, unique=True)),
                ('processed_at', models.DateTimeField(auto_now_add=True)),
                ('channel', models.CharField(default='whatsapp', max_length=20)),
            ],
            options={
                'db_table': 'whatsapp_message_processed',
                'verbose_name': 'WhatsApp Message Processed',
                'verbose_name_plural': 'WhatsApp Messages Processed',
            },
        ),
        migrations.AlterField(
            model_name='whatsappsession',
            name='phone',
            field=models.CharField(db_index=True, max_length=20, unique=True),
        ),
    ]
