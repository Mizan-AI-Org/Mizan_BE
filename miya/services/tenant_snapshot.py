"""Compact tenant facts injected into Miya's system prompt each turn."""

from __future__ import annotations

from typing import Any

from django.utils import timezone


def _urgency_label(urgency: str, days_left: int | None) -> str:
    if urgency == "expired":
        return "expired"
    if urgency == "critical" and days_left is not None:
        return f"due this week ({days_left} days left)"
    if urgency == "soon" and days_left is not None:
        return f"due soon ({days_left} days left)"
    if urgency == "unset":
        return "no expiry date set"
    if days_left is not None:
        return f"{days_left} days left"
    return urgency or "ok"


def build_tenant_snapshot_block(restaurant) -> str:
    """Load live tenant data Miya should treat as authoritative for this turn."""
    if restaurant is None:
        return ""

    lines: list[str] = []
    today = timezone.now().date()

    try:
        from payroll.models import ComplianceDocument
        from payroll.services.compliance_documents import serialize_document

        docs = list(
            ComplianceDocument.objects.filter(
                restaurant=restaurant,
                status=ComplianceDocument.STATUS_ACTIVE,
            ).order_by("expires_at", "title")[:25]
        )
        if docs:
            lines.append("Compliance documents (use these ids with update_compliance_document):")
            for doc in docs:
                row = serialize_document(doc)
                label = _urgency_label(row.get("urgency") or "", row.get("days_until_expiry"))
                expiry = row.get("expires_at") or "not set"
                lines.append(
                    f"  • {row['title']} (id={row['id']}, type={row['document_type']}): "
                    f"expires {expiry}, {label}"
                )
        else:
            lines.append("Compliance documents: none yet (seed_compliance_documents to add starters).")
    except Exception:
        lines.append("Compliance documents: unavailable this turn.")

    try:
        from accounts.models import BusinessLocation

        branches = list(
            BusinessLocation.objects.filter(restaurant=restaurant, is_active=True)
            .order_by("-is_primary", "name")
            .values("id", "name", "is_primary")[:20]
        )
        if branches:
            lines.append("Branches (use location_name or location_id with location_detail / cross_location_report):")
            for b in branches:
                tag = "primary" if b.get("is_primary") else "branch"
                lines.append(f"  • {b['name']} (id={b['id']}, {tag})")
    except Exception:
        pass

    try:
        from accounts.models import CustomUser

        staff_count = CustomUser.objects.filter(
            restaurant=restaurant,
            is_active=True,
        ).exclude(role__in={"ADMIN", "SUPER_ADMIN", "PLATFORM_ADMIN"}).count()
        lines.append(f"Active staff on roster: {staff_count}.")
    except Exception:
        pass

    try:
        from staff.models import StaffRequest

        pending = StaffRequest.objects.filter(
            restaurant=restaurant,
            status__in={"PENDING", "ESCALATED"},
        ).count()
        if pending:
            lines.append(f"Open staff requests (inbox): {pending}.")
    except Exception:
        pass

    try:
        from scheduling.models import AssignedShift

        shift_count = AssignedShift.objects.filter(
            restaurant=restaurant,
            shift_date=today,
        ).count()
        lines.append(f"Shifts scheduled today ({today.isoformat()}): {shift_count}.")
    except Exception:
        pass

    try:
        from miya.services.tenant_documents import recent_documents_block

        doc_block = recent_documents_block(restaurant)
        if doc_block:
            lines.append(doc_block.strip())
    except Exception:
        pass

    if not lines:
        return ""
    return "\n[TENANT SNAPSHOT — authoritative for this workspace]\n" + "\n".join(lines) + "\n"
