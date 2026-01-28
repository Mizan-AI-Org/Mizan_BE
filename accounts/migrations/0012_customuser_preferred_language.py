from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0011_password_reset_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="preferred_language",
            field=models.CharField(
                blank=True,
                choices=[("en", "English"), ("fr", "French"), ("ar", "Arabic")],
                max_length=10,
                null=True,
            ),
        ),
    ]

