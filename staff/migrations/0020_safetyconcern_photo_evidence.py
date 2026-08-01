from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("staff", "0019_safetyconcern_attachment"),
    ]

    operations = [
        migrations.AddField(
            model_name="safetyconcernreport",
            name="photo_evidence",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Durable photo evidence entries (WhatsApp, uploads) with storage URLs",
            ),
        ),
    ]
