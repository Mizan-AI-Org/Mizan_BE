from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("platform_admin", "0001_whatsapp_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformwhatsappconfig",
            name="disconnected_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
