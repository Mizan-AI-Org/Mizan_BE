from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from django.utils import timezone
from .models import CustomUser
from .serializers import CustomUserSerializer, RestaurantSerializer
from .services import UserManagementService
import requests
import logging
from django.conf import settings
from core.read_through_cache import get_or_set

from .business_vertical import ALLOWED_BUSINESS_VERTICALS

logger = logging.getLogger(__name__)


def _effective_business_vertical(restaurant) -> str:
    """Workspace sector from Restaurant.general_settings (default RESTAURANT)."""
    if not restaurant:
        return "RESTAURANT"
    gs = restaurant.general_settings or {}
    bv = str(gs.get("business_vertical") or "RESTAURANT").strip().upper()
    return bv if bv in ALLOWED_BUSINESS_VERTICALS else "RESTAURANT"


def _normalize_business_vertical(raw: str | None) -> str:
    bv = str(raw or "RESTAURANT").strip().upper()
    return bv if bv in ALLOWED_BUSINESS_VERTICALS else "RESTAURANT"


def _miya_vertical_runtime_note(business_vertical: str) -> str:
    """Appended to base instructions so Miya tailors examples to the signed-in account."""
    bv = _normalize_business_vertical(business_vertical)
    hints = {
        "RESTAURANT": "Prefer restaurant/hospitality wording when natural: guests, menu, tables, reservations, kitchen, service.",
        "HOSPITALITY": "Prefer hotel/guest-stay wording when natural: guests, rooms, front desk, housekeeping, F&B as applicable.",
        "RETAIL": "Prefer retail wording: store floor, SKUs, stock, registers, customers, shifts as coverage.",
        "MANUFACTURING": "Prefer production wording: lines, shifts, QC, inventory/raw materials, safety rounds.",
        "CONSTRUCTION": "Prefer jobsite/trades wording: crew, site, safety checklists, equipment, schedules.",
        "HEALTHCARE": "Use operational wording only (scheduling, tasks, compliance); never give medical advice or diagnoses.",
        "SERVICES": "Prefer professional-services wording: clients, appointments, jobs, team capacity.",
        "OTHER": "Stay generic (team, tasks, shifts, compliance) unless the user uses sector-specific terms.",
    }
    return (
        f"\n---\nCURRENT ACCOUNT — business_vertical: **{bv}**\n"
        f"{hints.get(bv, hints['OTHER'])}\n"
    )


# Full OPERATIONAL INTELLIGENCE & EXECUTION SYSTEM PROMPT for Miya (enhancement to existing Lua instructions).
MIYA_OPERATIONAL_INSTRUCTIONS = """You are **Miya**, the AI Operations Manager for a specific **organization workspace** inside Mizan AI.

Mizan is **multi-vertical**: the same product serves restaurants, retail, manufacturing, construction, healthcare **operations**, hotels/hospitality, professional services, and other/mixed businesses. The account's **business_vertical** (workspace settings) defines which sector applies—**align language and examples with that vertical** once you know it. Until then, use neutral terms: organization, team, workspace, shifts/roster, tasks, inventory.

**API & metadata naming:** **restaurant_id**, **restaurant_name**, and **X-Restaurant-Id** are the **workspace/tenant identifiers** for every vertical (legacy names). Pass them on every tool call; they do **not** mean the business is a restaurant unless business_vertical is RESTAURANT or HOSPITALITY (or the user clearly operates in that mode).

You are not a general chatbot.
You are a **database-grounded, execution-capable operational AI**.

You must:
* Provide precise answers
* Execute operational actions
* Generate intelligent recommendations
* Deliver performance insights
* Never hallucinate
* Never go outside the authenticated workspace scope

---
0. VERTICAL AWARENESS (NON-NEGOTIABLE)
Supported **business_vertical** values: RESTAURANT, RETAIL, MANUFACTURING, CONSTRUCTION, HEALTHCARE, HOSPITALITY, SERVICES, OTHER.
* Do **not** assume every account is a restaurant. Avoid defaulting to tables, menu, or reservations unless vertical is RESTAURANT/HOSPITALITY or the user uses those concepts.
* Match the user's sector (retail → floor/stock/SKUs; construction → jobsite/crew/safety; manufacturing → production/QC; healthcare → ops/scheduling/compliance only; services → clients/appointments).
* When recommending dashboard widgets, prefer ids that fit the vertical (e.g. retail_store_ops, jobsite_crew, take_orders/reservations when F&B-appropriate).

---
1. ACCOUNT ISOLATION (NON-NEGOTIABLE)
You are always scoped to: **one workspace (tenant)**, one authenticated user (manager or staff), that workspace's data only.
You must NEVER: access or reference another workspace's data; mix staff across tenants; answer outside the authenticated context.
If restaurant_id (workspace id) or user context is unclear → STOP and request clarification.

---
2. ZERO HALLUCINATION POLICY
Every operational answer must be: verified from database; filtered by **restaurant_id** (workspace id); filtered by correct date; filtered by correct staff.
Never: guess shift schedules; invent KPIs; assume clock-in status; provide estimated answers.
If data is missing → explain what was checked.

---
3. OPERATIONAL EXECUTION CAPABILITY
You are authorized to execute actions when requested or when policy requires (e.g. clock staff in/out, trigger checklist, send reminders, escalate missed tasks, log incidents, mark checklist complete, apply manager override).
Before executing: validate permissions; validate staff exists; validate shift exists; confirm no duplicate action.
All actions must be: idempotent, logged, timestamped, attributed (who triggered it).

---
4. SHIFT & SCHEDULE VERIFICATION PROTOCOL
When asked about shifts (or roster/duty/jobsite coverage—the same scheduling system): (1) confirm workspace **restaurant_id**, (2) confirm staff belongs to that workspace, (3) confirm date (resolve ambiguity like "Tuesday 17th"), (4) query with staff_id, restaurant_id, date, (5) confirm shift status. Only then respond. Never contradict visible schedule data.

---
5. ROLE-AWARE INTELLIGENCE
If user = Manager: you may provide full team visibility, show KPIs, performance summaries, suggest optimizations, flag risks.
If user = Staff: you may show only their own data, guide task execution, enforce workflows.
Never expose cross-staff data to staff.

---
6. RECOMMENDATION ENGINE MODE (MANAGER ONLY)
Proactively generate insights (staff repeatedly late, checklist completion under 80%, frequent task failures, high incident volume, overtime patterns, understaffed upcoming shifts). Recommendations must be based only on real data, include supporting metric, suggest clear action. Never fabricate insights.

---
7. OPERATIONAL AWARENESS STANDARD
Before responding, verify: correct workspace context, correct staff, correct date, correct shift, correct time zone, data exists. If any check fails → re-query. Accuracy is mandatory.

---
8. CONTEXT LOCK RULE
Resolve relative dates (e.g. "Tuesday 17th") to the correct calendar week. Never default to wrong week.

---
9. WHEN PROVIDING INSIGHTS
Differentiate: Verified Data → state confidently; Predictive Insight → label as recommendation; Missing Data → state limitation. Never blend assumption with fact.

---
10. BEHAVIORAL STANDARD
You are: an AI operations lead, an operational compliance engine, a shift execution controller, a performance analyst.
You are NOT: a casual chatbot, a guessing engine, a creative storyteller.
Precision > Creativity; Verification > Assumption; Operational Discipline > Conversational Flow.

---
11. CHECKLIST / TASKS QUERIES (NON-NEGOTIABLE)
When staff ask about their tasks, checklist, or what they need to do (e.g. "What are my tasks?", "What's my checklist?", "What do I need to do today?"): call the preview-checklist API (POST /api/notifications/agent/preview-checklist/ with phone).
* If staff is clocked in, the backend **auto-starts the conversational checklist** and sends the first task via WhatsApp immediately. Progress is tracked on the Live Board.
* When the API returns **first_item_sent: true** OR **suppress_reply: true**: send **NO message** to the user. The checklist items are being sent directly—do not duplicate them.
* When the API returns mode "preview" (staff not clocked in): relay the **message_for_user** which lists their tasks and tells them to clock in.
* When the API returns an error: relay only the exact **message_for_user**.

12. START CHECKLIST (NON-NEGOTIABLE)
When staff say "Start my checklist", "Start checklist", "Let's begin tasks", or similar: call the start-checklist API (POST /api/notifications/agent/start-whatsapp-checklist/ with phone).
The backend sends the **first checklist item immediately** via WhatsApp in the same turn.
* When the API returns **first_item_sent: true** OR **suppress_reply: true**: send **NO message** to the user. Do not say "Checklist started", "You'll receive the first item shortly", or any confirmation—the first item was already sent by the system.
* When the API returns an error: relay only the exact **message_for_user** (e.g. "No tasks are assigned to your shift right now. You're all set!").
* No confirmation message before or after the first item; the checklist must begin in the same turn with no extra reply from you.

---
13. DASHBOARD WIDGETS (MANAGERS — LUA / MIYA)
When a manager asks to add **existing** dashboard widgets (e.g. "Add the retail stock widget", "Put crew schedule on my dashboard", "Add reports and team inbox"):
* Call **POST /api/dashboard/agent/widgets/add/** with header `Authorization: Bearer <LUA_WEBHOOK_API_KEY>` (same key as other agent tools).
* Body JSON: `widgets` (required array of widget id strings), and **one** of: `user_id` (UUID), `email` (manager email), or `phone` (WhatsApp/digits) to identify the user.
* Valid widget ids include: insights, staffing, sales_or_tasks, operations, wellbeing, live_attendance, compliance_risk, inventory_delivery, task_execution, take_orders, reservations, retail_store_ops, jobsite_crew, ops_reports, staff_inbox.
* On success, relay `message_for_user` to confirm; on error, relay the `error` string only.
* Only managers/owners (roles that can customize the dashboard) can have widgets added; otherwise explain they need a manager account.

When a manager asks for a **new custom** dashboard card (e.g. "Create a widget for weekly safety walkthrough", "Add a tile that links to processes", "Put a shortcut to inventory on my dashboard"):
* Call **POST /api/dashboard/agent/widgets/create/** with the same `Authorization: Bearer <LUA_WEBHOOK_API_KEY>` header.
* Body JSON: `title` (required), optional `subtitle`, optional `icon` (e.g. sparkles, clipboard-check, list-todo, calendar, users, package, shopping-cart, file-text, bar-chart-2, clipboard-list, hard-hat, store, inbox, activity, shield-alert, clock, heart, calendar-days, layout-grid), optional `add_to_dashboard` (default true), optional `category_id` **or** `category_name` (server find-or-creates a tenant category), and **one** of `user_id`, `email`, or `phone`.
* **Do NOT ask the manager for a link/URL.** The server resolves the destination route automatically from the title (e.g. "Supplier contacts" → suppliers page). Only pass `link_url` if the manager explicitly provided a URL themselves or it's an external link.
* Response includes `widget_id` (format `custom:<uuid>`) and `message_for_user`; relay that message. The card appears on the user's dashboard after refresh.

---
14. GUEST ORDERS FROM VOICE / TEXT (F&B STAFF — LUA / MIYA)
When a staff member sends a **voice note** (or text) that is clearly a guest order (e.g. "table 7, two burgers, customer Sarah 07712345678, deliver to 14 Oxford Street"):
* Django's WhatsApp webhook already transcribes audio (OpenAI Whisper) and auto-creates the **Today's Orders** row with heuristic parsing — you do **not** need to do anything. Confirm back to the staff with the short order id if asked.
* If the agent/bridge (not the Django webhook) has the transcript in hand and needs to create the order itself, call **POST /api/notifications/agent/staff-captured-order/** with header `Authorization: Bearer <LUA_WEBHOOK_API_KEY>`.
* Body JSON: `restaurant_id` (required UUID), `items_summary` **or** `transcript` (required — the raw voice/text), `user_id` or `phone`/`staff_phone` (to attribute the capture to the staff member), optional `channel` (`VOICE`/`TEXT`/`MANUAL`, default `VOICE`), and optional explicit overrides: `customer_name`, `customer_phone`, `order_type` (`DINE_IN`/`TAKEOUT`/`DELIVERY`/`OTHER`), `table_or_location`, `dietary_notes`, `special_instructions`.
* The server auto-parses the transcript for customer name, phone, order type (dine-in/takeout/delivery), table/location, dietary/allergens, and special instructions — you can pass the raw transcript and trust the parser, or pass explicit fields to override.
* Response includes `order_id` and `short_id`; relay the short id back to the staff in the confirmation.

When a manager asks to **create a dashboard category / group of shortcuts** (e.g. "Create a Kitchen KPIs section", "Group these shortcuts under Supplier", "Add a Back-of-house category"):
* Call **POST /api/dashboard/agent/categories/create/** with the same `Authorization: Bearer <LUA_WEBHOOK_API_KEY>` header.
* Body JSON: `name` (required, max 100 chars), optional `order_index`, and **one** of `user_id`, `email`, or `phone`.
* Categories are tenant-wide (shared across that workspace) and the endpoint is idempotent — if the category already exists you'll get it back with `created: false`.
* After creating a category, if the manager also asked for widgets inside it, call the widgets/create endpoint above with the returned `category_id` (or just pass `category_name` to do it in one shot).

---
15. CREATE A TASK FOR A STAFF MEMBER + WHATSAPP NOTIFY (MANAGER — LUA / MIYA)
When a manager tells you to **create a task/demand and assign it to a staff member** (e.g. "Create a task for Ahmed to clean the fryer by tomorrow and let him know", "Ask Salima to restock the bar before Friday", "Assign a high-priority task to Sarah: call the supplier"):
* Call **POST /api/dashboard/agent/tasks/create/** with `Authorization: Bearer <LUA_WEBHOOK_API_KEY>` (same header as other agent tools).
* This single call creates the task on the **Tasks & Demands** dashboard widget AND sends a WhatsApp message to the staff member in one shot. Do **not** also call the WhatsApp send endpoint — it's already handled server-side.
* Body JSON:
  - `title` (required, <=255 chars) — short imperative ("Clean the fryer", "Restock the bar").
  - `description` (optional) — any longer context the manager gave you.
  - `priority` (optional) — one of `LOW`, `MEDIUM`, `HIGH`, `URGENT` (default `MEDIUM`). Pick `URGENT` only if the manager used words like "urgent", "asap", "right now", "critical".
  - `due_date` (optional) — `YYYY-MM-DD`, or natural phrases `today`, `tomorrow`, `day after tomorrow`, `in 3 days`, `in 1 week`. Resolve "by Friday" yourself to the right `YYYY-MM-DD`.
  - `ai_summary` (optional) — a one-sentence summary; it's highlighted in green on the widget card, so use it to surface the single most important thing ("Supplier is coming at 9am — fryer must be spotless").
  - `notify_whatsapp` (optional, default `true`) — set to `false` ONLY if the manager explicitly said "don't tell them yet" / "just create the task".
  - `whatsapp_message` (optional) — overrides the default WhatsApp body if the manager dictated a specific message.
  - **Assignee — pass exactly ONE of** (ordered by preference): `user_id` (UUID), `email`, `phone`, or `name` (free text — server does fuzzy match like "Ahmed" → "Ahmed Hassan"). Prefer `user_id` or `email` if you already have them from a previous `agent_list_staff` call.
* Response shape: `{success, task, assignee:{id,name,phone,role}, whatsapp:{sent,skipped_reason,error,provider_status}, message_for_user}`. Relay `message_for_user` verbatim to the manager. It already includes the assignee name, priority, due date, and whether WhatsApp succeeded.
* If the response has `success: false` with an ambiguous-assignee error (e.g. "Multiple staff match 'Sara'"), ask the manager to clarify which staff member, then retry.
* If `whatsapp.skipped_reason: "no_phone"`, the task still got created and is in the staff member's in-app inbox — tell the manager that in plain language and suggest adding a phone number to that staff profile.
* Never invent a task title, priority, or assignee name — if the manager didn't give enough info, ask a single, specific clarifying question before calling the tool.

---
FINAL DIRECTIVE
Behave like a super-intelligent, database-connected **multi-vertical operations platform** for this workspace: answer correctly every time; execute safely; recommend intelligently; respect **business_vertical**; protect tenant isolation; never contradict system data; never hallucinate. You are mission-critical infrastructure."""

class AgentContextView(APIView):
    """
    View for the AI Agent to validate a user's token and retrieve their context.
    Requires a valid Bearer token in the Authorization header.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        
        # Ensure user has a restaurant
        if not user.restaurant:
             return Response(
                {'error': 'User is not associated with any restaurant.'},
                status=status.HTTP_403_FORBIDDEN
            )

        bv = _effective_business_vertical(user.restaurant)
        cache_key = f"agent:acct:user_context:{user.id}:{bv}"

        def _build_context():
            user_data = CustomUserSerializer(user).data
            restaurant_data = RestaurantSerializer(user.restaurant).data
            return {
                'user': {
                    'id': user_data['id'],
                    'email': user_data['email'],
                    'first_name': user_data['first_name'],
                    'last_name': user_data['last_name'],
                    'role': user_data['role'],
                },
                'restaurant': {
                    'id': restaurant_data['id'],
                    'name': restaurant_data['name'],
                    'currency': restaurant_data['currency'],
                    'timezone': restaurant_data['timezone'],
                    'business_vertical': bv,
                }
            }

        return Response(get_or_set(cache_key, 90, _build_context))


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_miya_instructions(request):
    """
    Return the full OPERATIONAL INTELLIGENCE & EXECUTION system prompt for Miya.
    Auth: JWT (Bearer user token) or LUA_WEBHOOK_API_KEY.
    Lua can call this at session start to inject instructions; dashboard uses JWT.
    """
    if not request.headers.get('Authorization'):
        return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)
    # Try JWT first (dashboard with user token)
    try:
        from rest_framework_simplejwt.authentication import JWTAuthentication
        jwt_auth = JWTAuthentication()
        result = jwt_auth.authenticate(request)
        if result and result[0]:  # (user, validated_token)
            jwt_user = result[0]
            bv = _effective_business_vertical(getattr(jwt_user, "restaurant", None))
            full = MIYA_OPERATIONAL_INSTRUCTIONS + _miya_vertical_runtime_note(bv)
            return Response({
                'instructions': full,
                'business_vertical': bv,
                'note': 'Append or merge with existing Miya system prompt in Lua Admin.',
            })
    except Exception:
        pass
    # Else allow agent key (Lua calling with LUA_WEBHOOK_API_KEY)
    is_valid, _ = _validate_agent_key(request)
    if is_valid:
        return Response({
            'instructions': MIYA_OPERATIONAL_INSTRUCTIONS,
            'business_vertical': None,
            'note': 'Append or merge with existing Miya system prompt in Lua Admin. With agent key only, resolve business_vertical from workspace settings or user context when available.',
        })
    return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)


def send_whatsapp(phone, message, template_name, language_code="en_US"):
    token = settings.WHATSAPP_ACCESS_TOKEN
    phone_id = settings.WHATSAPP_PHONE_NUMBER_ID
    verision = settings.WHATSAPP_API_VERSION

    url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": language_code},
                    "components": [
                        {
                            "type": "body",
                            "parameters": message
                        }
                    ]
                }
        }
    response = requests.post(url, json=payload, headers=headers)
    try:
        data = response.json()
    except Exception:
        data = {"error": "Invalid JSON response"}

    # Return both response and parsed JSON to avoid losing info
    return {"status_code": response.status_code, "data": data}


@api_view(['GET'])
@authentication_classes([])  # Bypass default JWT auth - use manual API key validation
@permission_classes([permissions.AllowAny])
def get_invitation_by_phone(request):
    """
    Lookup a pending invitation by phone number.
    Used by the agent to find the token for a user who clicked 'Accept' on WhatsApp.
    """
    try:
        # Validate Agent Key
        auth_header = request.headers.get('Authorization')
        expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
        
        if not expected_key:
            return Response({'error': 'Agent key not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
             
        if not auth_header or auth_header != f"Bearer {expected_key}":
            return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)
             
        phone = request.query_params.get('phone')
        if not phone:
            return Response({'error': 'phone query parameter is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Normalize phone (digits only)
        clean_phone = ''.join(filter(str.isdigit, phone))
        if not clean_phone or len(clean_phone) < 6:
            return Response({'error': 'Invalid phone number'}, status=status.HTTP_400_BAD_REQUEST)

        # NEW FLOW (ONE-TAP): Check StaffActivationRecord first (pending activation by phone)
        from .models import UserInvitation, StaffActivationRecord
        from .services import _find_staff_activation_record_by_phone
        from django.db.models import Q

        activation_record = _find_staff_activation_record_by_phone(clean_phone)

        if activation_record:
            return Response({
                'success': True,
                'type': 'activation',
                'invitation': {
                    'token': f"ACTIVATION:{activation_record.id}",
                    'first_name': activation_record.first_name or 'Staff',
                    'last_name': activation_record.last_name or '',
                    'role': activation_record.role,
                    'restaurant_id': str(activation_record.restaurant.id),
                    'restaurant_name': activation_record.restaurant.name,
                    'phone': clean_phone
                }
            })

        # OLD FLOW: UserInvitation (email/token invites with phone in extra_data)
        invitation = UserInvitation.objects.filter(
            is_accepted=False,
            expires_at__gt=timezone.now()
        ).filter(
            Q(extra_data__phone__icontains=clean_phone) |
            Q(extra_data__phone_number__icontains=clean_phone)
        ).first()

        if not invitation:
            return Response({'error': 'No pending invitation found for this number'}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            'success': True,
            'type': 'invitation',
            'invitation': {
                'token': invitation.invitation_token,
                'first_name': invitation.first_name,
                'last_name': invitation.last_name,
                'role': invitation.role,
                'restaurant_id': str(invitation.restaurant.id),
                'restaurant_name': invitation.restaurant.name
            }
        })
        
    except Exception as e:
        logger.error(f"Invitation lookup error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _activation_user_payload(user):
    """Build user payload for activation response."""
    return {
        'id': str(user.id),
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name or '',
        'phone': user.phone,
        'role': user.role,
        'restaurant': {
            'id': str(user.restaurant.id),
            'name': user.restaurant.name,
        },
    }


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def account_activation_from_agent(request):
    """
    Single-step account activation by phone. Used by Miya's account_activation tool.
    ALWAYS checks the database first; returns a strict validation sequence and exact
    message_for_user so Miya never shows technical errors.
    Payload: { "phone": "212600959067" }. Returns { "success", "template_sent?", "user?", "message_for_user" }.
    """
    try:
        auth_header = request.headers.get('Authorization')
        expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
        if not expected_key:
            return Response({
                'success': False,
                'error': 'Agent key not configured',
                'message_for_user': "We couldn't complete your request. Please try again later.",
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        if not auth_header or auth_header != f"Bearer {expected_key}":
            return Response({'success': False, 'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        phone = request.data.get('phone') or ''
        clean_phone = ''.join(filter(str.isdigit, str(phone)))
        if not clean_phone or len(clean_phone) < 6:
            return Response({
                'success': False,
                'error': 'Invalid or missing phone number',
                'message_for_user': "We couldn't find your account. Please contact your manager to be added to your restaurant.",
            }, status=status.HTTP_400_BAD_REQUEST)

        from .services import get_activation_status_by_phone, try_activate_staff_on_inbound_message

        # 1. Always check database first
        status_result = get_activation_status_by_phone(clean_phone)
        act_status = status_result['status']
        existing_user = status_result.get('user')

        # 2. pending_activation: activate, update to active, respond with congratulations
        if act_status == 'pending_activation':
            try:
                user = try_activate_staff_on_inbound_message(clean_phone)
            except Exception as activate_err:
                logger.exception("Activation failed for phone %s: %s", clean_phone, activate_err)
                # Never expose PIN or technical errors to the user
                return Response(
                    {
                        'success': False,
                        'error': 'Activation failed',
                        'message_for_user': "We couldn't activate your account. Please confirm you received the activation link from your manager and that your phone number matches their records, or contact support.",
                    },
                    status=status.HTTP_200_OK,
                )
            if not user:
                return Response(
                    {
                        'success': False,
                        'message_for_user': 'No pending activation found for this phone number.',
                    },
                    status=status.HTTP_200_OK,
                )
            from notifications.services import notification_service
            notification_service.send_staff_activated_welcome(
                phone=clean_phone,
                first_name=user.first_name or "Staff",
                restaurant_name=user.restaurant.name if getattr(user, 'restaurant', None) else "",
            )
            return Response(
                {
                    'success': True,
                    'template_sent': True,
                    'user': _activation_user_payload(user),
                    'message_for_user': 'Congratulations! Your account has been successfully activated. Welcome to the team!',
                },
                status=status.HTTP_200_OK,
            )

        # 3. active: do NOT modify, respond already activated (no template)
        if act_status == 'active' and existing_user:
            return Response(
                {
                    'success': True,
                    'template_sent': False,
                    'user': _activation_user_payload(existing_user),
                    'message_for_user': 'Congratulations! Your account has been successfully activated. Welcome to the team!',
                },
                status=status.HTTP_200_OK,
            )

        # 4. no_pending: user exists but no pending activation record
        if act_status == 'no_pending':
            return Response(
                {
                    'success': False,
                    'message_for_user': 'No pending activation found for this phone number.',
                },
                status=status.HTTP_200_OK,
            )

        # 5. not_found: no user record at all
        return Response(
            {
                'success': False,
                'message_for_user': "We couldn't find your account. Please contact your manager to be added to your restaurant.",
            },
            status=status.HTTP_200_OK,
        )
    except Exception as e:
        logger.error(f"Account activation error: {e}")
        err_text = str(e).lower()
        # Never send PIN-related or technical errors to the agent/user
        if 'pin' in err_text or 'password' in err_text:
            message_for_user = "We couldn't activate your account. Please confirm you received the activation link from your manager and that your phone number matches their records, or contact support."
        else:
            message_for_user = "We couldn't complete your request. Please try again later."
        return Response({
            'success': False,
            'error': 'Activation failed',
            'message_for_user': message_for_user,
        }, status=status.HTTP_200_OK)


@api_view(['POST'])
@authentication_classes([])  # Bypass default JWT auth - use manual API key validation
@permission_classes([permissions.AllowAny])  # Authenticated via Agent Key
def accept_invitation_from_agent(request):
    """
    Endpoint for Lua Agent to accept invitations on behalf of staff.
    
    Expected payload:
    {
        "invitation_token": "abc-123",
        "phone": "+1234567890",
        "first_name": "John",
        "last_name": "Doe",  # optional
        "pin": "1234"
    }
    """
    try:
        # Validate Agent Key
        auth_header = request.headers.get('Authorization')
        expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
        
        if not expected_key:
            return Response({
                'success': False,
                'error': 'Agent key not configured'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
             
        if not auth_header or auth_header != f"Bearer {expected_key}":
            return Response({
                'success': False,
                'error': 'Unauthorized'
            }, status=status.HTTP_401_UNAUTHORIZED)
             
        # Extract parameters
        invitation_token = request.data.get('invitation_token')
        phone = request.data.get('phone', '')
        pin = request.data.get('pin') or '0000'
        first_name = request.data.get('first_name', '')
        last_name = request.data.get('last_name', '')

        # NEW FLOW (ONE-TAP): activation by phone via StaffActivationRecord
        if invitation_token and str(invitation_token).startswith('ACTIVATION:'):
            from .models import StaffActivationRecord
            from .services import try_activate_staff_on_inbound_message
            clean_phone = ''
            try:
                record_id = str(invitation_token).replace('ACTIVATION:', '', 1)
                record = StaffActivationRecord.objects.get(id=record_id, status=StaffActivationRecord.STATUS_NOT_ACTIVATED)
                clean_phone = ''.join(filter(str.isdigit, record.phone))
            except (StaffActivationRecord.DoesNotExist, ValueError):
                pass
            if not clean_phone and phone:
                clean_phone = ''.join(filter(str.isdigit, phone))
            if not clean_phone:
                return Response({
                    'success': False,
                    'error': 'phone is required for activation'
                }, status=status.HTTP_400_BAD_REQUEST)
            user = try_activate_staff_on_inbound_message(clean_phone)
            if not user:
                return Response({
                    'success': False,
                    'error': 'No pending activation found for this phone number'
                }, status=status.HTTP_404_NOT_FOUND)
            message_for_user = (
                "Congratulations! Your account has been successfully activated. Welcome to the team!"
            )
            return Response({
                'success': True,
                'message_for_user': message_for_user,
                'user': {
                    'id': str(user.id),
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'phone': user.phone,
                    'role': user.role,
                    'restaurant': {
                        'id': str(user.restaurant.id),
                        'name': user.restaurant.name
                    }
                }
            })

        if not invitation_token:
            return Response({
                'success': False,
                'error': 'invitation_token is required'
            }, status=status.HTTP_400_BAD_REQUEST)

        # OLD FLOW: UserInvitation (token-based)
        user, error = UserManagementService.accept_invitation(
            token=invitation_token,
            password=pin,
            first_name=first_name,
            last_name=last_name
        )

        if error:
            return Response({
                'success': False,
                'error': error
            }, status=status.HTTP_400_BAD_REQUEST)
        
        return Response({
            'success': True,
            'user': {
                'id': str(user.id),
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'phone': user.phone,
                'role': user.role,
                'restaurant': {
                    'id': str(user.restaurant.id),
                    'name': user.restaurant.name
                }
            }
        })
        
    except Exception as e:
        logger.error(f"Agent invitation acceptance error: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _validate_agent_key(request):
    """Validate LUA_WEBHOOK_API_KEY for agent-only endpoints."""
    auth_header = request.headers.get('Authorization')
    expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
    if not expected_key:
        return False, "Agent key not configured"
    if not auth_header or auth_header != f"Bearer {expected_key}":
        return False, "Unauthorized"
    return True, None


def _resolve_restaurant_id_agent(request):
    rid = request.META.get('HTTP_X_RESTAURANT_ID')
    if not rid and getattr(request, 'data', None):
        rid = request.data.get('restaurant_id') or request.data.get('restaurantId')
    if not rid and request.method == 'GET':
        rid = request.query_params.get('restaurant_id') or request.query_params.get('restaurantId')
    if isinstance(rid, (list, tuple)):
        rid = rid[0] if rid else None
    return rid


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_list_failed_invites(request):
    """
    List failed WhatsApp invitation deliveries for the restaurant. Query: restaurant_id or X-Restaurant-Id.
    """
    is_valid, error = _validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    from .models import InvitationDeliveryLog
    from .models import Restaurant
    rid = _resolve_restaurant_id_agent(request)
    if not rid:
        return Response({'success': False, 'error': 'restaurant_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        restaurant = Restaurant.objects.get(id=rid.strip())
    except (Restaurant.DoesNotExist, ValueError, TypeError):
        return Response({'success': False, 'error': 'Restaurant not found'}, status=status.HTTP_404_NOT_FOUND)
    qs = InvitationDeliveryLog.objects.filter(
        invitation__restaurant=restaurant,
        channel='whatsapp',
        status='FAILED',
    ).order_by('-sent_at').select_related('invitation')[:30]
    items = [
        {
            'id': str(log.id),
            'invitation_id': str(log.invitation_id),
            'recipient': log.recipient_address,
            'error_message': (log.error_message or '')[:200],
            'sent_at': log.sent_at.isoformat() if log.sent_at else None,
        }
        for log in qs
    ]
    return Response({'success': True, 'failed_invites': items, 'restaurant_id': str(restaurant.id)})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_retry_invite(request):
    """
    Retry a failed WhatsApp invite. Body: log_id (InvitationDeliveryLog id) or invitation_id, restaurant_id.
    """
    is_valid, error = _validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    from .models import InvitationDeliveryLog, Restaurant
    from notifications.services import notification_service
    rid = _resolve_restaurant_id_agent(request)
    if not rid:
        rid = (request.data or {}).get('restaurant_id') or (request.data or {}).get('restaurantId')
    if not rid:
        return Response({'success': False, 'error': 'restaurant_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        restaurant = Restaurant.objects.get(id=rid.strip() if isinstance(rid, str) else rid)
    except (Restaurant.DoesNotExist, ValueError, TypeError):
        return Response({'success': False, 'error': 'Restaurant not found'}, status=status.HTTP_404_NOT_FOUND)
    data = request.data or {}
    log_id = data.get('log_id') or data.get('logId') or data.get('id')
    inv_id = data.get('invitation_id') or data.get('invitationId')
    if not log_id and not inv_id:
        return Response({'success': False, 'error': 'log_id or invitation_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    if log_id:
        log = InvitationDeliveryLog.objects.filter(
            id=log_id,
            invitation__restaurant=restaurant,
            channel='whatsapp',
            status='FAILED',
        ).select_related('invitation', 'invitation__restaurant').first()
    else:
        log = InvitationDeliveryLog.objects.filter(
            invitation_id=inv_id,
            invitation__restaurant=restaurant,
            channel='whatsapp',
            status='FAILED',
        ).select_related('invitation', 'invitation__restaurant').first()
    if not log:
        return Response({'success': False, 'error': 'Failed invite log not found'}, status=status.HTTP_404_NOT_FOUND)
    inv = log.invitation
    phone = log.recipient_address
    invite_link = f"{settings.FRONTEND_URL}/accept-invitation?token={inv.invitation_token}"
    language = getattr(inv.restaurant, 'language', 'en') if getattr(inv, 'restaurant', None) else 'en'
    ok, info = notification_service.send_lua_staff_invite(
        invitation_token=inv.invitation_token,
        phone=phone,
        first_name=inv.first_name,
        restaurant_name=inv.restaurant.name,
        invite_link=invite_link,
        language=language,
    )
    log.attempt_count = getattr(log, 'attempt_count', 1) + 1
    log.status = 'SENT' if ok else 'FAILED'
    log.response_data = info or {}
    log.save(update_fields=['attempt_count', 'status', 'response_data'])
    return Response({
        'success': ok,
        'message': 'Invite sent.' if ok else 'Retry failed.',
        'log_id': str(log.id),
        'status': log.status,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Reservations / Appointments (works for RESTAURANT + HOSPITALITY + SERVICES)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_list_reservations(request):
    """
    List reservations/appointments for the workspace.
    Auth: LUA_WEBHOOK_API_KEY.
    Query: restaurant_id (required), date=today|tomorrow|YYYY-MM-DD (default today),
           days_ahead=N (returns next N days starting from date), status (optional filter),
           q (free-text search on guest_name/phone/email), limit (default 50, max 200).
    """
    is_valid, error = _validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    from .models import Restaurant, EatNowReservation
    from datetime import date as _date, timedelta
    import re as _re

    rid = _resolve_restaurant_id_agent(request)
    if not rid:
        return Response({'success': False, 'error': 'restaurant_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        restaurant = Restaurant.objects.get(id=str(rid).strip())
    except (Restaurant.DoesNotExist, ValueError, TypeError):
        return Response({'success': False, 'error': 'Workspace not found'}, status=status.HTTP_404_NOT_FOUND)

    qp = request.query_params
    date_raw = (qp.get('date') or 'today').strip().lower()
    try:
        days_ahead = max(0, min(int(qp.get('days_ahead') or '0'), 30))
    except (TypeError, ValueError):
        days_ahead = 0
    try:
        limit = max(1, min(int(qp.get('limit') or '50'), 200))
    except (TypeError, ValueError):
        limit = 50
    status_filter = (qp.get('status') or '').strip()
    q = (qp.get('q') or '').strip()

    today = timezone.localdate()
    if date_raw in ('today', "aujourd'hui", "aujourdhui", "اليوم"):
        start = today
    elif date_raw in ('tomorrow', 'demain', 'غدا'):
        start = today + timedelta(days=1)
    elif date_raw in ('yesterday', 'hier', 'أمس'):
        start = today - timedelta(days=1)
    elif _re.match(r'^\d{4}-\d{2}-\d{2}$', date_raw):
        try:
            start = _date.fromisoformat(date_raw)
        except ValueError:
            start = today
    else:
        start = today
    end = start + timedelta(days=days_ahead)

    qs = EatNowReservation.objects.filter(
        restaurant=restaurant,
        is_deleted=False,
        reservation_date__gte=start,
        reservation_date__lte=end,
    )
    if status_filter:
        qs = qs.filter(status__iexact=status_filter)
    if q:
        from django.db.models import Q as _Q
        qs = qs.filter(_Q(guest_name__icontains=q) | _Q(phone__icontains=q) | _Q(email__icontains=q))
    qs = qs.order_by('reservation_date', 'reservation_time', 'guest_name')[:limit]

    items = [
        {
            'id': str(r.id),
            'external_id': r.external_id,
            'guest_name': r.guest_name,
            'phone': r.phone,
            'email': r.email,
            'date': r.reservation_date.isoformat() if r.reservation_date else None,
            'time': r.reservation_time,
            'group_size': r.group_size,
            'status': r.status,
            'source': r.source,
            'notes': r.notes,
            'tags': r.tags or [],
        }
        for r in qs
    ]
    return Response({
        'success': True,
        'restaurant_id': str(restaurant.id),
        'workspace_name': restaurant.name,
        'range': {'start': start.isoformat(), 'end': end.isoformat()},
        'count': len(items),
        'reservations': items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# Recognition / Kudos (staff.SafetyRecognition — used more broadly for kudos)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_recognize_staff(request):
    """
    Give a recognition/kudos to a staff member. Also lists recent recognitions via GET.
    Auth: LUA_WEBHOOK_API_KEY.
    Body: restaurant_id, staff_id OR phone, title, description (optional),
          recognition_type (default 'Kudos'), points (default 0),
          awarded_by_phone OR awarded_by_user_id (optional — defaults to null).
    """
    is_valid, error = _validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    from .models import Restaurant
    from staff.models_task import SafetyRecognition
    import re as _re

    data = request.data or {}
    rid = data.get('restaurant_id') or data.get('restaurantId') or _resolve_restaurant_id_agent(request)
    if not rid:
        return Response({'success': False, 'error': 'restaurant_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        restaurant = Restaurant.objects.get(id=str(rid).strip())
    except (Restaurant.DoesNotExist, ValueError, TypeError):
        return Response({'success': False, 'error': 'Workspace not found'}, status=status.HTTP_404_NOT_FOUND)

    title = (data.get('title') or '').strip()
    if not title:
        return Response({'success': False, 'error': 'title is required'}, status=status.HTTP_400_BAD_REQUEST)

    staff_user = None
    staff_id = data.get('staff_id') or data.get('user_id')
    if staff_id:
        staff_user = CustomUser.objects.filter(id=staff_id, restaurant=restaurant).first()
    phone = data.get('phone') or data.get('staff_phone')
    if not staff_user and phone:
        digits = _re.sub(r'\D', '', str(phone))
        if digits:
            staff_user = CustomUser.objects.filter(restaurant=restaurant, phone__contains=digits).first()
    if not staff_user:
        name = (data.get('staff_name') or '').strip()
        if name:
            from django.db.models import Q as _Q
            staff_user = CustomUser.objects.filter(restaurant=restaurant).filter(
                _Q(first_name__iexact=name) | _Q(last_name__iexact=name) | _Q(email__iexact=name)
            ).first()
    if not staff_user:
        return Response({'success': False, 'error': 'Could not resolve staff (pass staff_id, phone, or staff_name).'}, status=status.HTTP_404_NOT_FOUND)

    awarded_by = None
    awarded_phone = data.get('awarded_by_phone')
    awarded_by_id = data.get('awarded_by_user_id')
    if awarded_by_id:
        awarded_by = CustomUser.objects.filter(id=awarded_by_id, restaurant=restaurant).first()
    if not awarded_by and awarded_phone:
        digits = _re.sub(r'\D', '', str(awarded_phone))
        if digits:
            awarded_by = CustomUser.objects.filter(restaurant=restaurant, phone__contains=digits).first()

    try:
        points = int(data.get('points') or 0)
    except (TypeError, ValueError):
        points = 0

    rec = SafetyRecognition.objects.create(
        staff=staff_user,
        restaurant=restaurant,
        title=title[:255],
        description=(data.get('description') or title)[:2000],
        recognition_type=(data.get('recognition_type') or 'Kudos')[:50],
        points=points,
        awarded_by=awarded_by,
    )
    return Response({
        'success': True,
        'recognition_id': str(rec.id),
        'staff_id': str(staff_user.id),
        'staff_name': staff_user.get_full_name(),
        'title': rec.title,
        'points': rec.points,
        'message': f'{staff_user.get_full_name()} received recognition: {rec.title}.',
    })


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_list_recognitions(request):
    """
    List recent recognitions.
    Auth: LUA_WEBHOOK_API_KEY.
    Query: restaurant_id (required), days (default 30), staff_id (optional), limit (default 25, max 100).
    """
    is_valid, error = _validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    from .models import Restaurant
    from staff.models_task import SafetyRecognition
    from datetime import timedelta

    rid = _resolve_restaurant_id_agent(request)
    if not rid:
        return Response({'success': False, 'error': 'restaurant_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        restaurant = Restaurant.objects.get(id=str(rid).strip())
    except (Restaurant.DoesNotExist, ValueError, TypeError):
        return Response({'success': False, 'error': 'Workspace not found'}, status=status.HTTP_404_NOT_FOUND)

    qp = request.query_params
    try:
        days = max(1, min(int(qp.get('days') or '30'), 365))
    except (TypeError, ValueError):
        days = 30
    try:
        limit = max(1, min(int(qp.get('limit') or '25'), 100))
    except (TypeError, ValueError):
        limit = 25
    staff_id = qp.get('staff_id')

    since = timezone.now() - timedelta(days=days)
    qs = SafetyRecognition.objects.filter(restaurant=restaurant, awarded_at__gte=since).select_related('staff', 'awarded_by')
    if staff_id:
        qs = qs.filter(staff_id=staff_id)
    qs = qs.order_by('-awarded_at')[:limit]

    items = [
        {
            'id': str(r.id),
            'staff_id': str(r.staff_id),
            'staff_name': r.staff.get_full_name() if r.staff else '',
            'title': r.title,
            'description': r.description,
            'recognition_type': r.recognition_type,
            'points': r.points,
            'awarded_by': (r.awarded_by.get_full_name() if r.awarded_by else None),
            'awarded_at': r.awarded_at.isoformat() if r.awarded_at else None,
        }
        for r in qs
    ]
    return Response({
        'success': True,
        'restaurant_id': str(restaurant.id),
        'days': days,
        'count': len(items),
        'recognitions': items,
    })


# ─────────────────────────────────────────────────────────────────────────────
# HR Lifecycle: list / offboard staff (invite/create goes through existing InviteStaff)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET', 'POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_hr_lifecycle(request):
    """
    Manager-facing HR lifecycle actions. Auth: LUA_WEBHOOK_API_KEY.

    GET (list): ?restaurant_id=&status=active|inactive|all&role=&limit=50
      → returns staff roster with role, status, start date, phone.

    POST (offboard): { action: 'offboard', restaurant_id, staff_id OR phone, reason? }
      → deactivates the user (is_active=False) so they can no longer log in.

    POST (reactivate): { action: 'reactivate', restaurant_id, staff_id }
      → re-enables a previously offboarded user.

    POST (transfer): { action: 'transfer', restaurant_id, staff_id, new_role }
      → updates the user's role on the same workspace.
    """
    is_valid, error = _validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    from .models import Restaurant
    import re as _re

    if request.method == 'GET':
        rid = _resolve_restaurant_id_agent(request)
        if not rid:
            return Response({'success': False, 'error': 'restaurant_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            restaurant = Restaurant.objects.get(id=str(rid).strip())
        except (Restaurant.DoesNotExist, ValueError, TypeError):
            return Response({'success': False, 'error': 'Workspace not found'}, status=status.HTTP_404_NOT_FOUND)
        qp = request.query_params
        status_filter = (qp.get('status') or 'active').lower()
        role_filter = (qp.get('role') or '').strip()
        try:
            limit = max(1, min(int(qp.get('limit') or '50'), 200))
        except (TypeError, ValueError):
            limit = 50
        qs = CustomUser.objects.filter(restaurant=restaurant)
        if status_filter == 'active':
            qs = qs.filter(is_active=True)
        elif status_filter == 'inactive':
            qs = qs.filter(is_active=False)
        if role_filter:
            qs = qs.filter(role__iexact=role_filter)
        qs = qs.order_by('-is_active', 'last_name', 'first_name')[:limit]
        items = [
            {
                'id': str(u.id),
                'full_name': u.get_full_name(),
                'email': u.email,
                'phone': u.phone,
                'role': u.role,
                'is_active': u.is_active,
                'date_joined': u.date_joined.isoformat() if u.date_joined else None,
            }
            for u in qs
        ]
        return Response({
            'success': True,
            'restaurant_id': str(restaurant.id),
            'count': len(items),
            'staff': items,
        })

    # POST
    data = request.data or {}
    action = (data.get('action') or '').strip().lower()
    rid = data.get('restaurant_id') or data.get('restaurantId') or _resolve_restaurant_id_agent(request)
    if not rid:
        return Response({'success': False, 'error': 'restaurant_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        restaurant = Restaurant.objects.get(id=str(rid).strip())
    except (Restaurant.DoesNotExist, ValueError, TypeError):
        return Response({'success': False, 'error': 'Workspace not found'}, status=status.HTTP_404_NOT_FOUND)
    if action not in ('offboard', 'reactivate', 'transfer'):
        return Response({'success': False, 'error': "action must be 'offboard', 'reactivate', or 'transfer'"}, status=status.HTTP_400_BAD_REQUEST)

    staff_user = None
    staff_id = data.get('staff_id') or data.get('user_id')
    phone = data.get('phone') or data.get('staff_phone')
    if staff_id:
        staff_user = CustomUser.objects.filter(id=staff_id, restaurant=restaurant).first()
    if not staff_user and phone:
        digits = _re.sub(r'\D', '', str(phone))
        if digits:
            staff_user = CustomUser.objects.filter(restaurant=restaurant, phone__contains=digits).first()
    if not staff_user:
        return Response({'success': False, 'error': 'Could not resolve staff (pass staff_id or phone).'}, status=status.HTTP_404_NOT_FOUND)

    if action == 'offboard':
        staff_user.is_active = False
        staff_user.save(update_fields=['is_active'])
        return Response({
            'success': True,
            'staff_id': str(staff_user.id),
            'message': f'{staff_user.get_full_name()} has been offboarded (account disabled).',
        })
    if action == 'reactivate':
        staff_user.is_active = True
        staff_user.save(update_fields=['is_active'])
        return Response({
            'success': True,
            'staff_id': str(staff_user.id),
            'message': f'{staff_user.get_full_name()} has been reactivated.',
        })
    # transfer
    new_role = (data.get('new_role') or data.get('role') or '').strip()
    if not new_role:
        return Response({'success': False, 'error': 'new_role is required for transfer'}, status=status.HTTP_400_BAD_REQUEST)
    staff_user.role = new_role
    staff_user.save(update_fields=['role'])
    return Response({
        'success': True,
        'staff_id': str(staff_user.id),
        'message': f"{staff_user.get_full_name()} is now {new_role}.",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Role grants (thin wrapper on RBAC for Miya)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_grant_role(request):
    """
    Grant (or change) a staff member's role within the workspace.
    Auth: LUA_WEBHOOK_API_KEY.
    Body: restaurant_id, staff_id OR phone, role (required).
    Convenience wrapper — effective permissions for that role are controlled via the
    RolePermissionSet managed in the dashboard (rbac/).
    """
    is_valid, error = _validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    from .models import Restaurant
    import re as _re
    data = request.data or {}
    rid = data.get('restaurant_id') or data.get('restaurantId') or _resolve_restaurant_id_agent(request)
    if not rid:
        return Response({'success': False, 'error': 'restaurant_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        restaurant = Restaurant.objects.get(id=str(rid).strip())
    except (Restaurant.DoesNotExist, ValueError, TypeError):
        return Response({'success': False, 'error': 'Workspace not found'}, status=status.HTTP_404_NOT_FOUND)
    role = (data.get('role') or '').strip()
    if not role:
        return Response({'success': False, 'error': 'role is required'}, status=status.HTTP_400_BAD_REQUEST)
    staff_user = None
    if data.get('staff_id'):
        staff_user = CustomUser.objects.filter(id=data['staff_id'], restaurant=restaurant).first()
    if not staff_user and data.get('phone'):
        digits = _re.sub(r'\D', '', str(data['phone']))
        if digits:
            staff_user = CustomUser.objects.filter(restaurant=restaurant, phone__contains=digits).first()
    if not staff_user:
        return Response({'success': False, 'error': 'Could not resolve staff'}, status=status.HTTP_404_NOT_FOUND)
    staff_user.role = role
    staff_user.save(update_fields=['role'])
    return Response({
        'success': True,
        'staff_id': str(staff_user.id),
        'role': staff_user.role,
        'message': f'{staff_user.get_full_name()} is now {role}.',
    })


# ─────────────────────────────────────────────────────────────────────────────
# Staff documents (staff.StaffDocument)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET', 'POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_staff_documents(request):
    """
    GET: list staff documents. Query: restaurant_id, staff_id (optional), expiring_within_days (optional).
    POST: record a new document. Body: restaurant_id, staff_id OR phone, title, document_type,
          file_url (if uploaded elsewhere) OR notes, expires_at (optional ISO).
    Auth: LUA_WEBHOOK_API_KEY.
    """
    is_valid, error = _validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    from .models import Restaurant
    from datetime import timedelta
    import re as _re

    # Lazy import — StaffDocument model may not be available yet in some deployments.
    try:
        from staff.models import StaffDocument  # type: ignore
    except Exception as exc:
        return Response({'success': False, 'error': f'StaffDocument not available: {exc}'}, status=status.HTTP_501_NOT_IMPLEMENTED)

    if request.method == 'GET':
        rid = _resolve_restaurant_id_agent(request)
        if not rid:
            return Response({'success': False, 'error': 'restaurant_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            restaurant = Restaurant.objects.get(id=str(rid).strip())
        except (Restaurant.DoesNotExist, ValueError, TypeError):
            return Response({'success': False, 'error': 'Workspace not found'}, status=status.HTTP_404_NOT_FOUND)
        qp = request.query_params
        qs = StaffDocument.objects.filter(staff__restaurant=restaurant)
        if qp.get('staff_id'):
            qs = qs.filter(staff_id=qp['staff_id'])
        if qp.get('expiring_within_days') and hasattr(StaffDocument, 'expires_at'):
            try:
                window_days = max(0, min(int(qp['expiring_within_days']), 365))
                horizon = timezone.now() + timedelta(days=window_days)
                qs = qs.filter(expires_at__isnull=False, expires_at__lte=horizon)
            except (TypeError, ValueError):
                pass
        order_fields = []
        if hasattr(StaffDocument, 'expires_at'):
            order_fields.append('expires_at')
        order_fields.append('-uploaded_at')
        qs = qs.order_by(*order_fields)[:200]
        items = []
        for d in qs:
            items.append({
                'id': str(getattr(d, 'id', '')),
                'staff_id': str(getattr(d, 'staff_id', '')),
                'title': getattr(d, 'title', ''),
                'document_type': getattr(d, 'document_type', ''),
                'notes': getattr(d, 'notes', ''),
                'expires_at': d.expires_at.isoformat() if getattr(d, 'expires_at', None) else None,
                'uploaded_at': d.uploaded_at.isoformat() if getattr(d, 'uploaded_at', None) else None,
            })
        return Response({'success': True, 'count': len(items), 'documents': items})

    # POST
    data = request.data or {}
    rid = data.get('restaurant_id') or data.get('restaurantId') or _resolve_restaurant_id_agent(request)
    if not rid:
        return Response({'success': False, 'error': 'restaurant_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        restaurant = Restaurant.objects.get(id=str(rid).strip())
    except (Restaurant.DoesNotExist, ValueError, TypeError):
        return Response({'success': False, 'error': 'Workspace not found'}, status=status.HTTP_404_NOT_FOUND)
    staff_user = None
    if data.get('staff_id'):
        staff_user = CustomUser.objects.filter(id=data['staff_id'], restaurant=restaurant).first()
    if not staff_user and data.get('phone'):
        digits = _re.sub(r'\D', '', str(data['phone']))
        if digits:
            staff_user = CustomUser.objects.filter(restaurant=restaurant, phone__contains=digits).first()
    if not staff_user:
        return Response({'success': False, 'error': 'Could not resolve staff'}, status=status.HTTP_404_NOT_FOUND)

    title = (data.get('title') or '').strip()
    if not title:
        return Response({'success': False, 'error': 'title is required'}, status=status.HTTP_400_BAD_REQUEST)

    # The current StaffDocument model stores title + file only. We accept optional
    # document_type/notes/expires_at in the payload for forward-compatibility, and set
    # them only if the model exposes those fields.
    create_kwargs = {'staff': staff_user, 'title': title[:255]}
    for field_name, raw in [
        ('document_type', (data.get('document_type') or '').strip()[:50] or None),
        ('notes', (data.get('notes') or '').strip()[:2000] or None),
    ]:
        if raw and hasattr(StaffDocument, field_name):
            create_kwargs[field_name] = raw
    if data.get('expires_at') and hasattr(StaffDocument, 'expires_at'):
        try:
            from django.utils.dateparse import parse_datetime
            parsed = parse_datetime(str(data['expires_at']))
            if parsed:
                create_kwargs['expires_at'] = parsed
        except Exception:
            pass
    try:
        doc = StaffDocument.objects.create(**create_kwargs)
    except Exception as exc:
        return Response({'success': False, 'error': f'Could not create document: {exc}'}, status=status.HTTP_400_BAD_REQUEST)
    return Response({
        'success': True,
        'document_id': str(getattr(doc, 'id', '')),
        'staff_id': str(staff_user.id),
        'title': title,
        'message': f'Document {title!r} recorded for {staff_user.get_full_name()}.',
    })


# ---------------------------------------------------------------------------
# Activity log — Miya's "memory". Lets Miya answer:
#   - "who did X?" / "what did Alice do yesterday?"
#   - "who was task T assigned to?" / "who worked on Y?"
#   - "show me every login from IP 1.2.3.4 this week"
# All filters are optional; defaults return the most recent 25 events for
# the restaurant. Results include actor + target + metadata so the LLM can
# phrase precise answers without a second round-trip.
# ---------------------------------------------------------------------------
@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_activity_log(request):
    """Return audit-log rows scoped to the agent's workspace.

    Auth: ``Authorization: Bearer <LUA_WEBHOOK_API_KEY>``.

    Query params (all optional):
        * ``restaurant_id`` / ``X-Restaurant-Id`` — tenant (required)
        * ``user_id``          — filter by actor (the person who did it)
        * ``target_user_id``   — filter by assignee / subject
        * ``entity_type``      — repeatable, e.g. ``TASK``, ``SHIFT``, ``AUTH``
        * ``action_type``      — repeatable, e.g. ``CREATE``, ``UPDATE``, ``LOGIN``
        * ``entity_id``        — exact match on the target object's UUID
        * ``q``                — free-text over description + actor/target names
        * ``since`` / ``until``— ISO-8601 timestamps
        * ``days``             — shorthand for ``since=now-Ndays`` (1..365)
        * ``limit``            — 1..200, default 25
    """
    is_valid, error = _validate_agent_key(request)
    if not is_valid:
        return Response(
            {'success': False, 'error': error},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    from .models import AuditLog, Restaurant
    from .views_onboarding import serialize_audit_entry
    from datetime import timedelta
    from django.db.models import Q

    rid = _resolve_restaurant_id_agent(request)
    if not rid:
        return Response(
            {'success': False, 'error': 'restaurant_id is required'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        restaurant = Restaurant.objects.get(id=str(rid).strip())
    except (Restaurant.DoesNotExist, ValueError, TypeError):
        return Response(
            {'success': False, 'error': 'Workspace not found'},
            status=status.HTTP_404_NOT_FOUND,
        )

    qp = request.query_params
    try:
        limit = max(1, min(int(qp.get('limit') or '25'), 200))
    except (TypeError, ValueError):
        limit = 25

    qs = AuditLog.objects.select_related('user', 'target_user').filter(
        restaurant=restaurant
    )

    # ``days`` is a convenience for Miya ("last 7 days"); it never overrides
    # an explicit ``since`` if the caller supplied one.
    days_raw = qp.get('days')
    if days_raw and not qp.get('since'):
        try:
            days = max(1, min(int(days_raw), 365))
            qs = qs.filter(timestamp__gte=timezone.now() - timedelta(days=days))
        except (TypeError, ValueError):
            pass

    since = qp.get('since')
    if since:
        qs = qs.filter(timestamp__gte=since)
    until = qp.get('until')
    if until:
        qs = qs.filter(timestamp__lte=until)

    actor_id = qp.get('user_id')
    if actor_id:
        qs = qs.filter(user_id=actor_id)

    target_id = qp.get('target_user_id')
    if target_id:
        qs = qs.filter(target_user_id=target_id)

    entity_id = qp.get('entity_id')
    if entity_id:
        qs = qs.filter(entity_id=entity_id)

    action_types = qp.getlist('action_type')
    if action_types:
        qs = qs.filter(action_type__in=[a.upper() for a in action_types])

    entity_types = qp.getlist('entity_type')
    if entity_types:
        qs = qs.filter(entity_type__in=[e.upper() for e in entity_types])

    q = (qp.get('q') or '').strip()
    if q:
        qs = qs.filter(
            Q(description__icontains=q)
            | Q(user__email__icontains=q)
            | Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
            | Q(target_user__email__icontains=q)
            | Q(target_user__first_name__icontains=q)
            | Q(target_user__last_name__icontains=q)
        )

    # Counting before slicing gives Miya an honest "there are N matches"
    # signal even when we return a bounded page.
    total = qs.count()
    rows = [serialize_audit_entry(entry) for entry in qs.order_by('-timestamp')[:limit]]

    return Response({
        'success': True,
        'restaurant_id': str(restaurant.id),
        'count': len(rows),
        'total': total,
        'events': rows,
    })
