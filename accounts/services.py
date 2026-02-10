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
import random
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
    UserRole, AuditLog, StaffActivationRecord,
)
from .tasks import send_whatsapp_invitation_task
import requests

logger = logging.getLogger(__name__)
from core.i18n import get_effective_language


# Lua Webhook Configuration
LUA_AGENT_ID = "baseAgent_agent_1762796132079_ob3ln5fkl"
LUA_USER_AUTH_WEBHOOK_ID = "df2d840c-b80e-4e6b-98d8-af4e95c0d96a"
LUA_WEBHOOK_API_KEY = getattr(settings, 'LUA_WEBHOOK_API_KEY', None) or 'mizan-agent-webhook-secret-2026'


def sync_user_to_lua_agent(user, access_token):
    """
    Sync user context to Lua agent after login.
    This provisions the user's Lua profile with restaurant context and JWT token,
    enabling Miya to make API calls on behalf of the user.
    Returns True on success, False on failure. Never raises; login proceeds regardless.
    """
    import time
    webhook_url = getattr(settings, 'LUA_USER_AUTHENTICATION_WEBHOOK', None)
    if not webhook_url:
        webhook_url = f"https://webhook.heylua.ai/{LUA_AGENT_ID}/{LUA_USER_AUTH_WEBHOOK_ID}"

    lua_api_key = getattr(settings, 'LUA_API_KEY', None) or os.environ.get('LUA_API_KEY', '').strip()
    if not lua_api_key:
        logger.warning(
            "[LuaSync] Skipping sync for %s: LUA_API_KEY is not configured. "
            "Set LUA_API_KEY in .env to enable Miya context sync.",
            user.email
        )
        return False

    try:
        mobile_number = getattr(user, 'phone', None)
        if mobile_number:
            mobile_number = ''.join(filter(str.isdigit, mobile_number))

        session_id = f"tenant-{str(user.restaurant.id) if user.restaurant else ''}-user-{str(user.id)}"
        effective_lang = get_effective_language(user=user, restaurant=getattr(user, 'restaurant', None))
        payload = {
            "emailAddress": user.email,
            "mobileNumber": mobile_number,
            "fullName": f"{user.first_name} {user.last_name}".strip(),
            "restaurantId": str(user.restaurant.id) if user.restaurant else None,
            "restaurantName": user.restaurant.name if user.restaurant else None,
            "role": user.role.lower() if user.role else "staff",
            "language": effective_lang,
            "rtl": True if effective_lang == "ar" else False,
            "metadata": {
                "token": access_token,
                "userId": str(user.id),
                "sessionId": session_id,
                "language": effective_lang,
            }
        }

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {lua_api_key}",
            "Api-Key": lua_api_key,
            "x-api-key": LUA_WEBHOOK_API_KEY
        }

        max_retries = 2
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                response = requests.post(webhook_url, json=payload, headers=headers, timeout=10)
                if response.status_code in (200, 201):
                    logger.info("[LuaSync] Successfully synced user %s to Lua agent", user.email)
                    return True
                last_error = f"HTTP {response.status_code}: {response.text[:200]}"
                logger.warning(
                    "[LuaSync] Attempt %d/%d failed for %s: %s",
                    attempt + 1, max_retries + 1, user.email, last_error
                )
                if attempt < max_retries:
                    time.sleep(1)
            except requests.RequestException as e:
                last_error = str(e)
                logger.warning(
                    "[LuaSync] Attempt %d/%d network error for %s: %s",
                    attempt + 1, max_retries + 1, user.email, last_error
                )
                if attempt < max_retries:
                    time.sleep(1)

        logger.error(
            "[LuaSync] All %d attempts failed for %s. Last error: %s",
            max_retries + 1, user.email, last_error
        )
        return False

    except Exception as e:
        logger.error(
            "[LuaSync] Unexpected error syncing user %s: %s",
            user.email, str(e),
            exc_info=True
        )
        return False


def _normalize_phone_digits(phone):
    """Return digits only from phone string (e.g. for lookup)."""
    if not phone:
        return ""
    return "".join(filter(str.isdigit, str(phone)))


def _normalize_staff_upload_row(row):
    """
    Map common CSV/Excel column names to canonical keys so uploads like
    'First Name', 'Last Name', 'Role', 'WhatsApp Number' work.
    Returns dict with keys: phone, first_name, last_name, role (values stripped).
    """
    if not isinstance(row, dict):
        return {}
    # Normalize keys: lowercase, strip, space -> underscore, for matching
    def norm(s):
        return (str(s).strip().lower().replace(" ", "_").replace("-", "_") if s else "")
    key_to_val = {norm(k): str(v).strip() for k, v in row.items() if k is not None and v is not None and str(v).strip()}
    def get_first(*candidates):
        for c in candidates:
            nc = norm(c)
            if nc and nc in key_to_val:
                return key_to_val[nc]
            for k, v in key_to_val.items():
                if nc in k or (len(nc) > 2 and k.endswith(nc)) or (len(k) > 2 and nc.endswith(k)):
                    return v
        return ""
    phone = get_first("phone", "whatsapp", "phonenumber", "whatsapp number", "whatsapp_number", "tel", "mobile")
    first_name = get_first("first name", "first_name", "firstname", "prenom")
    last_name = get_first("last name", "last_name", "lastname", "nom")
    role = get_first("role", "position", "title", "job")
    return {
        "phone": str(phone).strip() if phone else "",
        "first_name": str(first_name).strip() if first_name else "",
        "last_name": str(last_name).strip() if last_name else "",
        "role": str(role).strip() if role else "",
    }


def _phone_suffix(digits, length=9):
    """Return last N digits for matching (handles local vs international format)."""
    if not digits or len(digits) < 6:
        return ""
    return digits[-length:] if len(digits) >= length else digits


def _phone_to_e164_morocco(digits):
    """
    Normalize to E.164 for Morocco (212 + 9 digits). If digits are 9 and start with 6 or 7, prepend 212.
    Handles CSV like "697998519" or "0784476751" (strip leading 0) -> "212697998519" / "212784476751".
    """
    if not digits or len(digits) < 6:
        return digits
    d = _normalize_phone_digits(digits)
    if len(d) == 9 and d[0] in ('6', '7'):
        return '212' + d
    if len(d) == 10 and d.startswith('0') and d[1] in ('6', '7'):
        return '212' + d[1:]
    return d


def _find_staff_activation_record_by_phone(phone_digits):
    """
    Find a NOT_ACTIVATED StaffActivationRecord by phone using robust suffix matching.
    Prefers exact match; falls back to last-9-digit suffix; handles Morocco 212+7 (CSV) vs 212+9 (WhatsApp).
    Handles: CSV "0784476751" vs WhatsApp "212784476751"; CSV "2126979985" (10d) vs WhatsApp "212697998519" (12d).
    Returns record or None.
    """
    if not phone_digits or len(phone_digits) < 6:
        return None
    normalized = _normalize_phone_digits(phone_digits)
    suffix = _phone_suffix(normalized, 9)
    # Fast path: exact digit match (stored normalized)
    record = StaffActivationRecord.objects.filter(
        status=StaffActivationRecord.STATUS_NOT_ACTIVATED
    ).filter(phone=normalized).first()
    if record:
        return record
    # Suffix match: stored phone ends with suffix (last 9 digits)
    record = StaffActivationRecord.objects.filter(
        status=StaffActivationRecord.STATUS_NOT_ACTIVATED
    ).filter(phone__endswith=suffix).first()
    if record:
        return record
    # Fallback: compare last 9 digits in Python (handles stored with leading 0 etc.)
    for r in StaffActivationRecord.objects.filter(status=StaffActivationRecord.STATUS_NOT_ACTIVATED).only('id', 'phone'):
        stored_digits = _normalize_phone_digits(r.phone)
        if _phone_suffix(stored_digits, 9) == suffix:
            return r
    # Morocco: stored 212+7 digits (10 total) vs incoming 212+9 digits (12 total) — CSV missing 2 digits
    if len(normalized) >= 12 and normalized.startswith('212'):
        national_in = normalized[3:]  # 9 digits
        for r in StaffActivationRecord.objects.filter(status=StaffActivationRecord.STATUS_NOT_ACTIVATED).only('id', 'phone'):
            stored_digits = _normalize_phone_digits(r.phone)
            if len(stored_digits) == 10 and stored_digits.startswith('212'):
                national_stored = stored_digits[3:]  # 7 digits
                if len(national_in) >= 7 and national_in[:7] == national_stored:
                    return r
    return None


def try_activate_staff_on_inbound_message(phone_digits):
    """
    ONE-TAP activation: on first inbound WhatsApp message, match by phone and activate.
    Phone number is the ONLY identity; no tokens. Creates CustomUser, links session, hands off to Lua.
    Returns CustomUser if activated, else None.
    """
    record = _find_staff_activation_record_by_phone(phone_digits)
    if not record:
        return None
    full_phone = _normalize_phone_digits(record.phone) or phone_digits
    with transaction.atomic():
        record = StaffActivationRecord.objects.select_for_update().filter(
            id=record.id, status=StaffActivationRecord.STATUS_NOT_ACTIVATED
        ).first()
        if not record:
            return None  # Already activated by another process
        email = f"wa_{full_phone}@mizan.activation"
        if CustomUser.objects.filter(email=email).exists():
            # Already activated (e.g. race or duplicate record)
            existing = CustomUser.objects.get(email=email)
            record.status = StaffActivationRecord.STATUS_ACTIVATED
            record.user = existing
            record.activated_at = timezone.now()
            record.save(update_fields=["status", "user", "activated_at", "updated_at"])
            return existing
        pin_code = str(random.randint(1000, 9999))
        user = CustomUser.objects.create_user(
            email=email,
            pin_code=pin_code,
            first_name=record.first_name or "Staff",
            last_name=record.last_name or "",
            role=record.role,
            restaurant=record.restaurant,
            phone=full_phone,
            is_verified=True,
            is_active=True,
        )
        record.status = StaffActivationRecord.STATUS_ACTIVATED
        record.user = user
        record.activated_at = timezone.now()
        record.save(update_fields=["status", "user", "activated_at", "updated_at"])
        # Ensure StaffProfile exists (used elsewhere)
        if not getattr(user, "profile", None):
            StaffProfile.objects.get_or_create(user=user, defaults={})
        # Log activation (timestamp, phone, batch_id)
        try:
            AuditLog.create_log(
                restaurant=record.restaurant,
                user=None,
                action_type='OTHER',
                entity_type='StaffActivationRecord',
                entity_id=str(record.id),
                description=f"Staff activated via ONE-TAP WhatsApp: phone={full_phone}, batch_id={getattr(record, 'batch_id', '') or ''}",
                new_values={
                    'phone': full_phone,
                    'batch_id': getattr(record, 'batch_id', '') or '',
                    'user_id': str(user.id),
                    'activated_at': record.activated_at.isoformat() if record.activated_at else None,
                },
            )
        except Exception:
            pass
        # Hand off to Lua for welcome message (no outbound from Django before this)
        from notifications.services import notification_service
        notification_service.send_lua_staff_activated(
            phone=full_phone,
            first_name=user.first_name or record.first_name or "Staff",
            restaurant_name=record.restaurant.name,
            user_id=str(user.id),
            pin_code=pin_code,
            batch_id=getattr(record, 'batch_id', '') or '',
        )
        logger.info(f"[ONE-TAP] Activated staff {user.id} for phone {full_phone} batch={getattr(record, 'batch_id', '')} ({record.restaurant.name})")
        return user


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
                    logger.error(f"CSV bulk invite row {idx} DB error: {e}", exc_info=True)
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
                    # logger.info(f"Row {idx}: invitation created for {email}")


                    # Send invitation email
                    try:
                        UserManagementService._send_invitation_email(invitation)
                    except Exception as e:
                        results['errors'].append(f"Row {idx}: Email send failed for {email} - {str(e)}")
                        logger.warning(f"CSV bulk invite row {idx} email failed: {e}")

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
                    logger.warning(f"CSV bulk invite row {idx} creation failed: {e}")

            except Exception as e:
                results['failed'] += 1
                results['errors'].append(f"Row {idx}: Unexpected error for {email} - {str(e)}")
                logger.error(f"CSV bulk invite row {idx} unexpected error: {e}", exc_info=True)

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
    def get_activation_invite_link():
        """
        ONE-TAP invite link: same link for all staff. Opens WhatsApp with prefilled message.
        Uses WHATSAPP_ACTIVATION_WA_PHONE only (E.164 digits, e.g. 212784476751).
        Do not use WHATSAPP_PHONE_NUMBER_ID here — that is Meta's internal ID, not the WhatsApp number.
        """
        base = "https://wa.me/"
        phone = getattr(settings, 'WHATSAPP_ACTIVATION_WA_PHONE', None) or ''
        phone = "".join(filter(str.isdigit, str(phone))) if phone else ""
        text = "Hi Mizan AI, I am ready to activate my account!"
        import urllib.parse
        query = urllib.parse.urlencode({"text": text})
        return f"{base}{phone}?{query}" if phone else ""

    @staticmethod
    def bulk_create_staff_activation_records(restaurant, invited_by=None, csv_content=None, staff_list=None):
        """
        ONE-TAP: Create staff profiles (pre-activation) from CSV or JSON. No outbound message.
        Validates: required phone, valid format (digits, min length), no duplicate phones in batch.
        Columns/keys: phone (required), first_name, last_name, role. Accepts 'First Name', 'Last Name', 'WhatsApp Number', 'Role'.
        Manager copies and shares the returned invite_link; when staff click it and send the prefilled message to Miya,
        the backend activates that staff's account and Miya replies via WhatsApp.
        Returns: { created, failed, errors, records, invite_link, batch_id }.
        """
        from django.db import IntegrityError
        invite_link = UserManagementService.get_activation_invite_link()
        batch_id = secrets.token_hex(8)
        results = {
            "created": 0, "failed": 0, "errors": [], "records": [],
            "invite_link": invite_link, "batch_id": batch_id,
        }
        if csv_content:
            rows = list(csv.DictReader(io.StringIO(csv_content)))
        elif staff_list and isinstance(staff_list, list):
            rows = staff_list
        else:
            results["errors"].append("Provide csv_content or staff_list")
            return results
        seen_phones = set()
        for idx, row in enumerate(rows, start=2 if csv_content else 1):
            try:
                n = _normalize_staff_upload_row(row)
                raw_phone = n.get("phone") or ""
                phone = _normalize_phone_digits(raw_phone)
                # Normalize to E.164 for Morocco so WhatsApp incoming (212+9) matches
                phone = _phone_to_e164_morocco(phone) or phone
                if not phone:
                    results["failed"] += 1
                    results["errors"].append(f"Row {idx}: Missing phone number (use column 'Phone', 'WhatsApp', or 'WhatsApp Number')")
                    continue
                if len(phone) < 6:
                    results["failed"] += 1
                    results["errors"].append(f"Row {idx}: Phone must be at least 6 digits (international format, e.g. 212697998519)")
                    continue
                if phone in seen_phones:
                    results["failed"] += 1
                    results["errors"].append(f"Row {idx}: Duplicate phone number in this file")
                    continue
                seen_phones.add(phone)
                first_name = (n.get("first_name") or "").strip()
                last_name = (n.get("last_name") or "").strip()
                role_value = (n.get("role") or "WAITER").strip().upper().replace(" ", "_").replace("É", "E").replace("È", "E")[:50]
                # Map common French/other role labels to STAFF_ROLES_CHOICES
                role_aliases = {
                    "SERVEUR": "WAITER", "SERVER": "WAITER", "CHEF_DE_SALLE": "MANAGER",
                    "ADJOINT_DE_DIRECTION": "MANAGER", "MANAGER": "MANAGER", "CHEF": "CHEF",
                    "PLONGEUR": "CLEANER", "HYGIENE": "CLEANER", "HYGIÈNE": "CLEANER",
                    "COMMIS_PARTIE_FROID": "KITCHEN_HELP", "ENTREMETIER": "KITCHEN_HELP", "ENTREMÉTIER": "KITCHEN_HELP",
                    "CHEF_PARTIE_CHAUD": "CHEF", "RECEPTIONNISTE": "RECEPTIONIST",
                    "BARTENDER": "BARTENDER", "CASHIER": "CASHIER",
                }
                role_value = role_aliases.get(role_value, role_value)
                if role_value not in dict(CustomUser.ROLE_CHOICES):
                    role_value = "WAITER"
                try:
                    record = StaffActivationRecord.objects.create(
                        restaurant=restaurant,
                        phone=phone,
                        first_name=first_name,
                        last_name=last_name,
                        role=role_value,
                        status=StaffActivationRecord.STATUS_NOT_ACTIVATED,
                        batch_id=batch_id,
                        invited_by=invited_by,
                    )
                    results["created"] += 1
                    results["records"].append({
                        "id": str(record.id),
                        "phone": phone,
                        "first_name": first_name,
                        "last_name": last_name,
                        "batch_id": batch_id,
                    })
                except IntegrityError:
                    results["failed"] += 1
                    results["errors"].append(f"Row {idx}: This phone is already pending activation for this restaurant")
            except Exception as e:
                results["failed"] += 1
                results["errors"].append(f"Row {idx}: {str(e)}")
        return results

    @staticmethod
    def create_single_staff_activation_record(restaurant, phone_raw, first_name, last_name, role_raw, invited_by=None):
        """
        ONE-TAP: Create one StaffActivationRecord for individual WhatsApp invite.
        Same flow as bulk: manager gets invite_link and shares it; when staff click and send message, account activates.
        Returns (record, None) on success, (None, error_message) on validation/duplicate error.
        """
        from django.db import IntegrityError
        phone = _normalize_phone_digits(phone_raw or "")
        phone = _phone_to_e164_morocco(phone) or phone
        if not phone:
            return None, "Phone number is required and must be at least 6 digits (international format)."
        if len(phone) < 6:
            return None, "Phone must be at least 6 digits (international format, e.g. 212697998519)."
        role_value = (role_raw or "WAITER").strip().upper().replace(" ", "_").replace("É", "E").replace("È", "E")[:50]
        role_aliases = {
            "SERVEUR": "WAITER", "SERVER": "WAITER", "CHEF_DE_SALLE": "MANAGER",
            "ADJOINT_DE_DIRECTION": "MANAGER", "MANAGER": "MANAGER", "CHEF": "CHEF",
            "PLONGEUR": "CLEANER", "HYGIENE": "CLEANER", "HYGIÈNE": "CLEANER",
            "COMMIS_PARTIE_FROID": "KITCHEN_HELP", "ENTREMETIER": "KITCHEN_HELP", "ENTREMÉTIER": "KITCHEN_HELP",
            "CHEF_PARTIE_CHAUD": "CHEF", "RECEPTIONNISTE": "RECEPTIONIST",
            "BARTENDER": "BARTENDER", "CASHIER": "CASHIER",
        }
        role_value = role_aliases.get(role_value, role_value)
        if role_value not in dict(CustomUser.ROLE_CHOICES):
            role_value = "WAITER"
        first_name = (first_name or "").strip()
        last_name = (last_name or "").strip()
        batch_id = secrets.token_hex(8)
        try:
            record = StaffActivationRecord.objects.create(
                restaurant=restaurant,
                phone=phone,
                first_name=first_name,
                last_name=last_name,
                role=role_value,
                status=StaffActivationRecord.STATUS_NOT_ACTIVATED,
                batch_id=batch_id,
                invited_by=invited_by,
            )
            return record, None
        except IntegrityError:
            return None, "This phone number already has a pending activation for this restaurant."
    
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
    def accept_invitation(token, password=None, first_name=None, last_name=None):
        """
        Accept StaffInvitation and create user account (used by InvitationViewSet).
        Returns tuple: (user, error)
        """
        try:
            with transaction.atomic():
                invitation = UserInvitation.objects.select_for_update().filter(
                    invitation_token=token, is_accepted=False
                ).first()
                if not invitation:
                    # Token may have been used already – return a specific code so frontend can redirect to login
                    already = UserInvitation.objects.filter(
                        invitation_token=token, is_accepted=True
                    ).first()
                    if already:
                        return None, "already_accepted"
                    return None, "Invalid invitation token"
                if invitation.expires_at < timezone.now():
                    return None, "Invitation has expired"

                # Fallback for names if not provided
                effective_first_name = first_name or invitation.first_name or ''
                effective_last_name = last_name or invitation.last_name or ''

                # Detect missing email and generate placeholder from phone if needed
                email = invitation.email
                phone = (invitation.extra_data or {}).get('phone') or (invitation.extra_data or {}).get('phone_number')

                if not email:
                    if phone:
                        clean_phone = ''.join(filter(str.isdigit, phone))
                        email = f"{clean_phone}@mizan.ai"
                    else:
                        return None, "Invitation has no email and no phone number"

                # Guard against existing accounts for the same email
                if CustomUser.objects.filter(email=email).exists():
                    return (
                        None,
                        "An account with this email address already exists. Please log in instead or contact your admin."
                    )

                # Auto-generate password if not provided
                if not password:
                    import secrets
                    password = secrets.token_urlsafe(12)

                # Prepare arguments for user creation
                user_kwargs = {
                    'email': email,
                    'first_name': effective_first_name,
                    'last_name': effective_last_name,
                    'is_verified': True,
                    'is_active': True,
                    'role': invitation.role,
                    'restaurant': invitation.restaurant,
                }
                
                # Check if this is a staff role and if password is a 4-digit PIN
                is_staff = invitation.role not in ['SUPER_ADMIN', 'ADMIN', 'OWNER', 'MANAGER']
                if is_staff and password and len(password) == 4 and password.isdigit():
                    user_kwargs['pin_code'] = password
                else:
                    user_kwargs['password'] = password

                user = CustomUser.objects.create_user(**user_kwargs)

                # Set phone if provided
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
                # Ensure invitation record has the final email used
                if not invitation.email:
                    invitation.email = email
                invitation.save(update_fields=['is_accepted', 'status', 'accepted_at', 'email'])

                # Close any other pending invitations for the same identifier
                UserInvitation.objects.filter(
                    restaurant=invitation.restaurant,
                    email=email,
                    is_accepted=False,
                ).update(status='EXPIRED', expires_at=dj_tz.now())

                # Notify Lua agent if phone is available
                if phone:
                    from notifications.services import notification_service
                    lang = get_effective_language(user=user, restaurant=invitation.restaurant)
                    notification_service.send_lua_invitation_accepted(
                        invitation_token=invitation.invitation_token,
                        phone=phone,
                        first_name=user.first_name,
                        flow_data={'last_name': user.last_name, 'email': user.email},
                        language=lang,
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
