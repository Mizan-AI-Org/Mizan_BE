from django.db import migrations, models


def enable_auto_clock_out(apps, schema_editor):
    Restaurant = apps.get_model("accounts", "Restaurant")
    Restaurant.objects.filter(automatic_clock_out=False).update(automatic_clock_out=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0037_fix_morocco_country_code"),
    ]

    operations = [
        migrations.AlterField(
            model_name="restaurant",
            name="automatic_clock_out",
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(enable_auto_clock_out, migrations.RunPython.noop),
    ]
