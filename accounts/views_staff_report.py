"""
Staff Profile PDF Report – branded Mizan AI, in-depth single-staff report.
"""
from datetime import date, timedelta
from io import BytesIO
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.db.models import Sum

from .models import CustomUser, StaffProfile
from .views import IsManagerOrAdmin
from scheduling.models import Timesheet


# Mizan AI brand color (emerald)
MIZAN_GREEN = "#059669"


def _staff_hours_for_period(staff, start_date, end_date):
    """Sum timesheet total_hours for this staff in [start_date, end_date]."""
    qs = Timesheet.objects.filter(
        staff=staff,
        start_date__lte=end_date,
        end_date__gte=start_date,
        status__in=["SUBMITTED", "APPROVED", "PAID"],
    )
    total = qs.aggregate(Sum("total_hours"))["total_hours__sum"]
    return float(total) if total is not None else 0.0


def _build_staff_profile_pdf(staff, profile, restaurant_name, hours_weekly, hours_monthly, hours_yearly):
    """Build PDF buffer with ReportLab – Mizan AI branding and full staff details."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    )

    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        rightMargin=inch * 0.75,
        leftMargin=inch * 0.75,
        topMargin=inch * 0.6,
        bottomMargin=inch * 0.6,
    )
    styles = getSampleStyleSheet()
    story = []

    # ----- Brand header -----
    title_style = ParagraphStyle(
        name="MizanTitle",
        parent=styles["Title"],
        fontSize=18,
        textColor=colors.HexColor(MIZAN_GREEN),
        spaceAfter=2,
    )
    story.append(Paragraph("<b>Mizan AI</b>", title_style))
    story.append(Paragraph(
        "<i>Staff Profile Report</i>",
        ParagraphStyle(name="SubTitle", parent=styles["Normal"], fontSize=10, textColor=colors.grey),
    ))
    story.append(Spacer(1, 6))
    # Green line (thin table)
    line_table = Table([[""]], colWidths=[6 * inch])
    line_table.setStyle(TableStyle([
        ("LINEABOVE", (0, 0), (-1, 0), 2, colors.HexColor(MIZAN_GREEN)),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(line_table)
    story.append(Spacer(1, 14))

    # ----- Staff name and role -----
    full_name = f"{staff.first_name or ''} {staff.last_name or ''}".strip() or staff.email or "Staff"
    role_display = (staff.role or "").replace("_", " ").title()
    story.append(Paragraph(f"<b>{full_name}</b>", styles["Heading1"]))
    story.append(Paragraph(role_display, ParagraphStyle(name="Role", parent=styles["Normal"], fontSize=11, textColor=colors.HexColor(MIZAN_GREEN))))
    story.append(Spacer(1, 12))

    # ----- Hours summary -----
    story.append(Paragraph("<b>Hours summary</b>", styles["Heading2"]))
    hours_data = [
        ["Period", "Hours"],
        ["This week", f"{hours_weekly:.1f}"],
        ["This month", f"{hours_monthly:.1f}"],
        ["This year", f"{hours_yearly:.1f}"],
    ]
    t = Table(hours_data, colWidths=[2 * inch, 1.5 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(MIZAN_GREEN)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.black),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 10),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(t)
    story.append(Spacer(1, 16))

    # ----- Contact info -----
    story.append(Paragraph("<b>Contact</b>", styles["Heading2"]))
    email = staff.email or "—"
    phone = getattr(staff, "phone", None) or (profile.emergency_contact_phone if profile else None) or "—"
    story.append(Paragraph(f"Email: {email}", styles["Normal"]))
    story.append(Paragraph(f"Phone: {phone}", styles["Normal"]))
    story.append(Spacer(1, 12))

    # ----- Employment details -----
    story.append(Paragraph("<b>Employment</b>", styles["Heading2"]))
    join_date = "—"
    hourly_rate = "—"
    department = "—"
    if profile:
        if profile.join_date:
            join_date = profile.join_date.strftime("%B %d, %Y")
        if profile.hourly_rate is not None and profile.hourly_rate > 0:
            hourly_rate = f"${float(profile.hourly_rate):,.2f} /hr"
        if profile.department:
            department = profile.department
    story.append(Paragraph(f"Restaurant: {restaurant_name}", styles["Normal"]))
    story.append(Paragraph(f"Department: {department}", styles["Normal"]))
    story.append(Paragraph(f"Join date: {join_date}", styles["Normal"]))
    story.append(Paragraph(f"Hourly rate: {hourly_rate}", styles["Normal"]))
    story.append(Spacer(1, 12))

    # ----- Promotion history -----
    story.append(Paragraph("<b>Promotion history</b>", styles["Heading2"]))
    if profile and getattr(profile, "promotion_history", None) and len(profile.promotion_history) > 0:
        promo_data = [["Role", "Date", "Note"]]
        for p in profile.promotion_history:
            if isinstance(p, dict):
                promo_data.append([
                    p.get("role", "—"),
                    p.get("date", "—"),
                    p.get("note", "—") or "—",
                ])
            else:
                promo_data.append([str(p), "—", "—"])
        pt = Table(promo_data, colWidths=[1.5 * inch, 1.2 * inch, 2.5 * inch])
        pt.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        story.append(pt)
    else:
        story.append(Paragraph("No promotion history recorded.", styles["Normal"]))
    story.append(Spacer(1, 12))

    # ----- Report generated -----
    from django.utils import timezone
    generated = timezone.now().strftime("%B %d, %Y at %H:%M")
    story.append(Spacer(1, 8))
    story.append(Paragraph(
        f"<i>Report generated by Mizan AI on {generated}</i>",
        ParagraphStyle(name="Footer", parent=styles["Normal"], fontSize=8, textColor=colors.grey),
    ))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsManagerOrAdmin])
def staff_profile_report_pdf(request, pk):
    """
    GET /api/staff/<uuid:pk>/report/pdf/
    Returns a branded PDF report for the given staff member (same restaurant only).
    """
    staff = get_object_or_404(CustomUser, pk=pk)
    if not getattr(request.user, "restaurant", None):
        return Response({"detail": "No restaurant associated."}, status=status.HTTP_403_FORBIDDEN)
    if str(staff.restaurant_id) != str(request.user.restaurant_id):
        return Response({"detail": "Staff member not found."}, status=status.HTTP_404_NOT_FOUND)

    try:
        profile = getattr(staff, "profile", None)
    except StaffProfile.DoesNotExist:
        profile = None

    restaurant_name = getattr(request.user.restaurant, "name", "") or "Restaurant"
    today = date.today()

    # Weekly: Mon–today this week
    week_start = today - timedelta(days=today.weekday())
    hours_weekly = _staff_hours_for_period(staff, week_start, today)

    # Monthly: first day of month – today
    month_start = today.replace(day=1)
    hours_monthly = _staff_hours_for_period(staff, month_start, today)

    # Yearly: Jan 1 – today
    year_start = today.replace(month=1, day=1)
    hours_yearly = _staff_hours_for_period(staff, year_start, today)

    pdf_bytes = _build_staff_profile_pdf(
        staff=staff,
        profile=profile,
        restaurant_name=restaurant_name,
        hours_weekly=hours_weekly,
        hours_monthly=hours_monthly,
        hours_yearly=hours_yearly,
    )

    filename = f"staff_report_{staff.first_name or 'staff'}_{staff.last_name or 'member'}.pdf".replace(" ", "_")
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@api_view(["GET"])
@authentication_classes([])
@permission_classes([])
def agent_staff_report_pdf(request):
    """
    GET /api/agent/staff-report-pdf/?staff_id=<uuid>&restaurant_id=<uuid>
    Returns the same branded staff profile PDF. Auth: Bearer LUA_WEBHOOK_API_KEY.
    """
    from django.conf import settings as django_settings
    auth_header = request.headers.get("Authorization")
    expected_key = getattr(django_settings, "LUA_WEBHOOK_API_KEY", None)
    if not expected_key:
        return Response({"detail": "Agent key not configured"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    if not auth_header or auth_header != f"Bearer {expected_key}":
        return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

    staff_id = request.query_params.get("staff_id") or request.META.get("HTTP_X_STAFF_ID")
    restaurant_id = request.query_params.get("restaurant_id") or request.META.get("HTTP_X_RESTAURANT_ID")
    if not staff_id or not restaurant_id:
        return Response(
            {"detail": "staff_id and restaurant_id required (query or X-Staff-Id / X-Restaurant-Id)."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    from .models import Restaurant
    try:
        staff = CustomUser.objects.get(pk=staff_id, restaurant_id=restaurant_id)
    except CustomUser.DoesNotExist:
        return Response({"detail": "Staff not found or not in this restaurant."}, status=status.HTTP_404_NOT_FOUND)

    try:
        profile = getattr(staff, "profile", None)
    except StaffProfile.DoesNotExist:
        profile = None

    restaurant = getattr(staff, "restaurant", None)
    restaurant_name = getattr(restaurant, "name", "") or "Restaurant"
    today = date.today()
    week_start = today - timedelta(days=today.weekday())
    hours_weekly = _staff_hours_for_period(staff, week_start, today)
    month_start = today.replace(day=1)
    hours_monthly = _staff_hours_for_period(staff, month_start, today)
    year_start = today.replace(month=1, day=1)
    hours_yearly = _staff_hours_for_period(staff, year_start, today)

    pdf_bytes = _build_staff_profile_pdf(
        staff=staff,
        profile=profile,
        restaurant_name=restaurant_name,
        hours_weekly=hours_weekly,
        hours_monthly=hours_monthly,
        hours_yearly=hours_yearly,
    )

    filename = f"staff_report_{staff.first_name or 'staff'}_{staff.last_name or 'member'}.pdf".replace(" ", "_")
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
