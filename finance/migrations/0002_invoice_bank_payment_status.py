from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("finance", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="bank_payment_note",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="invoice",
            name="bank_payment_status",
            field=models.CharField(
                choices=[
                    ("NOT_APPLICABLE", "Not applicable"),
                    ("PENDING", "Pending"),
                    ("INITIATED", "Initiated"),
                    ("CLEARED", "Cleared"),
                    ("FAILED", "Failed"),
                ],
                default="PENDING",
                help_text="Tracks bank transfer / cheque payment lifecycle before/after mark paid.",
                max_length=20,
            ),
        ),
    ]
