"""Onboarding + Audit Log APIs.

These power:
* ``GET /api/accounts/onboarding/`` — current tenant onboarding progress.
* ``POST /api/accounts/onboarding/complete-step/`` — mark a step complete.
* ``POST /api/accounts/onboarding/seed/`` — one-shot helper that provisions a
  primary branch, a weekly schedule template, an opening checklist, and a
  sample menu category+item so the tenant is not staring at an empty dashboard.
* ``GET /api/accounts/audit-logs/`` — list of ``AuditLog`` rows (tenant-scoped,
  manager-readable, paginated, filterable).

Only SUPER_ADMIN / OWNER / ADMIN can advance onboarding. Managers can read the
audit log for their own tenant (scoped by ``user.restaurant``) but cannot see
other tenants' rows.
"""

from __future__ import annotations

from datetime import time as dt_time

from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.models import AuditLog, BusinessLocation, Restaurant


# New, user-facing 6-step onboarding flow. Step 0 ("welcome") is a display
# stage only — it has no backend side-effects — so we don't track it server
# side. Step 5 ("google_calendar") is optional: the onboarding completes
# when all REQUIRED steps are done; we record google_calendar progress
# separately for analytics but it doesn't block completion.
#
# The legacy seeder flow (branch/shift_template/checklist/menu) still
# exists on OnboardingSeedView as a convenience primitive — it's no longer
# a wizard step the user sees.
ONBOARDING_STEPS: tuple[str, ...] = (
    'staff_csv',
    'widgets',
    'widget_permissions',
    'category_owners',
    'google_calendar',
)

# Subset of ONBOARDING_STEPS that must be true before we auto-set
# ``onboarding_completed_at`` when the user advances through the wizard
# normally (upload/save each step). ``google_calendar`` stays out of this
# set. Steps are never *blocking*: owners can also POST
# ``{"complete_onboarding": true}`` to dismiss the wizard and unlock the
# dashboard without finishing each step.
REQUIRED_STEPS: frozenset[str] = frozenset({
    'staff_csv',
    'widgets',
    'widget_permissions',
    'category_owners',
})


def _default_state() -> dict:
    return {step: False for step in ONBOARDING_STEPS}


def _merge_state(existing: dict | None) -> dict:
    state = _default_state()
    if isinstance(existing, dict):
        for key in ONBOARDING_STEPS:
            state[key] = bool(existing.get(key, False))
    return state


def _is_owner_like(user) -> bool:
    role = str(getattr(user, 'role', '') or '').upper()
    return role in {'SUPER_ADMIN', 'OWNER', 'ADMIN'}


class OnboardingStatusView(APIView):
    """Return / update onboarding progress for the caller's tenant."""

    permission_classes = [permissions.IsAuthenticated]

    def _payload(self, restaurant: Restaurant) -> dict:
        state = _merge_state(restaurant.onboarding_state)
        completed_at = restaurant.onboarding_completed_at
        # Surface the stored config snippets so the wizard can resume with
        # whatever the user already saved. These are stored on
        # ``general_settings`` to avoid an extra migration.
        gs = restaurant.general_settings or {}
        return {
            'restaurant_id': str(restaurant.id),
            'completed': bool(completed_at),
            'completed_at': completed_at.isoformat() if completed_at else None,
            'steps': state,
            'order': list(ONBOARDING_STEPS),
            'required_steps': sorted(REQUIRED_STEPS),
            'optional_steps': [
                s for s in ONBOARDING_STEPS if s not in REQUIRED_STEPS
            ],
            'next_step': next(
                (s for s in ONBOARDING_STEPS if not state.get(s)), None
            ),
            'config': {
                'widget_role_visibility': gs.get('widget_role_visibility') or {},
                'category_owners': gs.get('category_owners') or {},
                'google_calendar': gs.get('google_calendar') or {
                    'connected': False,
                    'email': None,
                    'skipped': False,
                },
            },
        }

    def get(self, request):
        restaurant = getattr(request.user, 'restaurant', None)
        if not restaurant:
            return Response(
                {'detail': 'No restaurant associated with user.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return Response(self._payload(restaurant))

    def post(self, request):
        """Mark a specific step complete (or reset the flow)."""
        if not _is_owner_like(request.user):
            return Response(
                {'detail': 'Only owners/admins can update onboarding.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        restaurant = getattr(request.user, 'restaurant', None)
        if not restaurant:
            return Response(
                {'detail': 'No restaurant associated with user.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if bool(request.data.get('complete_onboarding')):
            restaurant.onboarding_completed_at = timezone.now()
            restaurant.save(update_fields=['onboarding_completed_at'])
            return Response(self._payload(restaurant))

        step = str(request.data.get('step') or '').strip().lower()
        reset = bool(request.data.get('reset'))

        state = _merge_state(restaurant.onboarding_state)

        if reset:
            restaurant.onboarding_state = _default_state()
            restaurant.onboarding_completed_at = None
            restaurant.save(
                update_fields=['onboarding_state', 'onboarding_completed_at']
            )
            return Response(self._payload(restaurant))

        if step not in ONBOARDING_STEPS:
            return Response(
                {
                    'detail': 'Invalid step.',
                    'allowed': list(ONBOARDING_STEPS),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        state[step] = True
        restaurant.onboarding_state = state
        # Completion is based on REQUIRED steps only — google_calendar is
        # optional and may stay False forever without blocking access.
        if all(state.get(s) for s in REQUIRED_STEPS):
            restaurant.onboarding_completed_at = timezone.now()
            restaurant.save(
                update_fields=['onboarding_state', 'onboarding_completed_at']
            )
        else:
            restaurant.save(update_fields=['onboarding_state'])

        return Response(self._payload(restaurant))


class OnboardingSeedView(APIView):
    """Seed a minimal viable setup so the wizard has real defaults to offer.

    Runs idempotently: re-calling does not create duplicates. Returns a small
    summary of what was touched. This is intentionally conservative — we seed
    one primary branch, one weekly template with day-0 open/close shifts, a
    single opening-shift checklist, and a small sample menu. Tenants tweak
    further from the normal CRUD UIs after the wizard.
    """

    permission_classes = [permissions.IsAuthenticated]

    @transaction.atomic
    def post(self, request):
        if not _is_owner_like(request.user):
            return Response(
                {'detail': 'Only owners/admins can seed onboarding data.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        restaurant = getattr(request.user, 'restaurant', None)
        if not restaurant:
            return Response(
                {'detail': 'No restaurant associated with user.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        created: dict[str, bool] = {}

        primary = BusinessLocation.objects.filter(
            restaurant=restaurant, is_primary=True
        ).first()
        if not primary:
            primary = BusinessLocation.objects.create(
                restaurant=restaurant,
                name=restaurant.name or 'Main branch',
                address=restaurant.address or '',
                latitude=restaurant.latitude,
                longitude=restaurant.longitude,
                radius=restaurant.radius or 100,
                geofence_enabled=bool(restaurant.geofence_enabled),
                is_primary=True,
                is_active=True,
            )
            created['branch'] = True
        else:
            created['branch'] = False

        # Schedule template. Imported lazily to avoid circular imports at
        # app-config time; scheduling depends on accounts.
        try:
            from scheduling.models import ScheduleTemplate, TemplateShift

            template, tmpl_created = ScheduleTemplate.objects.get_or_create(
                restaurant=restaurant,
                name='Default weekly template',
                defaults={
                    'description': 'Seeded by onboarding wizard. Edit freely.',
                    'is_active': True,
                },
            )
            if tmpl_created:
                # Monday opener + closer as a minimal shape; other days can
                # be cloned from the UI. Use roles that exist in the
                # STAFF_ROLES_CHOICES catalogue.
                TemplateShift.objects.get_or_create(
                    template=template,
                    role='WAITER',
                    day_of_week=0,
                    defaults={
                        'start_time': dt_time(9, 0),
                        'end_time': dt_time(17, 0),
                        'required_staff': 2,
                    },
                )
                TemplateShift.objects.get_or_create(
                    template=template,
                    role='CHEF',
                    day_of_week=0,
                    defaults={
                        'start_time': dt_time(10, 0),
                        'end_time': dt_time(22, 0),
                        'required_staff': 1,
                    },
                )
            created['shift_template'] = tmpl_created
        except Exception:  # scheduling app unavailable; skip silently
            created['shift_template'] = False

        try:
            from checklists.models import ChecklistStep, ChecklistTemplate

            checklist, ck_created = ChecklistTemplate.objects.get_or_create(
                restaurant=restaurant,
                name='Opening shift checklist',
                defaults={
                    'description': 'Seeded starter checklist. Edit in Checklists.',
                    'category': 'Opening',
                    'is_active': True,
                    'created_by': request.user,
                },
            )
            if ck_created:
                for idx, title in enumerate(
                    [
                        'Unlock and walk through the dining room',
                        'Check fridge and freezer temperatures',
                        'Wipe down tables and sanitise surfaces',
                        'Count the cash float in the register',
                        'Turn on POS, printer, and music',
                    ],
                    start=1,
                ):
                    ChecklistStep.objects.get_or_create(
                        template=checklist,
                        order=idx,
                        defaults={
                            'title': title,
                            'step_type': 'CHECK',
                            'is_required': True,
                        },
                    )
            created['checklist'] = ck_created
        except Exception:
            created['checklist'] = False

        try:
            from menu.models import MenuCategory, MenuItem

            category, cat_created = MenuCategory.objects.get_or_create(
                restaurant=restaurant,
                name='Starters',
                defaults={'display_order': 0, 'is_active': True},
            )
            if cat_created:
                MenuItem.objects.get_or_create(
                    restaurant=restaurant,
                    name='House salad',
                    defaults={
                        'category': category,
                        'price': 8.00,
                        'is_active': True,
                        'description': 'Seeded sample — rename or delete.',
                    },
                )
            created['menu'] = cat_created
        except Exception:
            created['menu'] = False

        # Seed side-effects are now decoupled from wizard step completion:
        # seeding a branch/template/menu is a convenience primitive, not a
        # wizard step. The current wizard tracks staff_csv / widgets /
        # widget_permissions / category_owners / google_calendar instead.
        return Response(
            {
                'created': created,
                'state': _merge_state(restaurant.onboarding_state),
                'completed': bool(restaurant.onboarding_completed_at),
            }
        )


def _mark_step(restaurant: Restaurant, step: str) -> None:
    """Mark a single onboarding step complete and set the completion timestamp
    when all REQUIRED steps are done. Safe to call from multiple endpoints."""
    state = _merge_state(restaurant.onboarding_state)
    state[step] = True
    restaurant.onboarding_state = state
    update_fields = ['onboarding_state']
    if all(state.get(s) for s in REQUIRED_STEPS) and not restaurant.onboarding_completed_at:
        restaurant.onboarding_completed_at = timezone.now()
        update_fields.append('onboarding_completed_at')
    restaurant.save(update_fields=update_fields)


# -----------------------------------------------------------------------------
# Onboarding config endpoints (widgets, permissions, owners, calendar)
# -----------------------------------------------------------------------------
# All three of these endpoints write into ``Restaurant.general_settings`` (a
# JSONField that already exists) rather than creating new columns. This keeps
# the wizard additive and migration-free.
#
# The wizard steps themselves are marked complete automatically by the SAME
# request that saves the config — the manager doesn't have to click "I'm done"
# after clicking "Save".


class OnboardingWidgetVisibilityView(APIView):
    """Per-widget role visibility — which roles can see each widget.

    Storage shape in ``Restaurant.general_settings['widget_role_visibility']``:

        {
            "tasks_demands": ["SUPER_ADMIN", "OWNER", "ADMIN", "MANAGER"],
            "staffing":      ["SUPER_ADMIN", "OWNER", "ADMIN"],
            ...
        }

    An empty list means "no one extra beyond what RBAC allows"; a non-empty
    list means "EXACTLY these roles, plus anyone with explicit widget
    override permissions". This is read by ``usePermissions().canWidget()``
    on the frontend via an augmented ``/auth/session/``-style payload.
    """

    permission_classes = [permissions.IsAuthenticated]

    _ALLOWED_ROLES = {
        'SUPER_ADMIN', 'OWNER', 'ADMIN', 'MANAGER',
        'STAFF', 'CHEF', 'WAITER', 'CASHIER', 'ACCOUNTANT',
    }

    def put(self, request):
        if not _is_owner_like(request.user):
            return Response(
                {'detail': 'Only owners/admins can configure widgets.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        restaurant = getattr(request.user, 'restaurant', None)
        if not restaurant:
            return Response(
                {'detail': 'No restaurant associated with user.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = request.data.get('visibility')
        if not isinstance(payload, dict):
            return Response(
                {'detail': 'Body must be {"visibility": {widget_id: [roles]}}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        clean: dict[str, list[str]] = {}
        for widget_id, roles in payload.items():
            if not isinstance(widget_id, str) or not widget_id.strip():
                continue
            if not isinstance(roles, list):
                continue
            clean_roles = [
                str(r).strip().upper()
                for r in roles
                if isinstance(r, str) and str(r).strip().upper() in self._ALLOWED_ROLES
            ]
            # Deduplicate, preserve order.
            seen: set[str] = set()
            clean[widget_id.strip()] = [
                r for r in clean_roles if not (r in seen or seen.add(r))
            ]

        gs = dict(restaurant.general_settings or {})
        gs['widget_role_visibility'] = clean
        restaurant.general_settings = gs
        restaurant.save(update_fields=['general_settings'])

        _mark_step(restaurant, 'widget_permissions')
        # Also mark the 'widgets' step as done on first save, since selecting
        # which roles see each widget implies you've decided which widgets
        # exist. The wizard still walks through both steps for clarity.
        _mark_step(restaurant, 'widgets')

        return Response({
            'saved': True,
            'visibility': clean,
        })


class OnboardingCategoryOwnersView(APIView):
    """Which staff member is the default owner for each category.

    Categories cover incidents, staff-request categories, and dashboard-task
    "departments" — one unified map so the wizard can collect everything in
    one step.

    Storage shape in ``Restaurant.general_settings['category_owners']``:

        {
            # Safety / incident routing (consumed by staff.incident_routing)
            "incident.equipment":   "<user-uuid>",
            "incident.safety":      "<user-uuid>",
            "incident.hr":          "<user-uuid>",
            "incident.customer":    "<user-uuid>",
            "incident.security":    "<user-uuid>",
            "incident.quality":     "<user-uuid>",

            # Intelligent inbox routing (consumed by staff.request_routing) —
            # Miya uses these when auto-assigning WhatsApp requests.
            "request.payroll":      "<user-uuid>",
            "request.scheduling":   "<user-uuid>",
            "request.hr":           "<user-uuid>",
            "request.document":     "<user-uuid>",
            "request.maintenance":  "<user-uuid>",
            "request.reservations": "<user-uuid>",
            "request.inventory":    "<user-uuid>",

            # Department ownership (used by tasks)
            "task.foh":             "<user-uuid>",
            "task.boh":             "<user-uuid>",
            "task.bar":             "<user-uuid>",
            "task.finance":         "<user-uuid>"
        }

    For backwards compatibility we ALSO write the ``incident.*`` subset into
    ``general_settings['incident_category_assignees']`` (keyed by the human
    category label) so ``staff.incident_routing.resolve_default_assignee_for_incident_type``
    keeps working unchanged.
    """

    permission_classes = [permissions.IsAuthenticated]

    # Map of incident slug → human label used by incident_routing.
    _INCIDENT_LABELS = {
        'incident.equipment': 'Equipment Failure',
        'incident.safety': 'Safety',
        'incident.hr': 'HR',
        'incident.customer': 'Customer Issue',
        'incident.security': 'Security',
        'incident.quality': 'Food Quality',
        'incident.other': 'Other',
    }

    def put(self, request):
        if not _is_owner_like(request.user):
            return Response(
                {'detail': 'Only owners/admins can configure ownership.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        restaurant = getattr(request.user, 'restaurant', None)
        if not restaurant:
            return Response(
                {'detail': 'No restaurant associated with user.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = request.data.get('owners')
        if not isinstance(payload, dict):
            return Response(
                {'detail': 'Body must be {"owners": {category: user_uuid}}.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Validate each user_uuid belongs to this tenant.
        from accounts.models import CustomUser

        clean: dict[str, str] = {}
        for cat, uid in payload.items():
            if not isinstance(cat, str) or not cat.strip():
                continue
            if not uid:
                continue
            try:
                user = CustomUser.objects.only('id', 'restaurant_id').get(id=str(uid))
            except (CustomUser.DoesNotExist, ValueError):
                continue
            if user.restaurant_id != restaurant.id:
                continue
            clean[cat.strip()] = str(user.id)

        gs = dict(restaurant.general_settings or {})
        gs['category_owners'] = clean

        # Also mirror into the legacy incident_category_assignees map so the
        # existing incident router keeps working.
        legacy_incident: dict[str, str] = dict(
            gs.get('incident_category_assignees') or {}
        )
        for slug, uid in clean.items():
            label = self._INCIDENT_LABELS.get(slug)
            if label:
                legacy_incident[label] = uid
        gs['incident_category_assignees'] = legacy_incident

        restaurant.general_settings = gs
        restaurant.save(update_fields=['general_settings'])

        _mark_step(restaurant, 'category_owners')

        return Response({
            'saved': True,
            'owners': clean,
        })


class OnboardingGoogleCalendarView(APIView):
    """Google Calendar connect / skip / disconnect for the onboarding wizard.

    End-to-end flow:
      1. Frontend POSTs ``{"action": "connect"}`` — we generate a signed
         ``state`` token embedding the restaurant id + user id, then
         return the Google OAuth consent URL.
      2. User approves on Google; Google redirects to our callback
         (``GoogleCalendarOAuthCallbackView``) with ``code`` + ``state``.
      3. We exchange the code for access + refresh tokens, fetch the
         user's Google email, persist everything on the Restaurant, and
         mark the ``google_calendar`` step complete.
      4. We redirect the browser back to the frontend wizard with a
         success flag so the wizard advances to Done.

    Set ``GOOGLE_OAUTH_CLIENT_ID`` and ``GOOGLE_OAUTH_CLIENT_SECRET`` in
    env to enable. If missing, the connect endpoint returns 501 so the
    wizard surfaces a friendly "ask support" note while Skip still works.
    """

    permission_classes = [permissions.IsAuthenticated]

    # Calendar read/write + openid/email for identification.
    _SCOPES = ' '.join([
        'openid',
        'email',
        'profile',
        'https://www.googleapis.com/auth/calendar.events',
    ])

    @staticmethod
    def _creds() -> tuple[str | None, str | None]:
        import os
        return (
            os.environ.get('GOOGLE_OAUTH_CLIENT_ID'),
            os.environ.get('GOOGLE_OAUTH_CLIENT_SECRET'),
        )

    @staticmethod
    def _redirect_uri(request) -> str:
        # Use the BACKEND origin for the redirect — Google must hit our
        # callback view, which then 302s the browser back to the frontend.
        return request.build_absolute_uri(
            '/api/integrations/google-calendar/callback/'
        )

    def post(self, request):
        if not _is_owner_like(request.user):
            return Response(
                {'detail': 'Only owners/admins can configure integrations.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        restaurant = getattr(request.user, 'restaurant', None)
        if not restaurant:
            return Response(
                {'detail': 'No restaurant associated with user.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        action = str(request.data.get('action') or '').strip().lower()
        if action not in {'skip', 'connect', 'disconnect'}:
            return Response(
                {'detail': 'action must be one of: skip, connect, disconnect.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        client_id, client_secret = self._creds()
        configured = bool(client_id and client_secret)

        gs = dict(restaurant.general_settings or {})
        gcal = dict(gs.get('google_calendar') or {})

        if action == 'skip':
            gcal['skipped'] = True
            gcal['skipped_at'] = timezone.now().isoformat()
        elif action == 'disconnect':
            gcal = {'connected': False, 'email': None, 'skipped': False}
        elif action == 'connect':
            if not configured:
                # Keep the user-facing copy short and free of internal
                # environment variable names — ops operators can see
                # the real cause in server logs.
                return Response(
                    {
                        'detail': (
                            'Google Calendar is not available right now. '
                            'Please try again later or contact your '
                            'administrator.'
                        ),
                        'configured': False,
                    },
                    status=status.HTTP_501_NOT_IMPLEMENTED,
                )

            # Signed, short-lived state token binds the OAuth redirect
            # back to this exact (user, restaurant) pair — protects
            # against CSRF and lets the callback find the right tenant
            # without reading session cookies.
            from django.core import signing

            # Optional caller-supplied return path so we can route the
            # browser back to wherever the connect button was clicked
            # (e.g. /dashboard when the Meetings & Reminders widget
            # kicks off the flow). Validated in the callback — we only
            # accept same-origin absolute paths to rule out open-redirect.
            return_to = str(request.data.get('return_to') or '').strip()
            if return_to and not return_to.startswith('/'):
                return_to = ''

            state = signing.dumps(
                {
                    'user_id': str(request.user.id),
                    'restaurant_id': str(restaurant.id),
                    'ts': timezone.now().isoformat(),
                    'return_to': return_to,
                },
                salt='google-calendar-oauth',
            )

            from urllib.parse import urlencode

            params = {
                'client_id': client_id,
                'response_type': 'code',
                'access_type': 'offline',
                'prompt': 'consent',
                'include_granted_scopes': 'true',
                'scope': self._SCOPES,
                'redirect_uri': self._redirect_uri(request),
                'state': state,
            }
            redirect = (
                'https://accounts.google.com/o/oauth2/v2/auth?'
                + urlencode(params)
            )
            return Response(
                {
                    'configured': True,
                    'redirect_url': redirect,
                    'saved': False,
                }
            )

        # skip / disconnect only reach here.
        gs['google_calendar'] = gcal
        restaurant.general_settings = gs
        restaurant.save(update_fields=['general_settings'])

        _mark_step(restaurant, 'google_calendar')

        return Response({
            'saved': True,
            'configured': configured,
            'google_calendar': gcal,
        })


class GoogleCalendarOAuthCallbackView(APIView):
    """Handles the redirect from Google after the user consents.

    Lives under ``/api/integrations/google-calendar/callback/`` — this is the
    ``redirect_uri`` we register with Google. The view:
      1. Validates the signed ``state`` token (binds to user + restaurant).
      2. Exchanges ``code`` for access + refresh tokens.
      3. Fetches the user's Google email.
      4. Stores tokens + email on ``Restaurant.general_settings['google_calendar']``.
      5. Marks the ``google_calendar`` step complete.
      6. 302-redirects the browser back to the frontend wizard with a
         ``?gcal=connected`` (or ``?gcal=error``) query flag.
    """

    # Google calls this endpoint from the user's browser — no JWT.
    permission_classes = [permissions.AllowAny]
    authentication_classes: list = []

    def _frontend_url(
        self,
        flag: str,
        detail: str = '',
        return_to: str = '',
    ) -> str:
        """Build the redirect back to the frontend.

        Defaults to ``/onboarding`` (the Google Calendar step) so callers
        that don't pass a ``return_to`` keep the original wizard UX.
        When the widget on ``/dashboard`` initiates the flow it sets
        ``return_to=/dashboard`` and we send the user back to exactly
        where they were, with ``?gcal=connected|error`` appended so the
        UI can show a toast and invalidate the calendar query.
        """
        from django.conf import settings as dj_settings
        from urllib.parse import urlencode, urlsplit, urlunsplit

        base = getattr(dj_settings, 'FRONTEND_URL', 'http://localhost:8080')

        # Only same-origin absolute paths are allowed so we don't
        # accidentally 302 users to an attacker-controlled domain via a
        # crafted state token.
        target_path = '/onboarding'
        if return_to and return_to.startswith('/') and not return_to.startswith('//'):
            target_path = return_to

        # Merge any existing query from the return_to path with our gcal
        # flag params so we don't clobber caller query state.
        parts = urlsplit(target_path)
        existing = parts.query
        extra = {'gcal': flag}
        if detail:
            extra['gcal_detail'] = detail[:200]
        merged_query = (existing + ('&' if existing else '') + urlencode(extra))
        return f'{base.rstrip("/")}' + urlunsplit((
            '', '', parts.path or '/', merged_query, parts.fragment,
        ))

    # Backwards-compatible alias — several helpers call this name.
    def _wizard_url(self, flag: str, detail: str = '') -> str:
        return self._frontend_url(flag, detail)

    def get(self, request):
        from django.core import signing
        from django.shortcuts import redirect as http_redirect

        error = request.GET.get('error')
        if error:
            return http_redirect(self._wizard_url('error', error))

        code = request.GET.get('code')
        state = request.GET.get('state')
        if not code or not state:
            return http_redirect(self._wizard_url('error', 'missing_code'))

        # Verify and unpack the state token (10-minute validity).
        try:
            payload = signing.loads(
                state,
                salt='google-calendar-oauth',
                max_age=600,
            )
        except signing.BadSignature:
            return http_redirect(self._wizard_url('error', 'bad_state'))

        restaurant_id = payload.get('restaurant_id')
        # The ``return_to`` field is opaque to the callback — we just
        # pass it through to ``_frontend_url`` which rejects anything
        # that doesn't look like a same-origin absolute path.
        return_to = str(payload.get('return_to') or '')
        if not restaurant_id:
            return http_redirect(
                self._frontend_url('error', 'bad_state', return_to=return_to)
            )

        try:
            restaurant = Restaurant.objects.get(id=restaurant_id)
        except Restaurant.DoesNotExist:
            return http_redirect(
                self._frontend_url('error', 'no_restaurant', return_to=return_to)
            )

        client_id, client_secret = OnboardingGoogleCalendarView._creds()
        if not (client_id and client_secret):
            return http_redirect(
                self._frontend_url('error', 'not_configured', return_to=return_to)
            )

        redirect_uri = OnboardingGoogleCalendarView._redirect_uri(request)

        # Exchange the authorization code for tokens.
        import requests

        try:
            token_res = requests.post(
                'https://oauth2.googleapis.com/token',
                data={
                    'code': code,
                    'client_id': client_id,
                    'client_secret': client_secret,
                    'redirect_uri': redirect_uri,
                    'grant_type': 'authorization_code',
                },
                timeout=10,
            )
        except requests.RequestException as exc:
            return http_redirect(
                self._frontend_url(
                    'error', f'token_request_failed:{exc}', return_to=return_to,
                )
            )

        if token_res.status_code != 200:
            return http_redirect(
                self._frontend_url(
                    'error',
                    f'token_exchange_{token_res.status_code}',
                    return_to=return_to,
                )
            )

        tokens = token_res.json() or {}
        access_token = tokens.get('access_token')
        refresh_token = tokens.get('refresh_token')
        expires_in = int(tokens.get('expires_in') or 3600)
        scope = tokens.get('scope') or OnboardingGoogleCalendarView._SCOPES

        if not access_token:
            return http_redirect(
                self._frontend_url('error', 'no_access_token', return_to=return_to)
            )

        # Fetch the Google identity to display (and confirm the grant).
        email = None
        try:
            ui_res = requests.get(
                'https://www.googleapis.com/oauth2/v2/userinfo',
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=10,
            )
            if ui_res.status_code == 200:
                email = (ui_res.json() or {}).get('email')
        except requests.RequestException:
            email = None

        # Persist on restaurant.general_settings — note that tokens are in
        # plaintext JSON for this MVP; moving them into the encrypted
        # ``pos_oauth_data``-style TextField is a follow-up hardening.
        gs = dict(restaurant.general_settings or {})
        gcal = {
            'connected': True,
            'email': email,
            'skipped': False,
            'connected_at': timezone.now().isoformat(),
            'scope': scope,
            'access_token': access_token,
            'refresh_token': refresh_token,
            'token_expires_at': (
                timezone.now() + timezone.timedelta(seconds=expires_in)
            ).isoformat(),
        }
        gs['google_calendar'] = gcal
        restaurant.general_settings = gs
        restaurant.save(update_fields=['general_settings'])

        _mark_step(restaurant, 'google_calendar')

        return http_redirect(
            self._frontend_url('connected', return_to=return_to)
        )


class _AuditPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 200


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def audit_log_list(request):
    """Tenant-scoped list of AuditLog rows for the activity-log UI.

    Filters (all optional query params):
        * ``action_type`` — exact match on AuditLog.ACTION_TYPES (repeatable).
        * ``entity_type`` — exact match (repeatable).
        * ``user_id`` — UUID of the actor.
        * ``q`` — substring over description / user email.
        * ``since`` / ``until`` — ISO-8601 timestamps.
    """
    user = request.user
    restaurant = getattr(user, 'restaurant', None)
    role = str(getattr(user, 'role', '') or '').upper()

    if role not in {'SUPER_ADMIN', 'OWNER', 'ADMIN', 'MANAGER'}:
        return Response(
            {'detail': 'Managers, admins, or owners only.'},
            status=status.HTTP_403_FORBIDDEN,
        )

    qs = AuditLog.objects.select_related('user', 'target_user', 'restaurant')

    if user.is_superuser and not restaurant:
        pass  # full visibility for system admins with no tenant attached
    elif restaurant:
        qs = qs.filter(restaurant=restaurant)
    else:
        return Response({'results': [], 'count': 0})

    action_types = request.GET.getlist('action_type')
    if action_types:
        qs = qs.filter(action_type__in=action_types)

    entity_types = request.GET.getlist('entity_type')
    if entity_types:
        qs = qs.filter(entity_type__in=[e.upper() for e in entity_types])

    actor = request.GET.get('user_id')
    if actor:
        qs = qs.filter(user_id=actor)

    # ``target_user_id`` answers "who was this assigned to / directed at?"
    target = request.GET.get('target_user_id')
    if target:
        qs = qs.filter(target_user_id=target)

    entity_id = request.GET.get('entity_id')
    if entity_id:
        qs = qs.filter(entity_id=entity_id)

    q = (request.GET.get('q') or '').strip()
    if q:
        from django.db.models import Q

        qs = qs.filter(
            Q(description__icontains=q)
            | Q(user__email__icontains=q)
            | Q(user__first_name__icontains=q)
            | Q(user__last_name__icontains=q)
            | Q(target_user__email__icontains=q)
            | Q(target_user__first_name__icontains=q)
            | Q(target_user__last_name__icontains=q)
        )

    since = request.GET.get('since')
    if since:
        qs = qs.filter(timestamp__gte=since)
    until = request.GET.get('until')
    if until:
        qs = qs.filter(timestamp__lte=until)

    paginator = _AuditPagination()
    page = paginator.paginate_queryset(qs.order_by('-timestamp'), request)

    return paginator.get_paginated_response(
        [serialize_audit_entry(entry) for entry in page]
    )


def serialize_audit_entry(entry: AuditLog) -> dict:
    """Shared serializer used by both the UI list endpoint and the Miya
    activity-log agent endpoint. Keeps the wire format in one place.
    """
    def _user_block(u):
        if not u:
            return None
        return {
            'id': str(u.id),
            'email': u.email,
            'name': (
                f'{u.first_name} {u.last_name}'.strip()
                or u.email
            ),
            'role': getattr(u, 'role', None),
        }

    return {
        'id': str(entry.id),
        'timestamp': entry.timestamp.isoformat() if entry.timestamp else None,
        'action_type': entry.action_type,
        'action_label': entry.get_action_type_display(),
        'entity_type': entry.entity_type,
        'entity_id': entry.entity_id,
        'description': entry.description,
        'ip_address': entry.ip_address,
        'user': _user_block(entry.user),
        'target_user': _user_block(entry.target_user),
        'metadata': entry.metadata or {},
    }
