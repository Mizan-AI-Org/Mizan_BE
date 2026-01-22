"""
User Management Service for Multi-Tenant Architecture

Handles:
- User invitations (bulk and individual)
- Role assignment and management
- Permission management
- RBAC operations
"""
import csv, io, sys, os
import logging
import secrets
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings

from .models import (
    Restaurant, CustomUser, StaffProfile,
    Role, Permission, RolePermission, UserInvitation,
    UserRole, AuditLog
)
from .tasks import send_whatsapp_invitation_task
import requests

logger = logging.getLogger(__name__)


# Lua Webhook Configuration
LUA_AGENT_ID = "baseAgent_agent_1762796132079_ob3ln5fkl"
LUA_USER_AUTH_WEBHOOK_ID = "df2d840c-b80e-4e6b-98d8-af4e95c0d96a"
LUA_WEBHOOK_API_KEY = getattr(settings, 'LUA_WEBHOOK_API_KEY', 'mizan-agent-webhook-secret-2026')


def sync_user_to_lua_agent(user, access_token):
    """
    Sync user context to Lua agent after login.
    This provisions the user's Lua profile with restaurant context and JWT token,
    enabling Miya to make API calls on behalf of the user.
    """
    try:
        # Use webhook.heylua.ai with webhook name (not api.heylua.ai/developer/webhooks)
        webhook_url = f"https://webhook.heylua.ai/{LUA_AGENT_ID}/user-authenticated"
        
        # Get the Lua API key for Authorization header
        lua_api_key = getattr(settings, 'LUA_API_KEY', None) or os.environ.get('LUA_API_KEY', '')
        
        payload = {
            "emailAddress": user.email,
            "mobileNumber": getattr(user, 'phone', None),
            "fullName": f"{user.first_name} {user.last_name}".strip(),
            "restaurantId": str(user.restaurant.id) if user.restaurant else None,
            "restaurantName": user.restaurant.name if user.restaurant else None,
            "role": user.role.lower() if user.role else "staff",
            "metadata": {
                "token": access_token,
                "userId": str(user.id),
            }
        }
        
        headers = {
            "Content-Type": "application/json",
            "Api-Key": lua_api_key,  # Common Lua API auth header
            "x-api-key": LUA_WEBHOOK_API_KEY  # Our webhook's internal validation
        }
        
        logger.info(f"[LuaSync] Calling webhook for user {user.email} at {webhook_url}")
        logger.info(f"[LuaSync] Using API key: {lua_api_key[:8]}... (truncated)")
        response = requests.post(webhook_url, json=payload, headers=headers, timeout=5)
        
        if response.status_code in (200, 201):
            logger.info(f"[LuaSync] Successfully synced user {user.email} to Lua agent")
            return True
        else:
            logger.warning(f"[LuaSync] Failed to sync user {user.email}: {response.status_code} - {response.text}")
            return False
            
    except requests.RequestException as e:
        logger.warning(f"[LuaSync] Network error syncing user {user.email}: {str(e)}")
        return False
    except Exception as e:
        logger.error(f"[LuaSync] Unexpected error syncing user {user.email}: {str(e)}")
        return False


class UserManagementService:
    """Service for managing users, invitations, and role assignments"""
    @staticmethod
    def bulk_invite_from_csv(csv_content, restaurant, invited_by, expires_in_days=7):
        """
        Process CSV content and create StaffInvitation/UserInvitation entries.
        Accepts columns: email, first_name/firstname, last_name/lastname, role,
        department (optional), phone (optional).

        Returns dict: { success, failed, errors, invitations }
        """
    
        reader = csv.DictReader(io.StringIO(csv_content))
        results = { 'success': 0, 'failed': 0, 'errors': [], 'invitations': [] }

        for idx, row in enumerate(reader, start=2):  # start=2 accounts for header line
            try:
                email = (row.get('email') or '').strip()    
                if not email or '@' not in email:
                    results['failed'] += 1
                    results['errors'].append(f"Row {idx}: Invalid email '{email}'")
                    continue

                role_value = (row.get('role') or '').strip()
                # skipping role validation / conversion

                # Normalize name fields
                first_name = (row.get('first_name') or row.get('firstname') or '').strip()
                last_name = (row.get('last_name') or row.get('lastname') or '').strip()

                department = (row.get('department') or '').strip()
                phone = (row.get('phone') or row.get('whatsapp') or row.get('phonenumber') or '').strip()

                # Check if pending invitation already exists
                print(f"Row {idx}: checking existing invitation for {email}", file=sys.stderr)
                try:
                    existing_invite = UserInvitation.objects.filter(
                        restaurant=restaurant,
                        email=email,
                        is_accepted=False,  # you said you added this field
                        expires_at__gt=timezone.now()
                    ).first()
                except Exception as e:
                    import traceback
                    results['failed'] += 1
                    results['errors'].append(f"Row {idx}: DB query failed for {email} - {str(e)}")
                    print(f"❌ ERROR in DB query (row {idx}): {e}", file=sys.stderr)
                    print(traceback.format_exc(), file=sys.stderr)
                    continue  # skip this row

                if existing_invite:
                    results['failed'] += 1
                    results['errors'].append(f"Row {idx}: Invitation already pending for {email}")
                    continue

                # Create invitation
                try:
                    token = secrets.token_urlsafe(32)
                    expires_at = timezone.now() + timedelta(days=expires_in_days)
                    
                    extra_data = {
                        'first_name': first_name,
                        'last_name': last_name,
                        'department': department,
                        'phone': phone
                    }

                    invitation = UserInvitation.objects.create(
                        email=email,
                        role=role_value,  # skipped role conversion
                        restaurant=restaurant,
                        invited_by=invited_by,
                        invitation_token=token,
                        expires_at=expires_at,
                        is_accepted=False,
                        first_name=first_name,
                        last_name=last_name,
                        extra_data=extra_data,
                    )
                    print(f"Row {idx}: invitation created for {email}", file=sys.stderr)

                    # Send invitation email
                    try:
                        UserManagementService._send_invitation_email(invitation)
                    except Exception as e:
                        results['errors'].append(f"Row {idx}: Email send failed for {email} - {str(e)}")
                        print(f"❌ Email send failed (row {idx}): {e}", file=sys.stderr)

                    results['success'] += 1
                    results['invitations'].append(invitation)

                    # Send WhatsApp if phone is present
                    phone_num = (row.get('phone') or row.get('phonenumber') or '').strip()
                    if phone_num:
                        invite_link = f"{settings.FRONTEND_URL}/accept-invitation?token={token}"
                        send_whatsapp_invitation_task.delay(
                            invitation_id=invitation.id,
                            phone=phone_num,
                            first_name=first_name,
                            restaurant_name=restaurant.name,
                            invite_link=invite_link,
                            support_contact=getattr(settings, 'SUPPORT_CONTACT', '')
                        )

                except Exception as e:
                    results['failed'] += 1
                    results['errors'].append(f"Row {idx}: Creation failed for {email} - {str(e)}")
                    print(f"❌ Creation failed (row {idx}): {e}", file=sys.stderr)

            except Exception as e:
                results['failed'] += 1
                results['errors'].append(f"Row {idx}: Unexpected error for {email} - {str(e)}")
                import traceback
                print(f"❌ Unexpected error (row {idx}): {e}", file=sys.stderr)
                print(traceback.format_exc(), file=sys.stderr)

        return results

    @staticmethod
    def bulk_invite_from_list(invitations, restaurant, invited_by, expires_in_days=7):
        """
        Process JSON list of invitations with the same fields as CSV.
        """
        results = { 'success': 0, 'failed': 0, 'errors': [], 'invitations': [] }
        for idx, item in enumerate(invitations, start=1):
            try:
                email = (item.get('email') or '').strip()
                role_value = (item.get('role') or '').strip()
                if not email or '@' not in email:
                    results['failed'] += 1
                    results['errors'].append(f"Item {idx}: Invalid email '{email}'")
                    continue
                if not role_value:
                    results['failed'] += 1
                    results['errors'].append(f"Item {idx}: Missing role for {email}")
                    continue

                first_name = (item.get('first_name') or item.get('firstname') or '').strip()
                last_name = (item.get('last_name') or item.get('lastname') or '').strip()
                department = (item.get('department') or '').strip()
                phone = (item.get('phone') or item.get('whatsapp') or item.get('phonenumber') or '').strip()

                existing_invite = UserInvitation.objects.filter(
                    restaurant=restaurant,
                    email=email,
                    is_accepted=False,
                    expires_at__gt=timezone.now()
                ).first()
                if existing_invite:
                    results['failed'] += 1
                    results['errors'].append(f"Item {idx}: Invitation already pending for {email}")
                    continue

                token = secrets.token_urlsafe(32)
                expires_at = timezone.now() + timedelta(days=expires_in_days)
                
                extra_data = {
                    'first_name': first_name,
                    'last_name': last_name,
                    'department': department,
                    'phone': phone
                }

                invitation = UserInvitation.objects.create(
                    email=email,
                    role=role_value,
                    restaurant=restaurant,
                    invited_by=invited_by,
                    invitation_token=token,
                    expires_at=expires_at,
                    first_name=first_name,
                    last_name=last_name,
                    extra_data=extra_data,
                )
                try:
                    UserManagementService._send_invitation_email(invitation)
                except Exception as e:
                    results['errors'].append(f"Item {idx}: Email send failed for {email} - {str(e)}")
                results['success'] += 1
                results['invitations'].append(invitation)

                # Send WhatsApp if phone is present
                if phone:
                    invite_link = f"{settings.FRONTEND_URL}/accept-invitation?token={token}"
                    send_whatsapp_invitation_task.delay(
                        invitation_id=invitation.id,
                        phone=phone,
                        first_name=first_name,
                        restaurant_name=restaurant.name,
                        invite_link=invite_link,
                        support_contact=getattr(settings, 'SUPPORT_CONTACT', '')
                    )
            except Exception as e:
                results['failed'] += 1
                results['errors'].append(f"Item {idx}: {str(e)}")
        return results
    
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
            try:
                UserManagementService.sync_hr_on_invitation(invitation)
            except Exception:
                pass
            
            return invitation
        
        except Exception as e:
            logger.error(f"Individual invitation error: {str(e)}")
            raise
    
    @staticmethod
    def accept_invitation(token, password, first_name, last_name):
        """
        Accept StaffInvitation and create user account (used by InvitationViewSet).
        Returns tuple: (user, error)
        """
        try:
            invitation = UserInvitation.objects.get(invitation_token=token, is_accepted=False)
            # Check expiration
            if invitation.expires_at < timezone.now():
                return None, "Invitation has expired"

            # Guard against existing accounts for the same email to avoid
            # database IntegrityError and aborted transaction states
            if CustomUser.objects.filter(email=invitation.email).exists():
                return (
                    None,
                    "An account with this email already exists. Please log in instead or contact your admin."
                )

            with transaction.atomic():
                user = CustomUser.objects.create_user(
                    email=invitation.email,
                    password=password,
                    first_name=first_name or '',
                    last_name=last_name or '',
                    is_verified=True,
                    is_active=True,
                    role=invitation.role,
                    restaurant=invitation.restaurant,
                )

                # Set phone if provided in extra_data
                phone = (invitation.extra_data or {}).get('phone')
                if phone:
                    user.phone = phone
                    user.save(update_fields=['phone'])

                # Create/update staff profile with department
                department = (invitation.extra_data or {}).get('department')
                if department:
                    StaffProfile.objects.update_or_create(
                        user=user,
                        defaults={'department': department}
                    )

                from django.utils import timezone as dj_tz
                invitation.is_accepted = True
                invitation.status = 'ACCEPTED'
                invitation.accepted_at = dj_tz.now()
                invitation.save(update_fields=['is_accepted', 'status', 'accepted_at'])

                # Close any other pending invitations for the same email in this restaurant
                UserInvitation.objects.filter(
                    restaurant=invitation.restaurant,
                    email=invitation.email,
                    is_accepted=False,
                ).update(status='EXPIRED', expires_at=dj_tz.now())

                # Notify Lua agent if phone is available for follow-up message
                if phone:
                    from notifications.services import notification_service
                    notification_service.send_lua_invitation_accepted(
                        invitation_token=invitation.invitation_token,
                        phone=phone,
                        first_name=user.first_name,
                        flow_data={'last_name': user.last_name, 'email': user.email}
                    )

                return user, None
        except UserInvitation.DoesNotExist:
            return None, "Invalid invitation token"
        except Exception as e:
            logger.error(f"Accept invitation error: {str(e)}")
            return None, str(e)
    
    @staticmethod
    def _send_invitation_email(invitation):
        """Send invitation email to user. Returns True on success, False on failure."""
        try:
            # Use the React route that reads token from query params
            token_value = getattr(invitation, 'invitation_token', getattr(invitation, 'token', ''))
            invite_link = f"{settings.FRONTEND_URL}/accept-invitation?token={token_value}"
            context = {
                'invitation': invitation,
                'invite_link': invite_link,
                'acceptance_link': f"{settings.FRONTEND_URL}/staff/accept-invitation/{invitation.invitation_token}",
                'expires_at': invitation.expires_at.strftime('%Y-%m-%d %H:%M:%S'),
                'restaurant_name': invitation.restaurant.name,
                'year': timezone.now().year,
            }

            # Render template and send mail within the same try block so any
            # template errors are caught and surfaced as a graceful failure
            html_message = render_to_string('emails/staff_invite.html', context)

            send_mail(
                subject=f"You're invited to join {invitation.restaurant.name}",
                message=f"You've been invited to join {invitation.restaurant.name}. Use this link: {invite_link}",
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[invitation.email],
                html_message=html_message,
                fail_silently=False,
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send invitation email: {str(e)}")
            return False

    @staticmethod
    def sync_hr_on_invitation(invitation):
        try:
            return True
        except Exception:
            return False


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
