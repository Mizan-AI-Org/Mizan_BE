"""Add the FINANCE category to ``StaffRequest.CATEGORY_CHOICES``.

The Finance dashboard widget needs a first-class bucket for invoices to
pay, supplier bills, taxes, rent, and utilities. PAYROLL stays separate
because staff payslip questions are a fundamentally different workflow
(employees → manager → finance) than vendor bills (manager → finance).

Schema-only ``choices`` change → no data migration required; existing
rows are unaffected.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('staff', '0012_intelligent_inbox_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='staffrequest',
            name='category',
            field=models.CharField(
                choices=[
                    ('DOCUMENT', 'Document'),
                    ('HR', 'HR'),
                    ('SCHEDULING', 'Scheduling'),
                    ('PAYROLL', 'Payroll'),
                    ('FINANCE', 'Finance'),
                    ('OPERATIONS', 'Operations'),
                    ('MAINTENANCE', 'Maintenance'),
                    ('RESERVATIONS', 'Reservations'),
                    ('INVENTORY', 'Inventory'),
                    ('OTHER', 'Other'),
                ],
                default='OTHER',
                max_length=20,
            ),
        ),
    ]
