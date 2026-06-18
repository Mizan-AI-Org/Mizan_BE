from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0002_invoice_bank_payment_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="attachment",
            field=models.FileField(
                blank=True,
                help_text="Original invoice scan (image or PDF) from WhatsApp / upload.",
                null=True,
                upload_to="invoices/",
            ),
        ),
        migrations.AddField(
            model_name="invoice",
            name="attachment_content_type",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
        migrations.AddField(
            model_name="invoice",
            name="attachment_filename",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
