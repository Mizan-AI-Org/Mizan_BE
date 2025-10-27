# Generated migration for RBAC models

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_add_geolocation_pos_ai'),
    ]

    operations = [
        # Create Role model
        migrations.CreateModel(
            name='Role',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('name', models.CharField(choices=[('OWNER', 'Restaurant Owner'), ('MANAGER', 'Manager'), ('SUPERVISOR', 'Supervisor'), ('CHEF', 'Chef'), ('WAITER', 'Waiter/Server'), ('CASHIER', 'Cashier'), ('KITCHEN_STAFF', 'Kitchen Staff'), ('CLEANER', 'Cleaner/Housekeeping'), ('DELIVERY', 'Delivery Driver'), ('CUSTOM', 'Custom Role')], max_length=100)),
                ('description', models.TextField(blank=True, null=True)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('restaurant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='roles', to='accounts.restaurant')),
            ],
            options={
                'db_table': 'rbac_roles',
                'ordering': ['name'],
            },
        ),
        
        # Create Permission model
        migrations.CreateModel(
            name='Permission',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('code', models.CharField(max_length=100, unique=True)),
                ('name', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True, null=True)),
                ('category', models.CharField(choices=[('USER_MANAGEMENT', 'User Management'), ('POS', 'Point of Sale'), ('INVENTORY', 'Inventory Management'), ('SCHEDULING', 'Staff Scheduling'), ('REPORTING', 'Reports & Analytics'), ('KITCHEN', 'Kitchen Operations'), ('ADMIN', 'Admin Settings')], max_length=50)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('restaurant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='permissions', to='accounts.restaurant')),
            ],
            options={
                'db_table': 'rbac_permissions',
                'ordering': ['category', 'code'],
            },
        ),
        
        # Create RolePermission model
        migrations.CreateModel(
            name='RolePermission',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('assigned_at', models.DateTimeField(auto_now_add=True)),
                ('permission', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='roles', to='accounts.permission')),
                ('role', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='permissions', to='accounts.role')),
            ],
            options={
                'db_table': 'rbac_role_permissions',
            },
        ),
        
        # Create UserRole model
        migrations.CreateModel(
            name='UserRole',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('is_primary', models.BooleanField(default=False)),
                ('assigned_at', models.DateTimeField(auto_now_add=True)),
                ('assigned_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='role_assignments', to=settings.AUTH_USER_MODEL)),
                ('restaurant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_roles', to='accounts.restaurant')),
                ('role', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='users', to='accounts.role')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='restaurant_roles', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'rbac_user_roles',
                'ordering': ['-is_primary', '-assigned_at'],
            },
        ),
        
        # Create UserInvitation model
        migrations.CreateModel(
            name='UserInvitation',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('email', models.EmailField(max_length=254)),
                ('first_name', models.CharField(blank=True, max_length=100, null=True)),
                ('last_name', models.CharField(blank=True, max_length=100, null=True)),
                ('status', models.CharField(choices=[('PENDING', 'Pending'), ('ACCEPTED', 'Accepted'), ('REJECTED', 'Rejected'), ('EXPIRED', 'Expired')], default='PENDING', max_length=20)),
                ('invitation_token', models.CharField(max_length=255, unique=True)),
                ('sent_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('accepted_at', models.DateTimeField(blank=True, null=True)),
                ('is_bulk_invite', models.BooleanField(default=False)),
                ('bulk_batch_id', models.CharField(blank=True, max_length=50, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('accepted_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invitations_accepted', to=settings.AUTH_USER_MODEL)),
                ('invited_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='invitations_sent', to=settings.AUTH_USER_MODEL)),
                ('restaurant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='user_invitations', to='accounts.restaurant')),
                ('role', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.SET_NULL, to='accounts.role')),
            ],
            options={
                'db_table': 'rbac_user_invitations',
                'ordering': ['-sent_at'],
            },
        ),
        
        # Create AuditLog model
        migrations.CreateModel(
            name='AuditLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('action_type', models.CharField(choices=[('CREATE', 'Created'), ('UPDATE', 'Updated'), ('DELETE', 'Deleted'), ('LOGIN', 'Login'), ('LOGOUT', 'Logout'), ('PERMISSION_CHANGE', 'Permission Changed'), ('ORDER_ACTION', 'Order Action'), ('INVENTORY_ACTION', 'Inventory Action'), ('OTHER', 'Other')], max_length=50)),
                ('entity_type', models.CharField(max_length=100)),
                ('entity_id', models.CharField(blank=True, max_length=100, null=True)),
                ('description', models.TextField()),
                ('old_values', models.JSONField(blank=True, default=dict)),
                ('new_values', models.JSONField(blank=True, default=dict)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.TextField(blank=True, null=True)),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('restaurant', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='audit_logs', to='accounts.restaurant')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='audit_logs', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'db_table': 'audit_logs',
                'ordering': ['-timestamp'],
            },
        ),
        
        # Add indexes
        migrations.AddIndex(
            model_name='userinvitation',
            index=models.Index(fields=['restaurant', 'status'], name='rbac_user_i_rest_st_idx'),
        ),
        migrations.AddIndex(
            model_name='userinvitation',
            index=models.Index(fields=['invitation_token'], name='rbac_user_i_token_idx'),
        ),
        migrations.AddIndex(
            model_name='userinvitation',
            index=models.Index(fields=['email', 'restaurant'], name='rbac_user_i_email_idx'),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['restaurant', 'timestamp'], name='audit_rest_ts_idx'),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['user', 'timestamp'], name='audit_user_ts_idx'),
        ),
        migrations.AddIndex(
            model_name='auditlog',
            index=models.Index(fields=['action_type'], name='audit_action_idx'),
        ),
        
        # Add unique constraints
        migrations.AddConstraint(
            model_name='role',
            constraint=models.UniqueConstraint(fields=['restaurant', 'name'], name='unique_role_per_restaurant'),
        ),
        migrations.AddConstraint(
            model_name='rolepermission',
            constraint=models.UniqueConstraint(fields=['role', 'permission'], name='unique_role_permission'),
        ),
        migrations.AddConstraint(
            model_name='userrole',
            constraint=models.UniqueConstraint(fields=['user', 'restaurant', 'role'], name='unique_user_role_restaurant'),
        ),
    ]