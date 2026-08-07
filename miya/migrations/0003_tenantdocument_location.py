from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0038_restaurant_automatic_clock_out_default_true"),
        ("miya", "0002_tenantdocument_structured"),
    ]

    operations = [
        migrations.AddField(
            model_name="tenantdocument",
            name="location",
            field=models.ForeignKey(
                blank=True,
                help_text="Establishment this upload belongs to (multi-site scoping).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tenant_documents",
                to="accounts.businesslocation",
            ),
        ),
    ]
