from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0020_morocco_features"),
    ]

    operations = [
        migrations.AddField(
            model_name="restaurant",
            name="reservation_oauth_data",
            field=models.TextField(blank=True, null=True, help_text="Encrypted JSON for reservation provider secrets (e.g. Eat Now API key)."),
        ),
    ]
