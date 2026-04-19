"""
Manager Action Center API.
Aggregates: pending staff requests, failed invites, checklist rejections, incidents.
"""
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions, status
from django.utils import timezone

from datetime import timedelta

from staff.models import StaffRequest
from accounts.models import UserInvitation, InvitationDeliveryLog, StaffActivationRecord
from checklists.models import ChecklistExecution
from staff.models_task import SafetyConcernReport
from timeclock.models import ClockEvent


def _staff_name(u):
    if not u:
        return "Unknown"
    return f"{getattr(u, 'first_name', '') or ''} {getattr(u, 'last_name', '') or ''}".strip() or (getattr(u, 'email', '') or 'Staff')


class ActionCenterView(APIView):
    """Manager action center: what needs attention."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        restaurant = getattr(user, 'restaurant', None)
        if not restaurant:
            return Response({"error": "No restaurant"}, status=status.HTTP_400_BAD_REQUEST)

        allowed_roles = {'SUPER_ADMIN', 'ADMIN', 'OWNER', 'MANAGER'}
        if str(getattr(user, 'role', '')).upper() not in allowed_roles:
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        now = timezone.now()

        # 1. Pending staff requests
        pending_requests = StaffRequest.objects.filter(
            restaurant=restaurant,
            status='PENDING'
        ).order_by('-created_at')[:10].select_related('staff')
        staff_requests = [
            {
                "id": str(r.id),
                "type": "staff_request",
                "subject": r.subject,
                "description": (r.description or "")[:100],
                "staff_name": _staff_name(r.staff) or r.staff_name,
                "category": r.category,
                "priority": r.priority,
                "created_at": r.created_at.isoformat(),
                "action_url": f"/staff/requests/{r.id}",
            }
            for r in pending_requests
        ]

        # 2. Failed WhatsApp invites (delivery log)
        failed_invites = InvitationDeliveryLog.objects.filter(
            invitation__restaurant=restaurant,
            channel='whatsapp',
            status='FAILED'
        ).order_by('-sent_at')[:10].select_related('invitation')
        failed_invites_list = [
            {
                "id": str(log.id),
                "type": "failed_invite",
                "recipient": log.recipient_address,
                "error_message": (log.error_message or "")[:80],
                "invitation_id": str(log.invitation_id),
                "sent_at": log.sent_at.isoformat() if log.sent_at else None,
                "action_url": "/staff/invitations",
            }
            for log in failed_invites
        ]

        # 3. Pending activation (ONE-TAP)
        pending_activations = StaffActivationRecord.objects.filter(
            restaurant=restaurant,
            status=StaffActivationRecord.STATUS_NOT_ACTIVATED
        ).order_by('-created_at')[:10]
        pending_activations_list = [
            {
                "id": str(r.id),
                "type": "pending_activation",
                "phone": r.phone,
                "name": f"{r.first_name} {r.last_name}".strip() or "—",
                "role": r.role,
                "created_at": r.created_at.isoformat(),
                "action_url": "/staff/team",
            }
            for r in pending_activations
        ]

        # 4. Checklist executions needing manager review (completed but not approved)
        checklist_reviews = ChecklistExecution.objects.filter(
            template__restaurant=restaurant,
            status='COMPLETED',
            supervisor_approved=False
        ).order_by('-completed_at')[:10].select_related('assigned_to', 'template')
        checklist_reviews_list = [
            {
                "id": str(e.id),
                "type": "checklist_review",
                "template_name": e.template.name if e.template else "—",
                "staff_name": _staff_name(e.assigned_to),
                "completed_at": e.completed_at.isoformat() if e.completed_at else None,
                "action_url": f"/dashboard/checklists/review/{e.id}",
            }
            for e in checklist_reviews
        ]

        # 5. Open incidents (safety concerns)
        open_incidents = SafetyConcernReport.objects.filter(
            restaurant=restaurant,
            status__in=['OPEN'],
            severity__in=['HIGH', 'CRITICAL']
        ).order_by('-created_at')[:10].select_related('reporter')
        incidents_list = [
            {
                "id": str(r.id),
                "type": "incident",
                "title": r.title,
                "incident_type": r.incident_type,
                "severity": r.severity,
                "reporter": _staff_name(r.reporter),
                "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
                "action_url": "/dashboard/safety",
            }
            for r in open_incidents
        ]

        # 6. Recent location mismatches (staff clocked in at a branch outside
        # their allowed locations). Only show events from the last 48h so the
        # widget stays actionable rather than historical.
        mismatch_cutoff = now - timedelta(hours=48)
        mismatch_qs = (
            ClockEvent.objects.filter(
                staff__restaurant=restaurant,
                event_type='in',
                location_mismatch=True,
                timestamp__gte=mismatch_cutoff,
            )
            .select_related('staff', 'location')
            .order_by('-timestamp')
        )
        # Scope managers to branches they're assigned to manage.
        if str(getattr(user, 'role', '')).upper() == 'MANAGER':
            managed_ids = list(user.managed_locations.values_list('id', flat=True))
            if managed_ids:
                mismatch_qs = mismatch_qs.filter(location_id__in=managed_ids)
        mismatch_qs = mismatch_qs[:10]
        mismatches_list = [
            {
                "id": str(ev.id),
                "type": "location_mismatch",
                "staff_name": _staff_name(ev.staff),
                "branch_name": getattr(ev.location, 'name', None) or "Unknown branch",
                "branch_id": str(ev.location_id) if ev.location_id else None,
                "timestamp": ev.timestamp.isoformat() if ev.timestamp else None,
                "action_url": "/dashboard/attendance",
            }
            for ev in mismatch_qs
        ]

        total_count = (
            len(staff_requests) +
            len(failed_invites_list) +
            len(pending_activations_list) +
            len(checklist_reviews_list) +
            len(incidents_list) +
            len(mismatches_list)
        )

        return Response({
            "items": (
                staff_requests +
                failed_invites_list +
                pending_activations_list +
                checklist_reviews_list +
                incidents_list +
                mismatches_list
            ),
            "counts": {
                "staff_requests": len(staff_requests),
                "failed_invites": len(failed_invites_list),
                "pending_activations": len(pending_activations_list),
                "checklist_reviews": len(checklist_reviews_list),
                "incidents": len(incidents_list),
                "location_mismatches": len(mismatches_list),
            },
            "total": total_count,
            "timestamp": now.isoformat(),
        })
