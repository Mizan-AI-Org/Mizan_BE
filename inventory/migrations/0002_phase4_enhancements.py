# Generated migration for Phase 4 Inventory Enhancements

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0001_initial'),
        ('menu', '0001_initial'),
        ('accounts', '0005_rbac_models'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Create StockLevel model
        migrations.CreateModel(
            name='StockLevel',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('current_quantity', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('reserved_quantity', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('available_quantity', models.DecimalField(decimal_places=2, default=0, max_digits=10)),
                ('last_counted', models.DateTimeField(blank=True, null=True)),
                ('last_restocked', models.DateTimeField(blank=True, null=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('inventory_item', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='stock_level', to='inventory.inventoryitem')),
            ],
            options={
                'db_table': 'inventory_stock_levels',
            },
        ),
        
        # Create StockMovement model
        migrations.CreateModel(
            name='StockMovement',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('movement_type', models.CharField(choices=[('PURCHASE', 'Purchase/Restock'), ('USAGE', 'Usage/Consumption'), ('ADJUSTMENT', 'Manual Adjustment'), ('RETURN', 'Return to Supplier'), ('DAMAGE', 'Damaged/Waste'), ('TRANSFER', 'Transfer between locations'), ('COUNT', 'Inventory Count')], max_length=20)),
                ('quantity_change', models.DecimalField(decimal_places=2, max_digits=10)),
                ('quantity_before', models.DecimalField(decimal_places=2, max_digits=10)),
                ('quantity_after', models.DecimalField(decimal_places=2, max_digits=10)),
                ('reference_id', models.CharField(blank=True, max_length=100, null=True)),
                ('notes', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('inventory_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stock_movements', to='inventory.inventoryitem')),
            ],
            options={
                'db_table': 'inventory_stock_movements',
                'ordering': ['-created_at'],
            },
        ),
        
        # Create StockAlert model
        migrations.CreateModel(
            name='StockAlert',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('alert_type', models.CharField(choices=[('LOW_STOCK', 'Low Stock Alert'), ('CRITICAL', 'Critical Stock Level'), ('OVERSTOCK', 'Overstock Alert'), ('EXPIRY_WARNING', 'Expiry Warning'), ('UNUSUAL_USAGE', 'Unusual Usage Pattern')], max_length=20)),
                ('status', models.CharField(choices=[('ACTIVE', 'Active'), ('ACKNOWLEDGED', 'Acknowledged'), ('RESOLVED', 'Resolved'), ('DISMISSED', 'Dismissed')], default='ACTIVE', max_length=20)),
                ('current_stock', models.DecimalField(decimal_places=2, max_digits=10)),
                ('threshold_value', models.DecimalField(decimal_places=2, max_digits=10)),
                ('message', models.TextField()),
                ('acknowledged_at', models.DateTimeField(blank=True, null=True)),
                ('resolved_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('acknowledged_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='acknowledged_alerts', to=settings.AUTH_USER_MODEL)),
                ('inventory_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='alerts', to='inventory.inventoryitem')),
            ],
            options={
                'db_table': 'inventory_stock_alerts',
                'ordering': ['-created_at'],
            },
        ),
        
        # Create Forecast model
        migrations.CreateModel(
            name='Forecast',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('forecast_date', models.DateField()),
                ('forecasted_quantity', models.DecimalField(decimal_places=2, max_digits=10)),
                ('confidence_level', models.IntegerField(help_text='Confidence percentage (0-100)')),
                ('forecast_method', models.CharField(max_length=50)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('inventory_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='forecasts', to='inventory.inventoryitem')),
            ],
            options={
                'db_table': 'inventory_forecasts',
                'ordering': ['forecast_date'],
            },
        ),
        
        # Create RecipeIngredient model
        migrations.CreateModel(
            name='RecipeIngredient',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('quantity_required', models.DecimalField(decimal_places=3, max_digits=10)),
                ('unit', models.CharField(max_length=10)),
                ('cost_per_portion', models.DecimalField(decimal_places=2, max_digits=10)),
                ('is_optional', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('inventory_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='used_in_recipes', to='inventory.inventoryitem')),
                ('menu_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ingredients', to='menu.menuitem')),
            ],
            options={
                'db_table': 'inventory_recipe_ingredients',
            },
        ),
        
        # Create SupplierContact model
        migrations.CreateModel(
            name='SupplierContact',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('contact_type', models.CharField(choices=[('SALES', 'Sales'), ('SUPPORT', 'Support'), ('BILLING', 'Billing')], default='SALES', max_length=20)),
                ('contact_name', models.CharField(max_length=255)),
                ('email', models.EmailField(max_length=254)),
                ('phone', models.CharField(max_length=20)),
                ('is_primary', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('supplier', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='contacts', to='inventory.supplier')),
            ],
            options={
                'db_table': 'inventory_supplier_contacts',
            },
        ),
        
        # Create SupplierPrice model
        migrations.CreateModel(
            name='SupplierPrice',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('unit_price', models.DecimalField(decimal_places=2, max_digits=10)),
                ('minimum_order', models.DecimalField(decimal_places=2, default=1, max_digits=10)),
                ('lead_time_days', models.IntegerField(help_text='Days to delivery')),
                ('is_current', models.BooleanField(default=True)),
                ('effective_from', models.DateField()),
                ('effective_until', models.DateField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('inventory_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='supplier_prices', to='inventory.inventoryitem')),
                ('supplier', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='prices', to='inventory.supplier')),
            ],
            options={
                'db_table': 'inventory_supplier_prices',
                'ordering': ['-effective_from'],
            },
        ),
        
        # Create InventoryAudit model
        migrations.CreateModel(
            name='InventoryAudit',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('audit_date', models.DateField()),
                ('status', models.CharField(choices=[('STARTED', 'Started'), ('IN_PROGRESS', 'In Progress'), ('COMPLETED', 'Completed')], default='STARTED', max_length=20)),
                ('total_variance', models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ('notes', models.TextField(blank=True, null=True)),
                ('started_at', models.DateTimeField(auto_now_add=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('restaurant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='inventory_audits', to='accounts.restaurant')),
                ('started_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'inventory_audits',
                'ordering': ['-audit_date'],
            },
        ),
        
        # Create InventoryAuditItem model
        migrations.CreateModel(
            name='InventoryAuditItem',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('system_quantity', models.DecimalField(decimal_places=2, max_digits=10)),
                ('counted_quantity', models.DecimalField(decimal_places=2, max_digits=10)),
                ('variance', models.DecimalField(decimal_places=2, max_digits=10)),
                ('variance_reason', models.CharField(blank=True, choices=[('DAMAGE', 'Damage'), ('USAGE', 'Usage'), ('COUNT_ERROR', 'Counting Error'), ('UNKNOWN', 'Unknown')], max_length=100, null=True)),
                ('audit', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='audit_items', to='inventory.inventoryaudit')),
                ('inventory_item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='inventory.inventoryitem')),
            ],
            options={
                'db_table': 'inventory_audit_items',
            },
        ),
        
        # Add indexes
        migrations.AddIndex(
            model_name='stockmovement',
            index=models.Index(fields=['inventory_item', 'created_at'], name='inv_stock_m_item_ts_idx'),
        ),
        migrations.AddIndex(
            model_name='stockmovement',
            index=models.Index(fields=['movement_type'], name='inv_stock_m_type_idx'),
        ),
        
        # Add unique constraints
        migrations.AddConstraint(
            model_name='forecast',
            constraint=models.UniqueConstraint(fields=['inventory_item', 'forecast_date'], name='unique_forecast_per_day'),
        ),
        migrations.AddConstraint(
            model_name='recipeingredient',
            constraint=models.UniqueConstraint(fields=['menu_item', 'inventory_item'], name='unique_ingredient_per_recipe'),
        ),
    ]