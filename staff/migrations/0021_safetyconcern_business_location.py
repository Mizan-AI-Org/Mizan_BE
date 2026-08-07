from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0038_restaurant_automatic_clock_out_default_true"),
        ("staff", "0020_safetyconcern_photo_evidence"),
    ]

    operations = [
        migrations.AddField(
            model_name="safetyconcernreport",
            name="business_location",
            field=models.ForeignKey(
                blank=True,
                help_text="Establishment this incident belongs to (multi-site scoping).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="safety_concerns",
                to="accounts.businesslocation",
            ),
        ),
        migrations.AddIndex(
            model_name="safetyconcernreport",
            index=models.Index(
                fields=["restaurant", "business_location"],
                name="staff_sc_rest_bloc_idx",
            ),
        ),
    ]
