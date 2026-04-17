from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0024_eatnow_reservation_model"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="dashboard_widget_order",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
