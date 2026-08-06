from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("platform_admin", "0002_platformwhatsappconfig_disconnected_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="platformwhatsappconfig",
            name="miya_fish_reference_id",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="platformwhatsappconfig",
            name="miya_fish_model",
            field=models.CharField(blank=True, default="s2.1-pro", max_length=32),
        ),
        migrations.AddField(
            model_name="platformwhatsappconfig",
            name="miya_voice_speed",
            field=models.FloatField(default=1.05),
        ),
        migrations.AddField(
            model_name="platformwhatsappconfig",
            name="miya_voice_label",
            field=models.CharField(blank=True, default="Sarah", max_length=64),
        ),
        migrations.AddField(
            model_name="platformwhatsappconfig",
            name="miya_openai_fallback_voice",
            field=models.CharField(blank=True, default="shimmer", max_length=32),
        ),
    ]
