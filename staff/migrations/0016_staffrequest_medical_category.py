"""Add the MEDICAL category to ``StaffRequest.CATEGORY_CHOICES``.

Team medical / occupational-health requests (clinic visits, medical
certificates, health screenings) need their own inbox lane and dashboard
widget — distinct from HR paperwork and scheduling leave.

Schema-only ``choices`` change → no data migration required.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("staff", "0015_staffrequest_purchase_order_category"),
    ]

    operations = [
        migrations.AlterField(
            model_name="staffrequest",
            name="category",
            field=models.CharField(
                choices=[
                    ("DOCUMENT", "Document"),
                    ("HR", "HR"),
                    ("SCHEDULING", "Scheduling"),
                    ("PAYROLL", "Payroll"),
                    ("FINANCE", "Finance"),
                    ("OPERATIONS", "Operations"),
                    ("MAINTENANCE", "Maintenance"),
                    ("RESERVATIONS", "Reservations"),
                    ("INVENTORY", "Inventory"),
                    ("PURCHASE_ORDER", "Purchase order"),
                    ("MEDICAL", "Medical"),
                    ("OTHER", "Other"),
                ],
                default="OTHER",
                max_length=20,
            ),
        ),
    ]
