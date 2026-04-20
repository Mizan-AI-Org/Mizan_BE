from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0002_morocco_features"),
    ]

    operations = [
        migrations.AddField(
            model_name="supplier",
            name="lead_time_days",
            field=models.PositiveSmallIntegerField(
                default=2,
                help_text=(
                    "Days between placing an order and receiving it. "
                    "Drives the prep list's 'order-by' date."
                ),
            ),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="pack_size",
            field=models.DecimalField(
                max_digits=10,
                decimal_places=3,
                null=True,
                blank=True,
                help_text="Units per case/pack (e.g. 12 for a 12-pack, 5 for a 5 kg sack).",
            ),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="min_order_qty",
            field=models.DecimalField(
                max_digits=10,
                decimal_places=3,
                null=True,
                blank=True,
                help_text="Supplier minimum order quantity in this item's unit.",
            ),
        ),
        migrations.AddField(
            model_name="inventoryitem",
            name="shelf_life_days",
            field=models.PositiveSmallIntegerField(
                null=True,
                blank=True,
                help_text=(
                    "Days the item stays usable once received. Perishables "
                    "(e.g. fresh fish) = 1\u20132; dry goods can be left blank."
                ),
            ),
        ),
    ]
