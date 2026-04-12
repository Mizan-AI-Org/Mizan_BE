from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0025_customuser_dashboard_widget_order"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="custom_role_label",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="staffactivationrecord",
            name="custom_role_label",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
