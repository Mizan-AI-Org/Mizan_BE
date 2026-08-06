# Generated manually for compliance-linked reminder approach nudges.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payroll", "0003_compliance_document"),
        ("scheduling", "0023_shiftchecklistprogress_completion_summary"),
    ]

    operations = [
        migrations.AddField(
            model_name="personalreminder",
            name="approach_nudges_sent",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Milestone days-before-due already pinged on WhatsApp (e.g. 30, 7, 1, 0).",
            ),
        ),
        migrations.AddField(
            model_name="personalreminder",
            name="linked_compliance_document",
            field=models.ForeignKey(
                blank=True,
                help_text="When set, due_at tracks document expiry and Miya sends approach nudges.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="personal_reminders",
                to="payroll.compliancedocument",
            ),
        ),
    ]
