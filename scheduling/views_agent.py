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
from django.db.models import Q, Value
from django.db.models.functions import Concat
import re
import uuid
import unicodedata
from difflib import SequenceMatcher

from accounts.models import CustomUser, Restaurant
from .models import AssignedShift, WeeklySchedule, AgentMemory, ShiftTask
from .serializers import AssignedShiftSerializer
from .services import SchedulingService
import logging
from core.utils import resolve_agent_restaurant_and_user
from .shift_auto_templates import (
    AutoAttachResult,
    auto_attach_templates_and_tasks,
    detect_shift_context,
    generate_shift_title,
    instantiate_shift_tasks_from_template,
    ensure_checklist_for_task_template,
    ensure_checklist_execution_for_shift,
)
from .task_templates import TaskTemplate
from .recurring_views import _dates_for_frequency, _dates_for_days_of_week

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


def _try_jwt_restaurant_and_user(request):
    """
    If Authorization is a valid user JWT (e.g. token from dashboard metadata),
    return (restaurant, user) so agent endpoints can resolve context without
    Lua forwarding restaurant_id in the body. Lets Miya work when Lua sends
    the user's token as Bearer. If user has no direct restaurant (e.g. super admin),
    use first restaurant from restaurant_roles.
    """
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return None, None
    token = auth_header[7:].strip()
    if not token:
        return None, None
    # Avoid treating the fixed API key as JWT
    expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
    if expected_key and token == expected_key:
        return None, None
    try:
        from rest_framework_simplejwt.authentication import JWTAuthentication
        jwt_auth = JWTAuthentication()
        validated = jwt_auth.get_validated_token(token)
        user = jwt_auth.get_user(validated)
        if not user:
            return None, None
        if getattr(user, 'restaurant_id', None) and getattr(user, 'restaurant', None):
            return user.restaurant, user
        if getattr(user, 'restaurant', None):
            return user.restaurant, user
        # User has no direct restaurant (e.g. SUPER_ADMIN); use first from restaurant_roles
        if hasattr(user, 'restaurant_roles'):
            first_role = user.restaurant_roles.select_related('restaurant').first()
            if first_role and getattr(first_role, 'restaurant', None):
                return first_role.restaurant, user
    except Exception:
        pass
    return None, None


def _agent_payload_from_request(request):
    """Build payload from query params and, for POST, body/metadata so Lua can send context either way."""
    payload = dict(request.query_params)
    if request.method == 'POST' and isinstance(getattr(request, 'data', None), dict):
        for k, v in request.data.items():
            if k == 'metadata' and isinstance(v, dict):
                for mk, mv in v.items():
                    payload.setdefault(mk, mv)
            else:
                payload.setdefault(k, v)
    return payload


def _explicit_restaurant_id_from_request(request):
    """Get restaurant ID from header or body when Miya sends it from context (widget). Prefer this so dashboard context wins."""
    rid = request.META.get('HTTP_X_RESTAURANT_ID')
    if rid:
        return rid.strip() or None
    payload = _agent_payload_from_request(request)
    meta = payload.get('metadata') if isinstance(payload.get('metadata'), dict) else {}
    for key in ('restaurant_id', 'restaurantId', 'restaurant'):
        val = payload.get(key) or meta.get(key)
        if val:
            return val[0] if isinstance(val, (list, tuple)) and val else val
    return None


@api_view(['GET', 'POST'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_list_staff(request):
    """
    List all staff members for a restaurant.
    Used by the Lua agent to look up staff for scheduling.
    Accepts GET (params in query) or POST (params in body/metadata).
    Auth: Bearer LUA_WEBHOOK_API_KEY or Bearer <user JWT> (dashboard token).
    """
    try:
        restaurant = None
        # 1) Prefer X-Restaurant-Id first (Miya sends this from widget context; ensures dashboard and agent see same restaurant)
        explicit_rid = _explicit_restaurant_id_from_request(request)
        if explicit_rid:
            rid = explicit_rid[0] if isinstance(explicit_rid, (list, tuple)) and explicit_rid else explicit_rid
            if rid and isinstance(rid, str) and rid.strip():
                try:
                    restaurant = Restaurant.objects.get(id=rid.strip())
                except (Restaurant.DoesNotExist, ValueError, TypeError):
                    pass
        # 2) Else try JWT (dashboard token as Bearer)
        if not restaurant:
            restaurant, _ = _try_jwt_restaurant_and_user(request)
        # 3) Else agent key + resolve from payload/sessionId
        if not restaurant:
            is_valid, error = validate_agent_key(request)
            if not is_valid:
                return Response({'error': error}, status=status.HTTP_401_UNAUTHORIZED)
            payload = _agent_payload_from_request(request)
            restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=payload)
        if not restaurant:
            return Response(
                {'error': 'Unable to resolve restaurant context (no restaurant_id/sessionId/userId/email/phone/token provided).'},
                status=status.HTTP_400_BAD_REQUEST
            )
        payload = _agent_payload_from_request(request)
        # Get staff for this restaurant (queryset used for count_only and list)
        queryset = CustomUser.objects.filter(
            restaurant=restaurant,
            is_active=True
        ).exclude(role='SUPER_ADMIN')

        # If only count is requested (e.g. "how many staff?"), return count + breakdown so one tool can serve both list and count
        _co = payload.get('count_only') or payload.get('countOnly')
        count_only_val = _co[0] if isinstance(_co, (list, tuple)) and _co else _co
        if count_only_val in (True, 'true', '1', 1):
            from django.db.models import Count
            count = queryset.count()
            by_role = dict(queryset.values('role').annotate(n=Count('id')).values_list('role', 'n'))
            return Response({
                'count': count,
                'active': count,
                'by_role': by_role,
                'restaurant_id': str(restaurant.id),
                'restaurant_name': restaurant.name,
                'message': f"There are {count} staff member{'s' if count != 1 else ''} in {restaurant.name}." if count else f"There are no staff members currently registered in {restaurant.name}.",
            })
        
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
        name_val = payload.get("name")
        raw_name = (name_val[0] if isinstance(name_val, (list, tuple)) and name_val else name_val or "").strip()
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

            # If token filter yields no results, try matching full name string (e.g. "salima majdallah" vs "Salima Majdallah").
            if not filtered.exists() and name_filter:
                filtered = queryset.annotate(
                    full_name=Concat("first_name", Value(" "), "last_name")
                ).filter(full_name__icontains=name_filter)

            # If still no results, fall back to fuzzy suggestions.
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
                # Keep close matches (lower threshold so "Salima Majdallah" / "salima majdallah" and minor typos still match)
                if score >= 0.55:
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


@api_view(['GET', 'POST'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_staff_count(request):
    """
    Return staff count and optional breakdown for the restaurant.
    Used by Miya to answer "how many staff do I have?" and similar queries.
    Auth: Bearer LUA_WEBHOOK_API_KEY or Bearer <user JWT> (dashboard token).
    """
    try:
        restaurant = None
        explicit_rid = _explicit_restaurant_id_from_request(request)
        if explicit_rid:
            rid = explicit_rid[0] if isinstance(explicit_rid, (list, tuple)) and explicit_rid else explicit_rid
            if rid and isinstance(rid, str) and rid.strip():
                try:
                    restaurant = Restaurant.objects.get(id=rid.strip())
                except (Restaurant.DoesNotExist, ValueError, TypeError):
                    pass
        if not restaurant:
            restaurant, _ = _try_jwt_restaurant_and_user(request)
        if not restaurant:
            is_valid, error = validate_agent_key(request)
            if not is_valid:
                return Response({'error': error}, status=status.HTTP_401_UNAUTHORIZED)
            payload = _agent_payload_from_request(request)
            restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=payload)
        if not restaurant:
            return Response(
                {'error': 'Unable to resolve restaurant context (no restaurant_id/sessionId/userId/email/phone/token provided).'},
                status=status.HTTP_400_BAD_REQUEST
            )

        queryset = CustomUser.objects.filter(
            restaurant=restaurant,
            is_active=True
        ).exclude(role='SUPER_ADMIN')

        count = queryset.count()
        from django.db.models import Count
        by_role = dict(queryset.values('role').annotate(n=Count('id')).values_list('role', 'n'))

        return Response({
            'count': count,
            'active': count,
            'by_role': by_role,
            'restaurant_id': str(restaurant.id),
            'restaurant_name': restaurant.name,
            'message': f"There are {count} staff member{'s' if count != 1 else ''} in {restaurant.name}." if count else f"There are no staff members currently registered in {restaurant.name}.",
        })
    except Exception as e:
        logger.exception("Agent staff count error")
        err = str(e).strip() if e else "Unable to get staff count"
        if len(err) > 200:
            err = err[:197] + "..."
        return Response({'error': err}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _resolve_restaurant_for_agent(request):
    """Resolve restaurant for agent endpoints."""
    restaurant = None
    explicit_rid = _explicit_restaurant_id_from_request(request)
    if explicit_rid:
        rid = explicit_rid[0] if isinstance(explicit_rid, (list, tuple)) and explicit_rid else explicit_rid
        if rid and isinstance(rid, str) and rid.strip():
            try:
                restaurant = Restaurant.objects.get(id=rid.strip())
            except (Restaurant.DoesNotExist, ValueError, TypeError):
                pass
    if not restaurant:
        restaurant, _ = _try_jwt_restaurant_and_user(request)
    if not restaurant:
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return None, {'error': error, 'status': 401}
        payload = _agent_payload_from_request(request)
        restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=payload)
    if not restaurant:
        return None, {'error': 'Unable to resolve restaurant context.', 'status': 400}
    return restaurant, None


@api_view(['GET'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_list_task_templates(request):
    """
    List task templates for the restaurant.
    Used by Miya to assign tasks/processes to shifts (e.g. "assign the opening checklist").
    Auth: Bearer LUA_WEBHOOK_API_KEY or Bearer <user JWT>.
    Query: restaurant_id (or X-Restaurant-Id header).
    """
    try:
        restaurant, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response({'error': err['error']}, status=err['status'])
        templates = TaskTemplate.objects.filter(
            restaurant=restaurant,
            is_active=True
        ).order_by('name').values('id', 'name', 'template_type', 'description')
        return Response({
            'task_templates': list(templates),
            'restaurant_id': str(restaurant.id),
        })
    except Exception as e:
        logger.exception("Agent list task templates error")
        err = str(e).strip() if e else "Unable to list task templates"
        return Response({'error': err[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_create_task_template(request):
    """
    Create a task template for the restaurant.
    Used by Miya when a requested template doesn't exist - Miya can create the perfect template for that shift.
    Auth: Bearer LUA_WEBHOOK_API_KEY or Bearer <user JWT>.
    Payload: restaurant_id, name, description (optional), template_type (optional, default CUSTOM),
             tasks: [{title, description?, priority?}]
    """
    try:
        restaurant, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response({'error': err['error']}, status=err['status'])
        data = request.data if isinstance(getattr(request, 'data', None), dict) else {}
        name = (data.get('name') or '').strip()
        if not name:
            return Response({'error': 'Missing required field: name'}, status=status.HTTP_400_BAD_REQUEST)
        tasks_raw = data.get('tasks') or []
        if not isinstance(tasks_raw, list):
            return Response({'error': 'tasks must be an array of {title, description?, priority?}'}, status=status.HTTP_400_BAD_REQUEST)
        import uuid as uuid_mod
        tasks = []
        for t in tasks_raw:
            if not isinstance(t, dict):
                continue
            title = str(t.get('title') or '').strip()
            if not title:
                continue
            tasks.append({
                'id': str(uuid_mod.uuid4()),
                'title': title,
                'description': str(t.get('description') or '').strip(),
                'priority': (str(t.get('priority') or 'MEDIUM')).upper()[:20] or 'MEDIUM',
                'completed': False,
            })
        if not tasks:
            return Response({'error': 'tasks must contain at least one item with a title'}, status=status.HTTP_400_BAD_REQUEST)
        template_type = (data.get('template_type') or 'CUSTOM').upper()
        valid_types = [c[0] for c in TaskTemplate.TEMPLATE_TYPES]
        if template_type not in valid_types:
            template_type = 'CUSTOM'
        acting_user = None
        try:
            _, acting_user = _try_jwt_restaurant_and_user(request)
        except Exception:
            pass
        template = TaskTemplate.objects.create(
            restaurant=restaurant,
            name=name,
            description=(data.get('description') or '').strip() or None,
            template_type=template_type,
            tasks=tasks,
            frequency='CUSTOM',
            ai_generated=True,
            ai_prompt=data.get('ai_prompt') or f"Created by Miya for shift: {name}",
            created_by=acting_user,
            is_active=True,
        )
        return Response({
            'success': True,
            'task_template': {
                'id': str(template.id),
                'name': template.name,
                'template_type': template.template_type,
                'tasks_count': len(tasks),
            },
            'message': f"Created template '{template.name}' with {len(tasks)} task(s). Use task_template_ids=[{template.id}] when creating shifts.",
        }, status=status.HTTP_201_CREATED)
    except Exception as e:
        logger.exception("Agent create task template error")
        err = str(e).strip()[:200] if e else "Unable to create task template"
        return Response({'error': err}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_attach_templates_to_shift(request):
    """
    Attach task templates to an existing shift.
    Used by Miya when manager says "add the opening checklist to Maria's shift".
    Auth: Bearer LUA_WEBHOOK_API_KEY or Bearer <user JWT>.
    Payload: shift_id, task_template_ids: [uuid1, uuid2]
    """
    try:
        restaurant, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response({'error': err['error']}, status=err['status'])
        data = request.data if isinstance(getattr(request, 'data', None), dict) else {}
        shift_id = data.get('shift_id') or data.get('shiftId')
        if not shift_id:
            return Response({'error': 'Missing required field: shift_id'}, status=status.HTTP_400_BAD_REQUEST)
        ids_raw = data.get('task_template_ids') or data.get('taskTemplateIds') or []
        if isinstance(ids_raw, str):
            ids_raw = [x.strip() for x in ids_raw.split(',') if x.strip()]
        task_template_ids = [str(x).strip() for x in ids_raw if x]
        if not task_template_ids:
            return Response({'error': 'Missing required field: task_template_ids (array of template UUIDs)'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            shift = AssignedShift.objects.get(
                id=shift_id,
                schedule__restaurant=restaurant
            )
        except AssignedShift.DoesNotExist:
            return Response({'error': 'Shift not found'}, status=status.HTTP_404_NOT_FOUND)
        templates = list(TaskTemplate.objects.filter(
            id__in=task_template_ids,
            restaurant=restaurant,
            is_active=True
        ))
        if not templates:
            return Response({'error': 'No valid task templates found for the given IDs'}, status=status.HTTP_404_NOT_FOUND)
        shift.task_templates.add(*templates)
        staff = shift.staff or shift.staff_members.first()
        acting_user = None
        try:
            _, acting_user = _try_jwt_restaurant_and_user(request)
        except Exception:
            pass
        from core.i18n import get_effective_language, normalize_language
        lang = normalize_language(get_effective_language(user=staff, restaurant=restaurant) or 'en') if staff else 'en'
        created_shift_tasks = 0
        created_executions = 0
        for tpl in templates:
            created_shift_tasks += instantiate_shift_tasks_from_template(
                shift=shift,
                assignee=staff,
                task_template=tpl,
                created_by=acting_user,
                language=lang,
            )
            ct = ensure_checklist_for_task_template(
                restaurant=restaurant,
                task_template=tpl,
                created_by=acting_user,
                language=lang,
            )
            if ct and staff:
                created_executions += ensure_checklist_execution_for_shift(
                    checklist_template=ct,
                    assignee=staff,
                    shift=shift,
                )
        return Response({
            'success': True,
            'shift_id': str(shift.id),
            'attached_templates': [{'id': str(t.id), 'name': t.name} for t in templates],
            'created_shift_tasks': created_shift_tasks,
            'created_checklist_executions': created_executions,
            'message': f"Attached {len(templates)} template(s) to shift. Created {created_shift_tasks} task(s).",
        })
    except Exception as e:
        logger.exception("Agent attach templates to shift error")
        err = str(e).strip()[:200] if e else "Unable to attach templates"
        return Response({'error': err}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_create_shift(request):
    """
    Create a single shift for a staff member.
    Used by the Lua agent (Miya) to schedule staff.
    For recurring shifts on specific days until an end date (e.g. "Mon–Sat until June 30"),
    use POST /api/scheduling/agent/create-recurring-shifts/ instead.

    Expected payload:
    {
        "restaurant_id": "uuid",
        "staff_id": "uuid",
        "shift_date": "YYYY-MM-DD",
        "start_time": "HH:MM",
        "end_time": "HH:MM",
        "role": "SERVER",  # optional
        "notes": "optional notes",
        "workspace_location": "Kitchen",  # optional
        "task_template_ids": ["uuid1", "uuid2"]  # optional: assign specific task/process templates
    }
    """
    try:
        # Resolve restaurant: try JWT first (Miya/Lua can send user token as Bearer)
        restaurant, acting_user = _try_jwt_restaurant_and_user(request)
        if not restaurant:
            is_valid, error = validate_agent_key(request)
            if not is_valid:
                return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
            payload = _agent_payload_from_request(request)
            restaurant_id = (
                payload.get('restaurant_id') or payload.get('restaurantId')
                or request.META.get('HTTP_X_RESTAURANT_ID')
            )
            if isinstance(restaurant_id, (list, tuple)) and restaurant_id:
                restaurant_id = restaurant_id[0]
            restaurant = None
            acting_user = None
            if restaurant_id:
                try:
                    restaurant = Restaurant.objects.get(id=restaurant_id)
                except Restaurant.DoesNotExist:
                    restaurant = None
            if not restaurant:
                restaurant, acting_user = resolve_agent_restaurant_and_user(request=request, payload=payload)
        
        data = request.data if isinstance(getattr(request, 'data', None), dict) else {}
        payload = _agent_payload_from_request(request)
        def _get_val(d, *keys):
            for k in keys:
                v = d.get(k)
                if v is not None and v != '':
                    return v[0] if isinstance(v, (list, tuple)) and v else v
            return None
        staff_id = _get_val(data, 'staff_id', 'staffId') or _get_val(payload, 'staff_id', 'staffId')
        shift_date_str = _get_val(data, 'shift_date', 'shiftDate') or _get_val(payload, 'shift_date', 'shiftDate')
        start_time_str = _get_val(data, 'start_time', 'startTime') or _get_val(payload, 'start_time', 'startTime')
        end_time_str = _get_val(data, 'end_time', 'endTime') or _get_val(payload, 'end_time', 'endTime')
        
        if not all([staff_id, shift_date_str, start_time_str, end_time_str]):
            return Response({
                'success': False,
                'error': 'Missing required fields: staff_id, shift_date, start_time, end_time'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if not restaurant:
            return Response({
                'success': False,
                'error': 'Unable to resolve restaurant context (provide restaurant_id or include sessionId/userId/email/phone/token).'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Each shift must have at least one Process & Task Template or one Custom Task
        task_template_ids_raw = data.get('task_template_ids') or data.get('taskTemplateIds') or []
        if isinstance(task_template_ids_raw, str):
            task_template_ids_raw = [x.strip() for x in task_template_ids_raw.split(',') if x.strip()]
        custom_tasks = data.get('tasks') or []
        if isinstance(custom_tasks, str):
            try:
                import json
                custom_tasks = json.loads(custom_tasks) if custom_tasks.strip() else []
            except Exception:
                custom_tasks = []
        if not task_template_ids_raw and not custom_tasks:
            return Response({
                'success': False,
                'error': 'Each shift must have at least one Process & Task Template (task_template_ids) or at least one Custom Task (tasks array with title).'
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

        # Attach tasks/processes: explicit task_template_ids (manager/Miya request) OR custom tasks OR auto-attach
        attach_result = None
        task_template_ids_raw = data.get('task_template_ids') or data.get('taskTemplateIds') or []
        if isinstance(task_template_ids_raw, str):
            task_template_ids_raw = [x.strip() for x in task_template_ids_raw.split(',') if x.strip()]
        task_template_ids = [str(x).strip() for x in task_template_ids_raw if x]
        custom_tasks_payload = data.get('tasks') or []
        if isinstance(custom_tasks_payload, str):
            try:
                import json
                custom_tasks_payload = json.loads(custom_tasks_payload) if custom_tasks_payload.strip() else []
            except Exception:
                custom_tasks_payload = []

        try:
            if task_template_ids:
                # Explicit assignment: Miya/manager requested specific task templates or processes
                templates = list(TaskTemplate.objects.filter(
                    id__in=task_template_ids,
                    restaurant=restaurant,
                    is_active=True
                ))
                if templates:
                    shift.task_templates.add(*templates)
                    from core.i18n import get_effective_language, normalize_language
                    lang = normalize_language(get_effective_language(user=staff, restaurant=restaurant) or 'en')
                    created_shift_tasks = 0
                    created_executions = 0
                    for tpl in templates:
                        created_shift_tasks += instantiate_shift_tasks_from_template(
                            shift=shift,
                            assignee=staff,
                            task_template=tpl,
                            created_by=acting_user,
                            language=lang,
                        )
                        ct = ensure_checklist_for_task_template(
                            restaurant=restaurant,
                            task_template=tpl,
                            created_by=acting_user,
                            language=lang,
                        )
                        if ct:
                            created_executions += ensure_checklist_execution_for_shift(
                                checklist_template=ct,
                                assignee=staff,
                                shift=shift,
                            )
                    attach_result = AutoAttachResult(
                        shift_context=detect_shift_context(
                            shift_title=shift_title,
                            shift_notes=shift_notes,
                            start_dt=start_datetime,
                            end_dt=end_datetime,
                        ),
                        used_templates=templates,
                        created_shift_tasks=created_shift_tasks,
                        created_checklist_executions=created_executions,
                        used_fallback_custom_template=False,
                    )
            if not attach_result:
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

        # Create custom tasks if provided (each shift must have templates or custom tasks)
        for t in custom_tasks_payload:
            if not isinstance(t, dict):
                continue
            title = (t.get('title') or t.get('name') or '').strip()
            if not title:
                continue
            priority = (t.get('priority') or 'MEDIUM').upper()
            if priority not in ('LOW', 'MEDIUM', 'HIGH', 'URGENT'):
                priority = 'MEDIUM'
            try:
                ShiftTask.objects.create(
                    shift=shift,
                    title=title[:255],
                    description=(t.get('description') or '')[:1000],
                    priority=priority,
                    status='TODO',
                    assigned_to=staff,
                    created_by=acting_user,
                )
            except Exception as e:
                logger.warning(f"Agent create shift: failed to create custom task '{title[:50]}': {e}")

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


def _normalize_days_of_week(value):
    """Convert days_of_week to a set of Python weekdays (0=Monday, 6=Sunday)."""
    if value is None:
        return None
    day_names = {
        'monday': 0, 'mon': 0, 'tuesday': 1, 'tue': 1, 'wednesday': 2, 'wed': 2,
        'thursday': 3, 'thu': 3, 'friday': 4, 'fri': 4, 'saturday': 5, 'sat': 5,
        'sunday': 6, 'sun': 6,
    }
    out = set()
    if isinstance(value, (list, tuple)):
        for v in value:
            if isinstance(v, int) and 0 <= v <= 6:
                out.add(v)
            elif isinstance(v, str):
                out.add(day_names.get(v.strip().lower(), -1))
        out.discard(-1)
    elif isinstance(value, str):
        for part in value.replace(',', ' ').split():
            part = part.strip().lower()
            if part in day_names:
                out.add(day_names[part])
    return out if out else None


@api_view(['POST'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_create_recurring_shifts(request):
    """
    Create recurring shifts for a staff member on specified days of the week until an end date.
    Used by Miya to support instructions like "every day from Monday to Saturday until June 30".

    Same payload as create-shift, plus:
    - start_date: YYYY-MM-DD (first occurrence)
    - end_date: YYYY-MM-DD (last date, e.g. 2026-06-30)
    - days_of_week: list of weekdays to repeat. Options:
      - Integers 0-6 (Monday=0, Sunday=6), e.g. [0,1,2,3,4,5] for Mon-Sat
      - Names: ["monday","tuesday",...,"saturday"] or "monday,tuesday,wednesday,thursday,friday,saturday"
    """
    try:
        restaurant, acting_user = _try_jwt_restaurant_and_user(request)
        if not restaurant:
            is_valid, error = validate_agent_key(request)
            if not is_valid:
                return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
            payload = _agent_payload_from_request(request)
            restaurant_id = (
                payload.get('restaurant_id') or payload.get('restaurantId')
                or request.META.get('HTTP_X_RESTAURANT_ID')
            )
            if isinstance(restaurant_id, (list, tuple)) and restaurant_id:
                restaurant_id = restaurant_id[0]
            restaurant = None
            acting_user = None
            if restaurant_id:
                try:
                    restaurant = Restaurant.objects.get(id=restaurant_id)
                except Restaurant.DoesNotExist:
                    restaurant = None
            if not restaurant:
                restaurant, acting_user = resolve_agent_restaurant_and_user(request=request, payload=payload)

        data = request.data if isinstance(getattr(request, 'data', None), dict) else {}
        payload = _agent_payload_from_request(request)

        def _get_val(d, *keys):
            for k in keys:
                v = d.get(k)
                if v is not None and v != '':
                    return v[0] if isinstance(v, (list, tuple)) and v else v
            return None

        staff_id = _get_val(data, 'staff_id', 'staffId') or _get_val(payload, 'staff_id', 'staffId')
        start_date_str = _get_val(data, 'start_date', 'startDate')
        end_date_str = _get_val(data, 'end_date', 'endDate')
        frequency_raw = (data.get('frequency') or payload.get('frequency') or '').upper()
        frequency = frequency_raw if frequency_raw in ('DAILY', 'WEEKLY', 'MONTHLY') else None
        days_of_week_raw = data.get('days_of_week') or data.get('daysOfWeek') or payload.get('days_of_week') or payload.get('daysOfWeek')
        start_time_str = _get_val(data, 'start_time', 'startTime') or _get_val(payload, 'start_time', 'startTime')
        end_time_str = _get_val(data, 'end_time', 'endTime') or _get_val(payload, 'end_time', 'endTime')

        if not all([staff_id, start_date_str, end_date_str, start_time_str, end_time_str]):
            return Response({
                'success': False,
                'error': 'Missing required fields: staff_id, start_date, end_date, start_time, end_time'
            }, status=status.HTTP_400_BAD_REQUEST)

        days_of_week = _normalize_days_of_week(days_of_week_raw)
        if not days_of_week and not frequency:
            return Response({
                'success': False,
                'error': 'Either days_of_week or frequency (DAILY, WEEKLY, MONTHLY) is required. Use days_of_week for custom days (e.g. [0,2,4] for Mon/Wed/Fri).'
            }, status=status.HTTP_400_BAD_REQUEST)

        if not restaurant:
            return Response({
                'success': False,
                'error': 'Unable to resolve restaurant context.'
            }, status=status.HTTP_400_BAD_REQUEST)

        task_template_ids_raw = data.get('task_template_ids') or data.get('taskTemplateIds') or []
        if isinstance(task_template_ids_raw, str):
            task_template_ids_raw = [x.strip() for x in task_template_ids_raw.split(',') if x.strip()]
        custom_tasks = data.get('tasks') or []
        if isinstance(custom_tasks, str):
            try:
                import json
                custom_tasks = json.loads(custom_tasks) if custom_tasks.strip() else []
            except Exception:
                custom_tasks = []
        if not task_template_ids_raw and not custom_tasks:
            return Response({
                'success': False,
                'error': 'Each shift must have at least one task_template_ids or tasks array.'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            staff = CustomUser.objects.get(id=staff_id, restaurant=restaurant)
        except CustomUser.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Staff member not found in this restaurant'
            }, status=status.HTTP_404_NOT_FOUND)

        try:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({
                'success': False,
                'error': 'Invalid start_date or end_date. Use YYYY-MM-DD'
            }, status=status.HTTP_400_BAD_REQUEST)
        if start_date > end_date:
            return Response({
                'success': False,
                'error': 'start_date must be on or before end_date'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
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

        role = data.get('role') or staff.role or 'SERVER'
        department = data.get('department') or None
        workspace_location = data.get('workspace_location') or data.get('workspaceLocation') or None
        shift_notes = data.get('notes', '') or ''
        shift_title = data.get('shift_title') or data.get('shiftTitle') or data.get('title') or ''
        task_template_ids = [str(x).strip() for x in task_template_ids_raw if x]
        recurrence_group_id = uuid.uuid4()
        if not shift_title:
            start_datetime_ctx = timezone.datetime.combine(start_date, start_time)
            end_datetime_ctx = timezone.datetime.combine(start_date, end_time)
            if timezone.is_naive(start_datetime_ctx):
                start_datetime_ctx = timezone.make_aware(start_datetime_ctx)
            if timezone.is_naive(end_datetime_ctx):
                end_datetime_ctx = timezone.make_aware(end_datetime_ctx)
            inferred = detect_shift_context(
                shift_title=None,
                shift_notes=shift_notes,
                start_dt=start_datetime_ctx,
                end_dt=end_datetime_ctx,
                restaurant=restaurant,
            )
            shift_title = generate_shift_title(
                shift_context=inferred,
                staff_role=role.upper(),
                department=department,
                workspace_location=workspace_location,
            )
        # Same semantics as dashboard: frequency (DAILY/WEEKLY/MONTHLY) or custom days_of_week
        if days_of_week:
            date_iter = _dates_for_days_of_week(start_date, end_date, list(days_of_week))
        elif frequency:
            date_iter = _dates_for_frequency(start_date, end_date, frequency)
        else:
            date_iter = iter([])
        created_shifts = []
        skipped_conflicts = []
        for shift_date in date_iter:
            days_since_monday = shift_date.weekday()
            week_start = shift_date - timedelta(days=days_since_monday)
            week_end = week_start + timedelta(days=6)
            schedule, _ = WeeklySchedule.objects.get_or_create(
                restaurant=restaurant,
                week_start=week_start,
                defaults={'week_end': week_end}
            )
            start_datetime = timezone.datetime.combine(shift_date, start_time)
            end_datetime = timezone.datetime.combine(shift_date, end_time)
            if timezone.is_naive(start_datetime):
                start_datetime = timezone.make_aware(start_datetime)
            if timezone.is_naive(end_datetime):
                end_datetime = timezone.make_aware(end_datetime)

            conflicts = SchedulingService.detect_scheduling_conflicts(
                str(staff.id),
                shift_date,
                start_time,
                end_time,
                workspace_location=workspace_location
            )
            if conflicts:
                skipped_conflicts.append({'date': str(shift_date), 'message': conflicts[0].get('message', 'Conflict')})
                continue

            shift = AssignedShift.objects.create(
                schedule=schedule,
                staff=staff,
                shift_date=shift_date,
                start_time=start_datetime,
                end_time=end_datetime,
                role=role.upper(),
                notes=shift_title,
                preparation_instructions=shift_notes,
                department=department,
                workspace_location=workspace_location,
                status='SCHEDULED',
                created_by=acting_user,
                last_modified_by=acting_user,
                is_recurring=True,
                recurrence_group_id=recurrence_group_id,
            )
            shift.staff_members.add(staff)
            try:
                SchedulingService.ensure_shift_color(shift)
            except Exception:
                pass

            if task_template_ids:
                templates = list(TaskTemplate.objects.filter(
                    id__in=task_template_ids,
                    restaurant=restaurant,
                    is_active=True
                ))
                if templates:
                    shift.task_templates.add(*templates)
                    from core.i18n import get_effective_language, normalize_language
                    lang = normalize_language(get_effective_language(user=staff, restaurant=restaurant) or 'en')
                    for tpl in templates:
                        instantiate_shift_tasks_from_template(
                            shift=shift,
                            assignee=staff,
                            task_template=tpl,
                            created_by=acting_user,
                            language=lang,
                        )
                        ct = ensure_checklist_for_task_template(
                            restaurant=restaurant,
                            task_template=tpl,
                            created_by=acting_user,
                            language=lang,
                        )
                        if ct:
                            ensure_checklist_execution_for_shift(
                                checklist_template=ct,
                                assignee=staff,
                                shift=shift,
                            )
            else:
                try:
                    auto_attach_templates_and_tasks(
                        shift=shift,
                        restaurant=restaurant,
                        assignee=staff,
                        staff_role=role.upper(),
                        shift_title=shift_title,
                        instructions=shift_notes,
                        created_by=acting_user,
                    )
                except Exception as e:
                    logger.warning(f"Agent create recurring: auto-attach failed for {shift_date}: {e}")

            for t in custom_tasks:
                if not isinstance(t, dict):
                    continue
                title = (t.get('title') or t.get('name') or '').strip()
                if not title:
                    continue
                priority = (t.get('priority') or 'MEDIUM').upper()
                if priority not in ('LOW', 'MEDIUM', 'HIGH', 'URGENT'):
                    priority = 'MEDIUM'
                try:
                    ShiftTask.objects.create(
                        shift=shift,
                        title=title[:255],
                        description=(t.get('description') or '')[:1000],
                        priority=priority,
                        status='TODO',
                        assigned_to=staff,
                        created_by=acting_user,
                    )
                except Exception as e:
                    logger.warning(f"Agent create recurring: custom task failed: {e}")

            try:
                SchedulingService.notify_shift_assignment(shift, force_whatsapp=True)
            except Exception as e:
                logger.warning(f"Agent create recurring: notify failed for {shift_date}: {e}")

            created_shifts.append({
                'id': str(shift.id),
                'shift_date': str(shift_date),
                'start_time': start_time_str,
                'end_time': end_time_str,
            })

        return Response({
            'success': True,
            'created': len(created_shifts),
            'shifts': created_shifts,
            'skipped_conflicts': skipped_conflicts,
            'recurrence_group_id': str(recurrence_group_id),
            'message': f"Created {len(created_shifts)} recurring shift(s) for {staff.first_name} from {start_date} to {end_date} on {_days_summary(days_of_week)}."
        }, status=status.HTTP_201_CREATED)

    except Exception as e:
        logger.exception("Agent create recurring shifts error")
        err = str(e).strip() if e else "Unknown error"
        if len(err) > 200:
            err = err[:197] + "..."
        return Response({
            'success': False,
            'error': f"Could not create recurring shifts: {err}"
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _days_summary(days_set):
    """Return a short label for the set of weekdays (0=Mon, 6=Sun)."""
    names = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
    return ', '.join(names[d] for d in sorted(days_set))


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
    Auth: Bearer user JWT or Bearer LUA_WEBHOOK_API_KEY; context via body or X-Restaurant-Id.
    """
    try:
        # Resolve restaurant: try JWT first (dashboard token)
        restaurant, _ = _try_jwt_restaurant_and_user(request)
        if not restaurant:
            is_valid, error = validate_agent_key(request)
            if not is_valid:
                return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
            payload = _agent_payload_from_request(request)
            restaurant_id = (
                payload.get('restaurant_id') or payload.get('restaurantId')
                or request.META.get('HTTP_X_RESTAURANT_ID')
            )
            if isinstance(restaurant_id, (list, tuple)) and restaurant_id:
                restaurant_id = restaurant_id[0]
            restaurant = None
            if restaurant_id:
                try:
                    restaurant = Restaurant.objects.get(id=restaurant_id)
                except Restaurant.DoesNotExist:
                    restaurant = None
            if not restaurant:
                restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=payload)
        if not restaurant:
            return Response({
                'success': False,
                'error': 'Unable to resolve restaurant context (provide restaurant_id or include sessionId/userId/email/phone/token).'
            }, status=status.HTTP_400_BAD_REQUEST)

        data = request.data if isinstance(getattr(request, 'data', None), dict) else {}
        payload = _agent_payload_from_request(request)
        def _get_val(d, *keys):
            for k in keys:
                v = d.get(k)
                if v is not None and v != '':
                    return v[0] if isinstance(v, (list, tuple)) and v else v
            return None
        week_start = _get_val(data, 'week_start') or _get_val(payload, 'week_start')
        department = _get_val(data, 'department') or _get_val(payload, 'department')
        if not week_start:
            return Response({
                'success': False,
                'error': 'Missing required field: week_start (YYYY-MM-DD)'
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
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_restaurant_search(request):
    """
    Search restaurants by name (agent key only). Lets Miya resolve "Barometre" / "Mizan Mistro"
    when the user has no session token (e.g. lua chat from CLI).
    """
    try:
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'error': error}, status=status.HTTP_401_UNAUTHORIZED)
        name = (request.GET.get('name') or request.GET.get('search') or '').strip()
        if not name:
            return Response({'error': 'Missing query parameter: name'}, status=status.HTTP_400_BAD_REQUEST)
        from django.db.models import Q
        qs = Restaurant.objects.filter(Q(name__icontains=name) | Q(email__icontains=name))[:20]
        results = [{'id': str(r.id), 'name': r.name} for r in qs]
        return Response({'results': results, 'count': len(results)})
    except Exception as e:
        logger.exception("Agent restaurant search error")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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
        restaurant = None
        explicit_rid = _explicit_restaurant_id_from_request(request)
        if explicit_rid:
            rid = explicit_rid[0] if isinstance(explicit_rid, (list, tuple)) and explicit_rid else explicit_rid
            if rid and isinstance(rid, str) and rid.strip():
                try:
                    restaurant = Restaurant.objects.get(id=rid.strip())
                except (Restaurant.DoesNotExist, ValueError, TypeError):
                    pass
        if not restaurant:
            restaurant, _ = _try_jwt_restaurant_and_user(request)
        if not restaurant:
            is_valid, error = validate_agent_key(request)
            if not is_valid:
                return Response({'error': error}, status=status.HTTP_401_UNAUTHORIZED)
            restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=dict(request.query_params))
        if not restaurant:
            return Response(
                {'error': 'Unable to resolve restaurant context.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        queryset = AssignedShift.objects.filter(schedule__restaurant=restaurant)

        # Filters
        date_from = request.query_params.get('date_from')
        date_to = request.query_params.get('date_to')
        staff_id = request.query_params.get('staff_id') or request.query_params.get('staffId')
        staff_name = (request.query_params.get('staff_name') or request.query_params.get('name') or '').strip()
        role = request.query_params.get('role')

        # Resolve staff by name so Miya can ask "who is scheduled" / "does X have a shift" by name
        if not staff_id and staff_name and restaurant:
            staff_qs = CustomUser.objects.filter(
                restaurant=restaurant, is_active=True
            ).exclude(role='SUPER_ADMIN')
            name_lower = staff_name.lower()
            for u in staff_qs[:200]:
                full = f"{(u.first_name or '').strip()} {(u.last_name or '').strip()}".strip().lower()
                if full == name_lower or name_lower in full:
                    staff_id = str(u.id)
                    break
            if not staff_id and ' ' in staff_name:
                first, last = staff_name.split(' ', 1)
                match = staff_qs.filter(
                    first_name__icontains=first.strip(),
                    last_name__icontains=last.strip()
                ).first()
                if match:
                    staff_id = str(match.id)

        if date_from:
            queryset = queryset.filter(shift_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(shift_date__lte=date_to)
        # Include shifts where staff is primary OR in staff_members (same source as dashboard & get_my_shifts)
        if staff_id:
            queryset = queryset.filter(
                Q(staff_id=staff_id) | Q(staff_members__id=staff_id)
            ).distinct()
        if role:
            queryset = queryset.filter(role=role)

        queryset = queryset.select_related('staff').prefetch_related('staff_members').order_by('shift_date', 'start_time')

        serializer = AssignedShiftSerializer(queryset, many=True)
        return Response(serializer.data)

    except Exception as e:
        logger.exception("Agent list shifts error")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# --- Agent Memory (Miya context persistence, corrections, explainability) ---

@api_view(['GET', 'POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_memory_list_or_save(request):
    """
    GET: List memories for the restaurant (optional filter: memory_type, key).
    POST: Save a memory (key, value, memory_type, scope optional).
    Auth: Bearer LUA_WEBHOOK_API_KEY or Bearer <user JWT>.
    """
    try:
        restaurant, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response({'error': err['error']}, status=err['status'])

        if request.method == 'GET':
            qs = AgentMemory.objects.filter(restaurant=restaurant)
            memory_type = request.query_params.get('memory_type')
            key = request.query_params.get('key')
            if memory_type:
                qs = qs.filter(memory_type=memory_type)
            if key:
                qs = qs.filter(key__icontains=key)
            qs = qs.order_by('-created_at')[:100]
            items = [
                {
                    'id': str(m.id),
                    'memory_type': m.memory_type,
                    'key': m.key,
                    'value': m.value,
                    'scope': m.scope or '',
                    'created_at': m.created_at.isoformat() if m.created_at else None,
                }
                for m in qs
            ]
            return Response({'memories': items, 'restaurant_id': str(restaurant.id)})

        # POST
        data = request.data if isinstance(getattr(request, 'data', None), dict) else {}
        key = (data.get('key') or '').strip()
        value = (data.get('value') or '').strip()
        memory_type = (data.get('memory_type') or 'fact').lower()
        if memory_type not in ('preference', 'correction', 'fact', 'pattern'):
            memory_type = 'fact'
        if not key:
            return Response({'error': 'Missing required field: key'}, status=status.HTTP_400_BAD_REQUEST)
        if not value:
            return Response({'error': 'Missing required field: value'}, status=status.HTTP_400_BAD_REQUEST)
        scope = (data.get('scope') or '').strip()[:64]
        acting_user = None
        try:
            _, acting_user = _try_jwt_restaurant_and_user(request)
        except Exception:
            pass
        memory = AgentMemory.objects.create(
            restaurant=restaurant,
            memory_type=memory_type,
            key=key,
            value=value,
            scope=scope,
            created_by=acting_user,
        )
        return Response({
            'success': True,
            'memory': {
                'id': str(memory.id),
                'memory_type': memory.memory_type,
                'key': memory.key,
                'value': memory.value,
                'scope': memory.scope or '',
            },
            'message': f"Remembered: {memory.key}",
        }, status=status.HTTP_201_CREATED)
    except Exception as e:
        logger.exception("Agent memory list/save error")
        return Response({'error': str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST', 'DELETE'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_memory_delete(request):
    """
    Delete a memory by id or by key.
    POST/DELETE body: memory_id (uuid) or key (string). Auth: agent key or JWT.
    """
    try:
        restaurant, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response({'error': err['error']}, status=err['status'])
        data = request.data if isinstance(getattr(request, 'data', None), dict) else {}
        memory_id = data.get('memory_id') or data.get('id')
        key = (data.get('key') or '').strip()
        if memory_id:
            deleted = AgentMemory.objects.filter(
                id=memory_id, restaurant=restaurant
            ).delete()
        elif key:
            deleted = AgentMemory.objects.filter(
                restaurant=restaurant, key=key
            ).delete()
        else:
            return Response({'error': 'Provide memory_id or key'}, status=status.HTTP_400_BAD_REQUEST)
        count = deleted[0] if isinstance(deleted, tuple) else (1 if deleted else 0)
        return Response({'success': True, 'deleted': count, 'message': f"Removed {count} memory(ies)."})
    except Exception as e:
        logger.exception("Agent memory delete error")
        return Response({'error': str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_proactive_insights(request):
    """
    Proactive intelligence: no-shows today, understaffed shifts, late patterns, staffing suggestions.
    Used by Miya to surface alerts and recommendations without being asked.
    Auth: Bearer LUA_WEBHOOK_API_KEY or Bearer <user JWT>.
    Query: restaurant_id (or X-Restaurant-Id), date (optional, default today).
    """
    try:
        restaurant, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response({'error': err['error']}, status=err['status'])

        date_str = request.query_params.get('date') or timezone.now().date().isoformat()
        try:
            target_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            target_date = timezone.now().date()

        insights = []
        no_shows = []
        understaffed = []
        late_patterns = []
        suggestions = []

        # No-shows: shifts with status NO_SHOW or SCHEDULED/CONFIRMED where clock-in never happened
        from timeclock.models import ClockEvent
        today_start = timezone.datetime.combine(target_date, time(0, 0))
        if timezone.is_naive(today_start):
            today_start = timezone.make_aware(today_start)
        today_end = today_start + timedelta(days=1)
        shifts_today = AssignedShift.objects.filter(
            schedule__restaurant=restaurant,
            shift_date=target_date,
            status__in=['SCHEDULED', 'CONFIRMED', 'NO_SHOW']
        ).select_related('staff')
        staff_clock_ins = {}
        if shifts_today.exists():
            clock_ins = ClockEvent.objects.filter(
                staff__restaurant=restaurant,
                event_type='in',
                timestamp__gte=today_start,
                timestamp__lt=today_end
            ).values_list('staff_id', 'timestamp')
            staff_clock_ins = {str(sid): ts for sid, ts in clock_ins}
        for shift in shifts_today:
            staff = shift.staff
            if not staff:
                continue
            clocked = str(staff.id) in staff_clock_ins
            if not clocked and shift.status == 'NO_SHOW':
                no_shows.append({
                    'shift_id': str(shift.id),
                    'staff_name': f"{staff.first_name} {staff.last_name}",
                    'role': shift.role or '',
                })
            elif not clocked and shift.status in ('SCHEDULED', 'CONFIRMED'):
                start_dt = shift.start_time
                if start_dt and timezone.now() > start_dt:
                    no_shows.append({
                        'shift_id': str(shift.id),
                        'staff_name': f"{staff.first_name} {staff.last_name}",
                        'role': shift.role or '',
                        'expected_start': start_dt.strftime('%H:%M') if hasattr(start_dt, 'strftime') else str(start_dt)[:5],
                    })

        if no_shows:
            insights.append({
                'type': 'no_shows',
                'priority': 'high',
                'title': 'No-shows or missing clock-ins',
                'items': no_shows,
                'summary': f"{len(no_shows)} staff expected today have not clocked in.",
            })

        # Understaffed: compare today's shifts to a simple baseline (e.g. roles count)
        role_counts = {}
        for shift in shifts_today:
            r = (shift.role or 'STAFF').upper()
            role_counts[r] = role_counts.get(r, 0) + 1
        if role_counts:
            # Simple heuristic: if only 1 server for dinner, flag
            if role_counts.get('SERVER', 0) < 2 and target_date == timezone.now().date():
                understaffed.append({
                    'reason': 'Few servers scheduled today',
                    'role': 'SERVER',
                    'current': role_counts.get('SERVER', 0),
                })
            if understaffed:
                insights.append({
                    'type': 'understaffed',
                    'priority': 'medium',
                    'title': 'Understaffing risk',
                    'items': understaffed,
                    'summary': 'Consider adding coverage for peak hours.',
                })

        # Late patterns: staff who clocked in after shift start
        for shift in shifts_today:
            staff = shift.staff
            if not staff or not shift.start_time:
                continue
            cin = staff_clock_ins.get(str(staff.id))
            if cin and shift.start_time and cin > shift.start_time:
                delta = cin - (shift.start_time if timezone.is_aware(shift.start_time) else timezone.make_aware(shift.start_time))
                mins = int(delta.total_seconds() / 60)
                if mins > 5:
                    late_patterns.append({
                        'staff_name': f"{staff.first_name} {staff.last_name}",
                        'lateness_minutes': mins,
                    })
        if late_patterns:
            insights.append({
                'type': 'late_patterns',
                'priority': 'low',
                'title': 'Late clock-ins today',
                'items': late_patterns[:5],
                'summary': f"{len(late_patterns)} staff clocked in late.",
            })

        # Staffing suggestions from operational advice
        try:
            from .ai_scheduler import AIScheduler
            scheduler = AIScheduler(restaurant)
            demand = scheduler._get_demand_forecast(target_date)
            day_name = target_date.strftime('%A')
            current_demand = demand.get(day_name, 'MEDIUM')
            if current_demand == 'HIGH':
                suggestions.append('High demand expected today; ensure peak-hour coverage (lunch 12–15, dinner 19–23).')
            if suggestions:
                insights.append({
                    'type': 'suggestions',
                    'priority': 'low',
                    'title': 'Staffing suggestions',
                    'items': suggestions,
                    'summary': '; '.join(suggestions),
                })
        except Exception:
            pass

        return Response({
            'restaurant_id': str(restaurant.id),
            'date': target_date.isoformat(),
            'insights': insights,
            'has_alerts': any(i.get('priority') == 'high' for i in insights),
        })
    except Exception as e:
        logger.exception("Agent proactive insights error")
        return Response({'error': str(e)[:200]}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
