from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0034_staffprofile_tags"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffprofile",
            name="monthly_salary",
            field=models.DecimalField(
                decimal_places=2,
                default=0,
                help_text="Fixed monthly gross when salary_type is MONTHLY.",
                max_digits=12,
            ),
        ),
    ]
