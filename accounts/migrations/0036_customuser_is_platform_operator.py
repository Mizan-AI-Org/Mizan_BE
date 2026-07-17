# Generated manually for platform operator gate

from django.db import migrations, models


def seed_platform_operators(apps, schema_editor):
    User = apps.get_model("accounts", "CustomUser")
    # Only dedicated staff accounts with no restaurant become operators.
    # Restaurant SUPER_ADMIN / accidental is_staff must NOT get SPA /admin.
    User.objects.filter(is_staff=True, restaurant__isnull=True).update(
        is_platform_operator=True
    )
    User.objects.filter(restaurant__isnull=False).update(is_platform_operator=False)


def noop_reverse(apps, schema_editor):
    User = apps.get_model("accounts", "CustomUser")
    User.objects.filter(is_platform_operator=True).update(is_platform_operator=False)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0035_staffprofile_monthly_salary"),
    ]

    operations = [
        migrations.AddField(
            model_name="customuser",
            name="is_platform_operator",
            field=models.BooleanField(
                db_index=True,
                default=False,
                help_text="May access SPA Platform Admin (/admin). Independent of restaurant SUPER_ADMIN role.",
            ),
        ),
        migrations.RunPython(seed_platform_operators, noop_reverse),
    ]
