from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("staff", "0016_staffrequest_medical_category"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffrequest",
            name="escalated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="staffrequest",
            name="follow_up_count",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="staffrequest",
            name="follow_up_enabled",
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name="staffrequest",
            name="follow_up_max",
            field=models.PositiveSmallIntegerField(default=2),
        ),
        migrations.AddField(
            model_name="staffrequest",
            name="last_follow_up_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="staffrequest",
            name="whatsapp_notified_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When the initial WhatsApp notification was sent to the assignee.",
                null=True,
            ),
        ),
    ]
