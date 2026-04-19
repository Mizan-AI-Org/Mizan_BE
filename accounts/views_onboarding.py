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


ONBOARDING_STEPS: tuple[str, ...] = (
    'branch',
    'shift_template',
    'checklist',
    'menu',
)


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
        return {
            'restaurant_id': str(restaurant.id),
            'completed': bool(completed_at),
            'completed_at': completed_at.isoformat() if completed_at else None,
            'steps': state,
            'order': list(ONBOARDING_STEPS),
            'next_step': next(
                (s for s in ONBOARDING_STEPS if not state.get(s)), None
            ),
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
        if all(state[s] for s in ONBOARDING_STEPS):
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

        state = _merge_state(restaurant.onboarding_state)
        for step, did in created.items():
            if did:
                state[step] = True
        restaurant.onboarding_state = state
        if all(state[s] for s in ONBOARDING_STEPS):
            restaurant.onboarding_completed_at = timezone.now()
            restaurant.save(
                update_fields=['onboarding_state', 'onboarding_completed_at']
            )
        else:
            restaurant.save(update_fields=['onboarding_state'])

        return Response(
            {
                'created': created,
                'state': state,
                'completed': bool(restaurant.onboarding_completed_at),
            }
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

    qs = AuditLog.objects.select_related('user', 'restaurant')

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

    q = (request.GET.get('q') or '').strip()
    if q:
        from django.db.models import Q

        qs = qs.filter(Q(description__icontains=q) | Q(user__email__icontains=q))

    since = request.GET.get('since')
    if since:
        qs = qs.filter(timestamp__gte=since)
    until = request.GET.get('until')
    if until:
        qs = qs.filter(timestamp__lte=until)

    paginator = _AuditPagination()
    page = paginator.paginate_queryset(qs.order_by('-timestamp'), request)

    def _row(entry: AuditLog) -> dict:
        actor = entry.user
        return {
            'id': str(entry.id),
            'timestamp': entry.timestamp.isoformat() if entry.timestamp else None,
            'action_type': entry.action_type,
            'action_label': entry.get_action_type_display(),
            'entity_type': entry.entity_type,
            'entity_id': entry.entity_id,
            'description': entry.description,
            'ip_address': entry.ip_address,
            'user': (
                {
                    'id': str(actor.id),
                    'email': actor.email,
                    'name': (
                        f'{actor.first_name} {actor.last_name}'.strip()
                        or actor.email
                    ),
                    'role': actor.role,
                }
                if actor
                else None
            ),
        }

    return paginator.get_paginated_response([_row(entry) for entry in page])
