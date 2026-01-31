"""
Agent-specific views for scheduling operations.
These endpoints use LUA_WEBHOOK_API_KEY authentication instead of JWT.
"""
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework import status, permissions
from django.conf import settings
from django.utils import timezone
from datetime import datetime, time, timedelta
from django.db.models import Q
import re
import unicodedata
from difflib import SequenceMatcher

from accounts.models import CustomUser, Restaurant
from .models import AssignedShift, WeeklySchedule
from .serializers import AssignedShiftSerializer
from .services import SchedulingService
import logging
from core.utils import resolve_agent_restaurant_and_user
from .shift_auto_templates import auto_attach_templates_and_tasks, detect_shift_context, generate_shift_title

logger = logging.getLogger(__name__)


def validate_agent_key(request):
    """Validate the agent API key from Authorization header."""
    auth_header = request.headers.get('Authorization')
    expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
    
    if not expected_key:
        return False, "Agent key not configured"
    
    if not auth_header or auth_header != f"Bearer {expected_key}":
        return False, "Unauthorized"
    
    return True, None


@api_view(['GET'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_list_staff(request):
    """
    List all staff members for a restaurant.
    Used by the Lua agent to look up staff for scheduling.
    
    Query params:
    - restaurant_id: UUID of the restaurant (required)
    - name: Optional name filter (fuzzy match)
    """
    try:
        # Validate agent key
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'error': error}, status=status.HTTP_401_UNAUTHORIZED)

        restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=dict(request.query_params))
        if not restaurant:
            return Response(
                {'error': 'Unable to resolve restaurant context (no restaurant_id/sessionId/userId/email/phone/token provided).'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get staff for this restaurant
        queryset = CustomUser.objects.filter(
            restaurant=restaurant,
            is_active=True
        ).exclude(role='SUPER_ADMIN')
        
        def _norm(s: str) -> str:
            # Normalize for fuzzy matching: lowercase, strip diacritics, collapse spaces.
            s = (s or "").strip().lower()
            s = unicodedata.normalize("NFKD", s)
            s = "".join(ch for ch in s if not unicodedata.combining(ch))
            s = re.sub(r"\s+", " ", s)
            return s

        def _strip_titles(s: str) -> str:
            # Strip common titles so "Mr Ayoub" / "Mr. Ayoub" becomes "Ayoub" for lookup.
            s = (s or "").strip()
            s = re.sub(r"^(?:mr\.?|mrs\.?|ms\.?|miss\.?|dr\.?|prof\.?|sir|madam|mx\.?)\s+", "", s, flags=re.IGNORECASE)
            return s.strip()

        # Optional name filter (supports full names like "First Last" or just "First")
        # Strip titles so Miya/lua can pass "Mr Ayoub" and we still find "Ayoub"
        raw_name = (request.query_params.get("name") or "").strip()
        name_filter = _strip_titles(raw_name) or raw_name
        fuzzy_mode = False
        token_query = None
        if name_filter:
            tokens = [t for t in re.split(r"\s+", name_filter) if t]
            token_query = tokens[:]  # keep for ranking/response metadata
            filtered = queryset
            # AND across tokens, OR across fields (first/last/email/phone)
            for tok in tokens:
                filtered = filtered.filter(
                    Q(first_name__icontains=tok)
                    | Q(last_name__icontains=tok)
                    | Q(email__icontains=tok)
                    | Q(phone__icontains=tok)
                )

            # If token filter yields no results, fall back to fuzzy suggestions.
            if filtered.exists():
                queryset = filtered
            else:
                fuzzy_mode = True

        # If fuzzy_mode is enabled, return best matches (so the agent can confirm)
        if fuzzy_mode:
            query_n = _norm(name_filter)
            candidates = []
            for staff in queryset[:500]:
                full_a = _norm(f"{staff.first_name} {staff.last_name}")
                full_b = _norm(f"{staff.last_name} {staff.first_name}")
                email_n = _norm(staff.email or "")
                score = max(
                    SequenceMatcher(None, query_n, full_a).ratio(),
                    SequenceMatcher(None, query_n, full_b).ratio(),
                    SequenceMatcher(None, query_n, email_n).ratio(),
                )
                # Only keep reasonably close matches
                if score >= 0.72:
                    candidates.append((score, staff))

            candidates.sort(key=lambda x: x[0], reverse=True)
            queryset = [s for _, s in candidates[:8]]
        
        # If we have a name filter and a lot of matches, rank them in Python so the agent can
        # confidently pick the best match (especially for first-name-only queries).
        ranked = None
        if name_filter and not fuzzy_mode:
            toks = token_query or [name_filter]
            toks_n = [_norm(t) for t in toks if t]

            def _rank_staff(staff):
                fn = _norm(staff.first_name or "")
                ln = _norm(staff.last_name or "")
                full = (fn + " " + ln).strip()
                score = 0
                # Higher weight for first-name matches for single-token queries
                if len(toks_n) == 1:
                    t = toks_n[0]
                    if fn == t:
                        score += 100
                    elif fn.startswith(t):
                        score += 70
                    elif t in fn:
                        score += 50
                    if ln == t:
                        score += 40
                    elif ln.startswith(t):
                        score += 25
                    elif t in ln:
                        score += 15
                    if full == t:
                        score += 20
                    elif full.startswith(t):
                        score += 10
                else:
                    # Multi-token: reward when all tokens appear in full name
                    if all(t in full for t in toks_n):
                        score += 80
                    # Bonus for token order (e.g. "first last" vs "last first")
                    joined = " ".join(toks_n)
                    if joined and joined in full:
                        score += 20
                return score

            # Cap initial DB fetch to keep it cheap
            candidates = list(queryset[:200])
            candidates.sort(key=_rank_staff, reverse=True)
            ranked = candidates[:25]

        staff_list = []
        iterable = ranked if ranked is not None else queryset
        for staff in iterable:
            staff_list.append({
                'id': str(staff.id),
                'first_name': staff.first_name,
                'last_name': staff.last_name,
                'full_name': f"{(staff.first_name or '').strip()} {(staff.last_name or '').strip()}".strip(),
                'email': staff.email,
                'role': staff.role,
                'phone': staff.phone or '',
                'match_mode': 'fuzzy' if fuzzy_mode else 'exact',
            })
        
        return Response(staff_list)
        
    except Exception as e:
        logger.exception("Agent staff list error")
        err = str(e).strip() if e else "Unable to list staff"
        if len(err) > 200:
            err = err[:197] + "..."
        return Response({'error': err}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_create_shift(request):
    """
    Create a shift for a staff member.
    Used by the Lua agent to schedule staff.
    
    Expected payload:
    {
        "restaurant_id": "uuid",
        "staff_id": "uuid",
        "shift_date": "YYYY-MM-DD",
        "start_time": "HH:MM",
        "end_time": "HH:MM",
        "role": "SERVER",  # optional, defaults to staff's role
        "notes": "optional notes"
    }
    """
    try:
        # Validate agent key
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
        
        data = request.data
        
        # Required fields
        restaurant_id = data.get('restaurant_id') or data.get('restaurantId')
        staff_id = data.get('staff_id')
        shift_date_str = data.get('shift_date')
        start_time_str = data.get('start_time')
        end_time_str = data.get('end_time')
        
        if not all([staff_id, shift_date_str, start_time_str, end_time_str]):
            return Response({
                'success': False,
                'error': 'Missing required fields: staff_id, shift_date, start_time, end_time'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Resolve restaurant context (restaurant_id optional)
        restaurant = None
        acting_user = None
        if restaurant_id:
            try:
                restaurant = Restaurant.objects.get(id=restaurant_id)
            except Restaurant.DoesNotExist:
                restaurant = None
        if not restaurant:
            restaurant, acting_user = resolve_agent_restaurant_and_user(request=request, payload=data)
        if not restaurant:
            return Response({
                'success': False,
                'error': 'Unable to resolve restaurant context (provide restaurant_id or include sessionId/userId/email/phone/token).'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate staff
        try:
            staff = CustomUser.objects.get(id=staff_id, restaurant=restaurant)
        except CustomUser.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Staff member not found in this restaurant'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Parse date and times
        try:
            shift_date = datetime.strptime(shift_date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({
                'success': False,
                'error': 'Invalid shift_date format. Use YYYY-MM-DD'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Handle HH:MM or HH:MM:SS format
            if len(start_time_str) == 5:
                start_time = datetime.strptime(start_time_str, '%H:%M').time()
            else:
                start_time = datetime.strptime(start_time_str, '%H:%M:%S').time()
                
            if len(end_time_str) == 5:
                end_time = datetime.strptime(end_time_str, '%H:%M').time()
            else:
                end_time = datetime.strptime(end_time_str, '%H:%M:%S').time()
        except ValueError:
            return Response({
                'success': False,
                'error': 'Invalid time format. Use HH:MM or HH:MM:SS'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Determine role
        role = data.get('role') or staff.role or 'SERVER'

        # Optional metadata to improve titles/context
        department = data.get('department') or None
        workspace_location = data.get('workspace_location') or data.get('workspaceLocation') or None
        
        # Get or create weekly schedule for this date
        # Calculate week start (Monday)
        days_since_monday = shift_date.weekday()
        week_start = shift_date - timedelta(days=days_since_monday)
        week_end = week_start + timedelta(days=6)
        
        schedule, created = WeeklySchedule.objects.get_or_create(
            restaurant=restaurant,
            week_start=week_start,
            defaults={'week_end': week_end}
        )
        
        # Create datetime objects for start and end
        start_datetime = timezone.datetime.combine(shift_date, start_time)
        end_datetime = timezone.datetime.combine(shift_date, end_time)
        
        # Make timezone-aware if needed
        if timezone.is_naive(start_datetime):
            start_datetime = timezone.make_aware(start_datetime)
        if timezone.is_naive(end_datetime):
            end_datetime = timezone.make_aware(end_datetime)
        
        # Check for conflicts
        workspace_location = data.get('workspace_location') or data.get('workspaceLocation')
        conflicts = SchedulingService.detect_scheduling_conflicts(
            staff_id,
            shift_date,
            start_time,
            end_time,
            workspace_location=workspace_location
        )
        
        if conflicts:
            # Use the descriptive message from the first conflict
            conflict = conflicts[0]
            message = conflict.get('message', f"{staff.first_name} has a conflict at this time")
            return Response({
                'success': False,
                'error': f"Scheduling conflict: {message}",
                'conflicts': conflicts
            }, status=status.HTTP_409_CONFLICT)
        
        # Optional shift title/context (used for auto template association)
        shift_title = data.get('shift_title') or data.get('shiftTitle') or data.get('title')
        shift_notes = data.get('notes', '') or ''

        # Auto-generate a descriptive title if not provided
        if not shift_title:
            inferred_context = detect_shift_context(
                shift_title=None,
                shift_notes=shift_notes,
                start_dt=start_datetime,
                end_dt=end_datetime,
                restaurant=restaurant,
            )
            shift_title = generate_shift_title(
                shift_context=inferred_context,
                staff_role=role.upper(),
                department=department,
                workspace_location=workspace_location,
            )

        # Create the shift
        shift = AssignedShift.objects.create(
            schedule=schedule,
            staff=staff,
            shift_date=shift_date,
            start_time=start_datetime,
            end_time=end_datetime,
            role=role.upper(),
            # Frontend calendar uses `notes` as the visible shift title.
            notes=shift_title,
            # Keep instructions separate so the title stays clean.
            preparation_instructions=shift_notes,
            department=department,
            workspace_location=workspace_location,
            status='SCHEDULED'
            ,
            created_by=acting_user,
            last_modified_by=acting_user,
        )
        
        # Add staff to staff_members M2M
        shift.staff_members.add(staff)

        # Assign a deterministic "random" color per staff
        try:
            SchedulingService.ensure_shift_color(shift)
        except Exception:
            pass

        # Auto-attach relevant Process/Task templates and generate tasks/checklists
        attach_result = None
        try:
            attach_result = auto_attach_templates_and_tasks(
                shift=shift,
                restaurant=restaurant,
                assignee=staff,
                staff_role=role.upper(),
                shift_title=shift_title,
                instructions=shift_notes,
                created_by=acting_user,
            )
        except Exception as e:
            # Do not fail shift creation if automation fails; log for observability
            logger.warning(f"Agent create shift: auto-attach templates failed: {e}")

        # Immediately notify the assigned staff (WhatsApp + in-app), with audit logging
        try:
            # For agent-created shifts, force WhatsApp delivery (bypass user prefs).
            SchedulingService.notify_shift_assignment(shift, force_whatsapp=True)
        except Exception as e:
            logger.warning(f"Agent create shift: notification failed: {e}")
        
        return Response({
            'success': True,
            'shift': {
                'id': str(shift.id),
                'staff_id': str(staff.id),
                'staff_name': f"{staff.first_name} {staff.last_name}",
                'shift_date': str(shift_date),
                'start_time': start_time_str,
                'end_time': end_time_str,
                'role': role.upper(),
                'color': shift.color,
                'title': shift_title,
                'task_templates': [str(t.id) for t in (shift.task_templates.all() if hasattr(shift, 'task_templates') else [])],
            },
            'auto_association': ({
                'shift_context': attach_result.shift_context,
                'used_fallback_custom_template': attach_result.used_fallback_custom_template,
                'attached_template_names': [t.name for t in attach_result.used_templates],
                'created_shift_tasks': attach_result.created_shift_tasks,
                'created_checklist_executions': attach_result.created_checklist_executions,
            } if attach_result else None),
            'message': f"Successfully scheduled {staff.first_name} for {shift_date} from {start_time_str} to {end_time_str}"
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        logger.exception("Agent create shift error")
        # Return a short, agent-friendly message so Miya doesn't show "try again later"
        err = str(e).strip() if e else "Unknown error"
        if "conflict" in err.lower() or "already has" in err.lower():
            return Response({'success': False, 'error': err}, status=status.HTTP_409_CONFLICT)
        if "not found" in err.lower() or "does not exist" in err.lower():
            return Response({'success': False, 'error': err}, status=status.HTTP_404_NOT_FOUND)
        # Keep message brief for the agent to relay
        if len(err) > 200:
            err = err[:197] + "..."
        return Response({
            'success': False,
            'error': f"Could not create shift: {err}"
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_send_shift_notification(request):
    """
    Send WhatsApp notification about a scheduled shift using the staff_weekly_schedule template.
    
    Expected payload:
    {
        "shift_id": "uuid",
        "staff_id": "uuid",  # optional if shift_id provided
    }
    """
    try:
        # Validate agent key
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
        
        data = request.data
        shift_id = data.get('shift_id')
        staff_id = data.get('staff_id')
        
        if not shift_id and not staff_id:
            return Response({
                'success': False,
                'error': 'Either shift_id or staff_id is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get shift if provided
        shift = None
        staff = None
        
        if shift_id:
            try:
                shift = AssignedShift.objects.select_related('staff', 'schedule__restaurant').get(id=shift_id)
                staff = shift.staff
            except AssignedShift.DoesNotExist:
                return Response({
                    'success': False,
                    'error': 'Shift not found'
                }, status=status.HTTP_404_NOT_FOUND)
        elif staff_id:
            try:
                staff = CustomUser.objects.get(id=staff_id)
            except CustomUser.DoesNotExist:
                return Response({
                    'success': False,
                    'error': 'Staff not found'
                }, status=status.HTTP_404_NOT_FOUND)
        
        # Get phone number
        phone = staff.phone if staff else None
        if not phone:
            return Response({
                'success': False,
                'error': 'Staff member has no phone number'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Send WhatsApp template message
        from notifications.services import notification_service
        
        if shift:
            # Build template components for staff_weekly_schedule
            shift_date = shift.shift_date.strftime('%A, %B %d')
            start = shift.start_time.strftime('%H:%M') if hasattr(shift.start_time, 'strftime') else str(shift.start_time)[:5]
            end = shift.end_time.strftime('%H:%M') if hasattr(shift.end_time, 'strftime') else str(shift.end_time)[:5]
            restaurant_name = shift.schedule.restaurant.name
            role = shift.role or 'Staff'
            
            # Template parameters: name, restaurant, date, start_time, end_time, role
            components = [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": staff.first_name or "Team Member"},
                        {"type": "text", "text": restaurant_name},
                        {"type": "text", "text": shift_date},
                        {"type": "text", "text": start},
                        {"type": "text", "text": end},
                        {"type": "text", "text": role}
                    ]
                }
            ]
            
            ok, resp = notification_service.send_whatsapp_template(
                phone=phone,
                template_name='staff_weekly_schedule',
                language_code='en_US',
                components=components
            )
            
            if not ok:
                logger.warning(f"Template send failed, falling back to text: {resp}")
                # Fallback to text message
                fallback_message = (
                    f"Hi {staff.first_name}! 📅\n\n"
                    f"You've been scheduled for a shift at {restaurant_name}:\n\n"
                    f"📆 Date: {shift_date}\n"
                    f"⏰ Time: {start} - {end}\n"
                    f"👔 Role: {role}\n\n"
                    f"Please reply 'CONFIRM' to confirm your availability."
                )
                ok, resp = notification_service.send_whatsapp_text(phone=phone, body=fallback_message)
        else:
            # No shift data, just send a generic text
            message = f"Hi {staff.first_name}! You have new shift information."
            ok, resp = notification_service.send_whatsapp_text(phone=phone, body=message)
        
        # Mark shift as notified
        if shift:
            shift.notification_sent = True
            shift.notification_sent_at = timezone.now()
            shift.notification_channels = ['whatsapp']
            shift.save(update_fields=['notification_sent', 'notification_sent_at', 'notification_channels'])
        
        return Response({
            'success': ok,
            'message': f"Notification sent to {staff.first_name}" if ok else "Failed to send notification",
            'whatsapp_result': resp
        })
        
    except Exception as e:
        logger.error(f"Agent send notification error: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

@api_view(['POST'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_optimize_schedule(request):
    """
    Generate optimized schedule for a week.
    Used by the Lua agent to automatically fill a week's schedule.
    
    Expected payload:
    {
        "restaurant_id": "uuid",
        "week_start": "YYYY-MM-DD",
        "department": "kitchen/service/all"
    }
    """
    try:
        # Validate agent key
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
        
        data = request.data
        restaurant_id = data.get('restaurant_id') or data.get('restaurantId')
        week_start = data.get('week_start')
        department = data.get('department')
        
        if not week_start:
            return Response({
                'success': False,
                'error': 'Missing required fields: week_start'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        restaurant = None
        if restaurant_id:
            try:
                restaurant = Restaurant.objects.get(id=restaurant_id)
            except Restaurant.DoesNotExist:
                restaurant = None
        if not restaurant:
            restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=data)
        if not restaurant:
            return Response({
                'success': False,
                'error': 'Unable to resolve restaurant context (provide restaurant_id or include sessionId/userId/email/phone/token).'
            }, status=status.HTTP_400_BAD_REQUEST)
            
        # OptimizationService will handle the business logic
        from .services import OptimizationService
        result = OptimizationService.optimize_schedule(
            str(restaurant.id),
            week_start,
            department
        )
        
        if result.get('error'):
            return Response({
                'success': False,
                'error': result.get('error')
            }, status=status.HTTP_400_BAD_REQUEST)
            
        return Response({
            'success': True,
            **result
        })
        
    except Exception as e:
        logger.error(f"Agent optimize schedule error: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
@api_view(['GET'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_get_restaurant_details(request):
    """
    Get restaurant details for the agent.
    Returns business hours, peaks, and other context.
    """
    try:
        # Validate agent key
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'error': error}, status=status.HTTP_401_UNAUTHORIZED)

        restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=dict(request.query_params))
        if not restaurant:
            return Response(
                {'error': 'Unable to resolve restaurant context (no restaurant_id/sessionId/userId/email/phone/token provided).'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Default peak definitions
        peak_definitions = {
            'lunch': {'start': '12:00', 'end': '15:00'},
            'dinner': {'start': '19:00', 'end': '23:00'},
            'breakfast': {'start': '07:00', 'end': '10:30'}
        }
        
        # Build response data
        data = {
            'id': str(restaurant.id),
            'name': restaurant.name,
            'timezone': str(restaurant.timezone) if hasattr(restaurant, 'timezone') else 'Africa/Casablanca',
            'operating_hours': getattr(restaurant, 'operating_hours', {}),
            'restaurant_type': getattr(restaurant, 'restaurant_type', 'CASUAL_DINING'),
            'max_weekly_hours': float(getattr(restaurant, 'max_weekly_hours', 40.0)),
            'min_rest_hours': float(getattr(restaurant, 'min_rest_hours', 11.0)),
            'general_settings': {
                'peak_periods': getattr(restaurant, 'general_settings', {}).get('peak_periods', peak_definitions) if isinstance(getattr(restaurant, 'general_settings', None), dict) else peak_definitions
            },
            'break_duration': getattr(restaurant, 'break_duration', 30)
        }
        
        return Response(data)
        
    except Exception as e:
        logger.error(f"Agent restaurant details error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
 
 
@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_get_operational_advice(request):
    """
    Get operational advice for staffing levels and shift structures.
    Used by Miya to suggest optimal staffing.
    """
    try:
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'error': error}, status=status.HTTP_401_UNAUTHORIZED)

        restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=dict(request.query_params))
        if not restaurant:
            return Response({'error': 'Restaurant context not found.'}, status=status.HTTP_400_BAD_REQUEST)

        date_str = request.query_params.get('date')
        if not date_str:
            date_str = timezone.now().date().isoformat()
        
        from .ai_scheduler import AIScheduler
        scheduler = AIScheduler(restaurant)
        
        # Get demand forecast for the day
        day_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        demand_forecast = scheduler._get_demand_forecast(day_date)
        day_name = day_date.strftime('%A')
        current_demand = demand_forecast.get(day_name, 'MEDIUM')
        
        # Calculate required roles based on demand
        historical_patterns = scheduler._get_historical_patterns(day_date)
        required_roles = scheduler._calculate_required_roles(current_demand, historical_patterns)
        
        # Advice on shift splits
        shift_splits = []
        if current_demand == 'HIGH':
            shift_splits = [
                {'type': 'LUNCH_PEAK', 'time': '11:00-15:00', 'reason': 'High volume expected during lunch.'},
                {'type': 'DINNER_PEAK', 'time': '18:00-22:00', 'reason': 'High volume expected during dinner.'}
            ]
        
        return Response({
            'status': 'success',
            'date': date_str,
            'demand_level': current_demand,
            'optimal_staffing': required_roles,
            'shift_split_suggestions': shift_splits,
            'restaurant_type': getattr(restaurant, 'restaurant_type', 'CASUAL_DINING'),
            'best_practices': [
                "Schedule your strongest team for peak hours.",
                "Ensure at least 11 hours of rest between shifts (clopening prevention).",
                f"For {getattr(restaurant, 'restaurant_type', 'CASUAL_DINING').lower()} style, focus on { 'service consistency' if restaurant.restaurant_type == 'FINE_DINING' else 'speed of service' }."
            ]
        })

    except Exception as e:
        logger.error(f"Agent operational advice error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_staff_by_phone(request):
    """
    Look up a staff member by their phone number.
    Returns staff info including their restaurant ID.
    
    Query params:
    - phone: Phone number (required)
    """
    try:
        # Validate agent key
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
        
        # Accept multiple common parameter names used by WhatsApp/Lua
        phone = (
            request.query_params.get('phone')
            or request.query_params.get('phoneNumber')
            or request.query_params.get('from')
        )
        if not phone:
            return Response(
                {'success': False, 'error': 'phone (or phoneNumber/from) query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Normalize phone number (remove common prefixes)
        phone_digits = ''.join(filter(str.isdigit, phone))
        default_cc = ''.join(filter(str.isdigit, str(getattr(settings, 'WHATSAPP_DEFAULT_COUNTRY_CODE', '') or '')))
        
        # Try to find staff by phone - check multiple phone formats
        staff = None
        possible_patterns = []
        if phone_digits:
            possible_patterns.extend([phone_digits, f"+{phone_digits}"])
            # Try last 10 digits (common DB storage pattern)
            if len(phone_digits) > 10:
                possible_patterns.append(phone_digits[-10:])
                possible_patterns.append(f"+{phone_digits[-10:]}")
            # If we have a default country code, try stripping it and/or adding local leading zero
            if default_cc and phone_digits.startswith(default_cc):
                local = phone_digits[len(default_cc):]
                if local:
                    possible_patterns.extend([local, f"0{local}", f"+{default_cc}{local}"])
            # If starts with 0, try without 0
            if phone_digits.startswith('0') and len(phone_digits) > 1:
                possible_patterns.append(phone_digits.lstrip('0'))
                if default_cc:
                    possible_patterns.append(f"{default_cc}{phone_digits.lstrip('0')}")
        
        # Deduplicate while preserving order
        seen = set()
        possible_patterns = [p for p in possible_patterns if p and not (p in seen or seen.add(p))]

        for pattern in possible_patterns:
            try:
                staff = CustomUser.objects.filter(
                    phone__icontains=pattern,
                    is_active=True
                ).exclude(role='SUPER_ADMIN').first()
                if staff:
                    break
            except Exception:
                continue
        
        if not staff:
            return Response({
                'success': False,
                'found': False,
                'error': 'No staff member found with this phone number'
            }, status=status.HTTP_404_NOT_FOUND)
        
        return Response({
            'success': True,
            'found': True,
            'staff': {
                'id': str(staff.id),
                'first_name': staff.first_name,
                'last_name': staff.last_name,
                'email': staff.email,
                'phone': staff.phone,
                'role': staff.role,
                'restaurant_id': str(staff.restaurant_id) if staff.restaurant_id else None,
                'restaurant_name': staff.restaurant.name if staff.restaurant else None
            }
        })
        
    except Exception as e:
        logger.error(f"Agent staff by phone error: {e}")
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_get_my_shifts(request):
    """
    Get a staff member's shifts for this week and next week (default), resolving the staff via:
    - staff_id
    - phone / phoneNumber / from (WhatsApp sender)
    - sessionId/userId/email/token via resolve_agent_restaurant_and_user

    Query params:
    - staff_id: UUID (optional)
    - phone|phoneNumber|from: phone number (optional)
    - weeks: int (optional, default 2)
    - start_date / end_date: YYYY-MM-DD (optional override)
    - when: "today" | "tomorrow" | "<weekday>" (optional override)
    - day: alias for when (optional)
    """
    try:
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)

        qp = dict(request.query_params)
        # Flatten QueryDict values
        qp = {k: (v[0] if isinstance(v, list) and v else v) for k, v in qp.items()}

        staff_id = qp.get('staff_id') or qp.get('staffId') or qp.get('userId') or qp.get('user_id')
        phone = qp.get('phone') or qp.get('phoneNumber') or qp.get('from')

        staff = None
        restaurant = None

        # 1) staff_id direct
        if staff_id:
            try:
                staff = CustomUser.objects.filter(id=staff_id, is_active=True).exclude(role='SUPER_ADMIN').select_related('restaurant').first()
            except Exception:
                staff = None
            if staff:
                restaurant = staff.restaurant

        # 2) Resolve via phone (WhatsApp sender)
        if not staff and phone:
            phone_digits = ''.join(filter(str.isdigit, str(phone)))
            default_cc = ''.join(filter(str.isdigit, str(getattr(settings, 'WHATSAPP_DEFAULT_COUNTRY_CODE', '') or '')))
            patterns = []
            if phone_digits:
                patterns.extend([phone_digits, f"+{phone_digits}"])
                if len(phone_digits) > 10:
                    patterns.extend([phone_digits[-10:], f"+{phone_digits[-10:]}"])
                if default_cc and phone_digits.startswith(default_cc):
                    local = phone_digits[len(default_cc):]
                    if local:
                        patterns.extend([local, f"0{local}", f"+{default_cc}{local}"])
                if phone_digits.startswith('0') and len(phone_digits) > 1:
                    stripped = phone_digits.lstrip('0')
                    patterns.append(stripped)
                    if default_cc:
                        patterns.append(f"{default_cc}{stripped}")
            seen = set()
            patterns = [p for p in patterns if p and not (p in seen or seen.add(p))]
            for p in patterns:
                staff = CustomUser.objects.filter(phone__icontains=p, is_active=True).exclude(role='SUPER_ADMIN').select_related('restaurant').first()
                if staff:
                    restaurant = staff.restaurant
                    break

        # 3) Fallback: resolve via sessionId/email/token etc
        if not staff:
            restaurant, staff = resolve_agent_restaurant_and_user(request=request, payload=qp)

        if not staff:
            return Response({
                'success': False,
                'error': 'Unable to resolve staff profile from this request (missing/unknown phone or staff_id).'
            }, status=status.HTTP_404_NOT_FOUND)

        if not restaurant:
            restaurant = getattr(staff, 'restaurant', None)

        # Determine date range
        # - default: this week + next week
        # - override: start_date/end_date
        # - override: when/day = today|tomorrow|monday|tuesday|...
        start_date = qp.get('start_date')
        end_date = qp.get('end_date')
        when = (qp.get('when') or qp.get('day') or '').strip().lower()
        try:
            weeks = int(qp.get('weeks') or 2)
        except Exception:
            weeks = 2
        weeks = max(1, min(8, weeks))

        today = timezone.localdate()
        week_start = today - timedelta(days=today.weekday())
        range_start = week_start
        range_end = week_start + timedelta(days=(7 * weeks) - 1)

        # "when" override
        if when:
            weekday_map = {
                'mon': 0, 'monday': 0,
                'tue': 1, 'tues': 1, 'tuesday': 1,
                'wed': 2, 'wednesday': 2,
                'thu': 3, 'thur': 3, 'thurs': 3, 'thursday': 3,
                'fri': 4, 'friday': 4,
                'sat': 5, 'saturday': 5,
                'sun': 6, 'sunday': 6,
            }
            if when in ('today', 'now'):
                range_start = today
                range_end = today
            elif when in ('tomorrow',):
                range_start = today + timedelta(days=1)
                range_end = range_start
            elif when in weekday_map:
                target = weekday_map[when]
                # Next occurrence (including today)
                delta = (target - today.weekday()) % 7
                range_start = today + timedelta(days=delta)
                range_end = range_start
            # else: ignore unknown value and fall back to default/week range

        if start_date:
            try:
                range_start = datetime.strptime(start_date, '%Y-%m-%d').date()
            except Exception:
                pass
        if end_date:
            try:
                range_end = datetime.strptime(end_date, '%Y-%m-%d').date()
            except Exception:
                pass

        shifts_qs = AssignedShift.objects.filter(
            schedule__restaurant=restaurant,
            shift_date__gte=range_start,
            shift_date__lte=range_end,
        ).filter(Q(staff=staff) | Q(staff_members=staff)).select_related('schedule__restaurant').order_by('shift_date', 'start_time')

        shifts = []
        for s in shifts_qs:
            try:
                start_dt = timezone.localtime(s.start_time) if s.start_time else None
                end_dt = timezone.localtime(s.end_time) if s.end_time else None
            except Exception:
                start_dt = s.start_time
                end_dt = s.end_time
            shifts.append({
                'id': str(s.id),
                'restaurant_id': str(restaurant.id) if restaurant else None,
                'restaurant_name': restaurant.name if restaurant else None,
                'shift_date': s.shift_date.isoformat() if s.shift_date else None,
                'start_time': start_dt.strftime('%H:%M') if hasattr(start_dt, 'strftime') else (str(start_dt)[:5] if start_dt else None),
                'end_time': end_dt.strftime('%H:%M') if hasattr(end_dt, 'strftime') else (str(end_dt)[:5] if end_dt else None),
                'role': (s.role or '').upper(),
                'title': (getattr(s, 'notes', '') or '').strip(),
                'department': (getattr(s, 'department', '') or '').strip(),
                'workspace_location': (getattr(s, 'workspace_location', '') or '').strip(),
                'instructions': (getattr(s, 'preparation_instructions', '') or '').strip(),
                'status': s.status,
            })

        return Response({
            'success': True,
            'staff': {
                'id': str(staff.id),
                'first_name': staff.first_name,
                'last_name': staff.last_name,
                'phone': staff.phone,
                'restaurant_id': str(getattr(staff, 'restaurant_id', '') or ''),
                'restaurant_name': getattr(getattr(staff, 'restaurant', None), 'name', None),
            },
            'range': {
                'start_date': range_start.isoformat(),
                'end_date': range_end.isoformat(),
                'weeks': weeks,
            },
            'count': len(shifts),
            'shifts': shifts,
        })

    except Exception as e:
        logger.error(f"Agent get my shifts error: {e}")
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_detect_conflicts(request):
    """
    Detect scheduling conflicts for the agent.
    Bypasses JWT auth - uses agent key.
    """
    try:
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'error': error}, status=status.HTTP_401_UNAUTHORIZED)

        data = request.query_params
        staff_id = data.get('staff_id')
        shift_date_str = data.get('shift_date')
        start_time_str = data.get('start_time')
        end_time_str = data.get('end_time')
        workspace_location = data.get('workspace_location') or data.get('workspaceLocation')

        if not all([staff_id, shift_date_str, start_time_str, end_time_str]):
            return Response({'error': 'Missing required fields'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            shift_date = datetime.strptime(shift_date_str, '%Y-%m-%d').date()
            # Handle both HH:MM and HH:MM:SS
            if len(start_time_str) == 5:
                start_time = datetime.strptime(start_time_str, '%H:%M').time()
            else:
                start_time = datetime.strptime(start_time_str, '%H:%M:%S').time()
                
            if len(end_time_str) == 5:
                end_time = datetime.strptime(end_time_str, '%H:%M').time()
            else:
                end_time = datetime.strptime(end_time_str, '%H:%M:%S').time()
        except ValueError:
            return Response({'error': 'Invalid date/time format'}, status=status.HTTP_400_BAD_REQUEST)

        conflicts = SchedulingService.detect_scheduling_conflicts(
            staff_id, shift_date, start_time, end_time, workspace_location=workspace_location
        )
        
        return Response({
            'has_conflicts': len(conflicts) > 0,
            'conflicts': conflicts
        })

    except Exception as e:
        logger.error(f"Agent detect conflicts error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_list_shifts(request):
    """
    List assigned shifts for a restaurant.
    Used by the Lua agent to show schedules and find who is on duty.
    """
    try:
        # Validate agent key
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'error': error}, status=status.HTTP_401_UNAUTHORIZED)

        restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=dict(request.query_params))
        if not restaurant:
            return Response(
                {'error': 'Unable to resolve restaurant context.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        queryset = AssignedShift.objects.filter(weekly_schedule__restaurant=restaurant)

        # Filters
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')
        staff_id = request.query_params.get('staff_id')
        role = request.query_params.get('role')

        if date_from:
            queryset = queryset.filter(shift_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(shift_date__lte=date_to)
        if staff_id:
            queryset = queryset.filter(staff_id=staff_id)
        if role:
            queryset = queryset.filter(role=role)

        queryset = queryset.select_related('staff').order_by('shift_date', 'start_time')
        
        serializer = AssignedShiftSerializer(queryset, many=True)
        return Response(serializer.data)

    except Exception as e:
        logger.exception("Agent list shifts error")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
