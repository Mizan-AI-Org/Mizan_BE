"""
User Management Service for Multi-Tenant Architecture

Handles:
- User invitations (bulk and individual)
- Role assignment and management
- Permission management
- RBAC operations
"""
import csv
import io
import logging
import secrets
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings

from .models import Restaurant, CustomUser
from .models_rbac import (
    Role, Permission, RolePermission, UserInvitation, 
    UserRole, AuditLog
)

logger = logging.getLogger(__name__)


class UserManagementService:
    """Service for managing users, invitations, and role assignments"""
    
    @staticmethod
    def bulk_invite_users(restaurant, csv_file, invited_by, role=None, expires_in_days=7):
        """
        Bulk invite users from CSV file
        CSV format: email, first_name, last_name, role_name
        
        Returns: {
            'total': int,
            'created': int,
            'skipped': int,
            'errors': List[str],
            'batch_id': str,
            'invitations': List[UserInvitation]
        }
        """
        batch_id = secrets.token_hex(8)
        results = {
            'total': 0,
            'created': 0,
            'skipped': 0,
            'errors': [],
            'batch_id': batch_id,
            'invitations': []
        }
        
        try:
            # Parse CSV
            csv_data = csv.DictReader(io.StringIO(csv_file.read().decode('utf-8')))
            
            with transaction.atomic():
                for row_idx, row in enumerate(csv_data, 1):
                    results['total'] += 1
                    
                    try:
                        email = row.get('email', '').strip()
                        first_name = row.get('first_name', '').strip()
                        last_name = row.get('last_name', '').strip()
                        role_name = row.get('role', '').strip()
                        
                        # Validate email
                        if not email or '@' not in email:
                            results['errors'].append(f"Row {row_idx}: Invalid email '{email}'")
                            results['skipped'] += 1
                            continue
                        
                        # Check if user already exists
                        existing_user = CustomUser.objects.filter(email=email).exists()
                        if existing_user:
                            # Check if already invited
                            existing_invite = UserInvitation.objects.filter(
                                restaurant=restaurant,
                                email=email,
                                status='PENDING'
                            ).exists()
                            
                            if existing_invite:
                                results['errors'].append(f"Row {row_idx}: User already invited")
                                results['skipped'] += 1
                                continue
                        
                        # Get role
                        if role_name:
                            try:
                                user_role = Role.objects.get(
                                    restaurant=restaurant,
                                    name=role_name,
                                    is_active=True
                                )
                            except Role.DoesNotExist:
                                results['errors'].append(f"Row {row_idx}: Role '{role_name}' not found")
                                results['skipped'] += 1
                                continue
                        else:
                            user_role = role
                        
                        # Create invitation
                        invitation = UserInvitation.create_invitation(
                            restaurant=restaurant,
                            email=email,
                            role=user_role,
                            invited_by=invited_by,
                            expires_in_days=expires_in_days,
                            bulk_batch_id=batch_id
                        )
                        
                        if first_name:
                            invitation.first_name = first_name
                        if last_name:
                            invitation.last_name = last_name
                        
                        invitation.is_bulk_invite = True
                        invitation.save()
                        
                        results['created'] += 1
                        results['invitations'].append(invitation)
                        
                        # Send invitation email
                        UserManagementService._send_invitation_email(invitation)
                        
                    except Exception as e:
                        results['errors'].append(f"Row {row_idx}: {str(e)}")
                        results['skipped'] += 1
        
        except Exception as e:
            results['errors'].append(f"CSV parsing failed: {str(e)}")
            logger.error(f"Bulk invite error: {str(e)}")
        
        return results
    
    @staticmethod
    def send_individual_invitation(restaurant, email, role, invited_by, 
                                   first_name=None, last_name=None, expires_in_days=7):
        """Send individual invitation to user"""
        
        try:
            # Check if already invited
            existing_invite = UserInvitation.objects.filter(
                restaurant=restaurant,
                email=email,
                status__in=['PENDING', 'ACCEPTED']
            ).first()
            
            if existing_invite:
                if existing_invite.status == 'ACCEPTED':
                    raise ValueError("User already has an active account")
                else:
                    return existing_invite  # Return existing pending invitation
            
            # Create invitation
            invitation = UserInvitation.create_invitation(
                restaurant=restaurant,
                email=email,
                role=role,
                invited_by=invited_by,
                expires_in_days=expires_in_days
            )
            
            if first_name:
                invitation.first_name = first_name
            if last_name:
                invitation.last_name = last_name
            
            invitation.save()
            
            # Send email
            UserManagementService._send_invitation_email(invitation)
            
            return invitation
        
        except Exception as e:
            logger.error(f"Individual invitation error: {str(e)}")
            raise
    
    @staticmethod
    def accept_invitation(invitation_token, password, username=None):
        """
        Accept invitation and create user account
        
        Returns: (success: bool, user: CustomUser | None, error: str | None)
        """
        try:
            invitation = UserInvitation.objects.get(
                invitation_token=invitation_token,
                status='PENDING'
            )
            
            # Check expiration
            if invitation.is_expired():
                invitation.status = 'EXPIRED'
                invitation.save()
                return False, None, "Invitation has expired"
            
            with transaction.atomic():
                # Create user
                email = invitation.email
                user = CustomUser.objects.create_user(
                    email=email,
                    username=username or email,
                    password=password,
                    first_name=invitation.first_name or '',
                    last_name=invitation.last_name or '',
                    is_verified=True,
                    is_active=True
                )
                
                # Assign role
                if invitation.role:
                    UserRole.objects.create(
                        user=user,
                        restaurant=invitation.restaurant,
                        role=invitation.role,
                        is_primary=True,
                        assigned_by=invitation.invited_by
                    )
                
                # Mark invitation as accepted
                invitation.status = 'ACCEPTED'
                invitation.accepted_at = timezone.now()
                invitation.accepted_by = user
                invitation.save()
                
                # Log audit
                AuditLog.objects.create(
                    restaurant=invitation.restaurant,
                    user=user,
                    action_type='CREATE',
                    entity_type='CustomUser',
                    entity_id=str(user.id),
                    description=f"User registered via invitation",
                )
                
                return True, user, None
        
        except UserInvitation.DoesNotExist:
            return False, None, "Invalid or expired invitation token"
        except Exception as e:
            logger.error(f"Accept invitation error: {str(e)}")
            return False, None, str(e)
    
    @staticmethod
    def _send_invitation_email(invitation):
        """Send invitation email to user"""
        try:
            context = {
                'invitation': invitation,
                'acceptance_link': f"{settings.FRONTEND_URL}/staff/accept-invitation/{invitation.invitation_token}",
                'expires_at': invitation.expires_at.strftime('%Y-%m-%d %H:%M:%S'),
                'restaurant_name': invitation.restaurant.name,
            }
            
            html_message = render_to_string('emails/invitation.html', context)
            
            send_mail(
                subject=f"You're invited to join {invitation.restaurant.name}",
                message=f"You've been invited to join {invitation.restaurant.name}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[invitation.email],
                html_message=html_message,
                fail_silently=False,
            )
        except Exception as e:
            logger.error(f"Failed to send invitation email: {str(e)}")


class RoleManagementService:
    """Service for managing roles and permissions"""
    
    @staticmethod
    def create_role(restaurant, name, description=None, is_active=True):
        """Create new role"""
        role, created = Role.objects.get_or_create(
            restaurant=restaurant,
            name=name,
            defaults={
                'description': description,
                'is_active': is_active
            }
        )
        return role, created
    
    @staticmethod
    def assign_permission_to_role(role, permission):
        """Assign permission to role"""
        perm_assign, created = RolePermission.objects.get_or_create(
            role=role,
            permission=permission
        )
        return perm_assign, created
    
    @staticmethod
    def remove_permission_from_role(role, permission):
        """Remove permission from role"""
        RolePermission.objects.filter(
            role=role,
            permission=permission
        ).delete()
    
    @staticmethod
    def create_default_roles(restaurant):
        """Create default roles for new restaurant"""
        default_roles = [
            ('OWNER', 'Restaurant Owner - Full access'),
            ('MANAGER', 'Manager - Can manage staff and orders'),
            ('CHEF', 'Chef - Can manage kitchen operations'),
            ('WAITER', 'Waiter - Can take orders'),
            ('CASHIER', 'Cashier - Can process payments'),
            ('DELIVERY', 'Delivery Driver - Can manage deliveries'),
        ]
        
        roles = []
        for role_name, description in default_roles:
            role, _ = RoleManagementService.create_role(
                restaurant=restaurant,
                name=role_name,
                description=description,
                is_active=True
            )
            roles.append(role)
        
        return roles
    
    @staticmethod
    def create_default_permissions(restaurant):
        """Create default permissions"""
        default_permissions = [
            # User Management
            ('user.manage_users', 'Manage Users', 'USER_MANAGEMENT'),
            ('user.invite_staff', 'Invite Staff', 'USER_MANAGEMENT'),
            ('user.manage_roles', 'Manage Roles', 'USER_MANAGEMENT'),
            
            # POS
            ('pos.create_order', 'Create Orders', 'POS'),
            ('pos.edit_order', 'Edit Orders', 'POS'),
            ('pos.cancel_order', 'Cancel Orders', 'POS'),
            ('pos.process_payment', 'Process Payments', 'POS'),
            ('pos.refund_order', 'Refund Orders', 'POS'),
            ('pos.discount_codes', 'Manage Discount Codes', 'POS'),
            
            # Inventory
            ('inventory.view', 'View Inventory', 'INVENTORY'),
            ('inventory.manage', 'Manage Inventory', 'INVENTORY'),
            ('inventory.restock', 'Restock Items', 'INVENTORY'),
            ('inventory.manage_suppliers', 'Manage Suppliers', 'INVENTORY'),
            
            # Scheduling
            ('schedule.view', 'View Schedules', 'SCHEDULING'),
            ('schedule.create', 'Create Schedules', 'SCHEDULING'),
            ('schedule.edit', 'Edit Schedules', 'SCHEDULING'),
            ('schedule.assign_tasks', 'Assign Tasks', 'SCHEDULING'),
            
            # Reporting
            ('report.view', 'View Reports', 'REPORTING'),
            ('report.export', 'Export Reports', 'REPORTING'),
            
            # Admin
            ('admin.access', 'Access Admin Panel', 'ADMIN'),
            ('admin.settings', 'Change Settings', 'ADMIN'),
        ]
        
        permissions = []
        for code, name, category in default_permissions:
            perm, _ = Permission.objects.get_or_create(
                restaurant=restaurant,
                code=code,
                defaults={
                    'name': name,
                    'category': category,
                    'is_active': True
                }
            )
            permissions.append(perm)
        
        return permissions
    
    @staticmethod
    def assign_role_to_user(user, restaurant, role, assigned_by, is_primary=False):
        """Assign role to user in restaurant"""
        user_role, created = UserRole.objects.get_or_create(
            user=user,
            restaurant=restaurant,
            role=role,
            defaults={
                'assigned_by': assigned_by,
                'is_primary': is_primary
            }
        )
        return user_role, created
    
    @staticmethod
    def remove_role_from_user(user, restaurant, role):
        """Remove role from user"""
        UserRole.objects.filter(
            user=user,
            restaurant=restaurant,
            role=role
        ).delete()
    
    @staticmethod
    def check_user_permission(user, restaurant, permission_code):
        """Check if user has specific permission in restaurant"""
        if user.is_superuser:
            return True
        
        try:
            user_roles = UserRole.objects.filter(
                user=user,
                restaurant=restaurant
            ).select_related('role')
            
            for user_role in user_roles:
                has_perm = RolePermission.objects.filter(
                    role=user_role.role,
                    permission__code=permission_code,
                    permission__is_active=True
                ).exists()
                
                if has_perm:
                    return True
            
            return False
        except:
            return False