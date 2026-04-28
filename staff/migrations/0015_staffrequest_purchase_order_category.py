"""Add the PURCHASE_ORDER category to ``StaffRequest.CATEGORY_CHOICES``.

Procurement asks ("we need to buy 6 bottles of vodka", "order 50 kg of
flour from Acme") were previously falling into INVENTORY, which is a
*stock-state* lane (low/out-of-stock observations). The two read very
differently to a manager and so the dashboard now has a dedicated
"Purchase Orders" widget. This migration introduces the choice that
backs that widget.

Schema-only ``choices`` change → no data migration required; existing
rows are unaffected.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('staff', '0014_staffrequest_waiting_on'),
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
                    ('PURCHASE_ORDER', 'Purchase order'),
                    ('OTHER', 'Other'),
                ],
                default='OTHER',
                max_length=20,
            ),
        ),
    ]
