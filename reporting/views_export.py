"""
Staff Attendance Report export for HR / payroll: PDF and Excel.
Managers can generate and send to HR.
"""
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from datetime import datetime
from io import BytesIO

from accounts.permissions import IsAdminOrManager


def _get_attendance_report_data(restaurant, start_date, end_date):
    """Build attendance report rows: timesheets merged with late/no_show from planned_vs_actual."""
    from scheduling.models import Timesheet
    from reporting.services_labor import planned_vs_actual_hours

    timesheets = Timesheet.objects.filter(
        restaurant=restaurant,
        start_date__lte=end_date,
        end_date__gte=start_date,
    ).select_related("staff").order_by("staff__last_name", "staff__first_name", "start_date")

    pv = planned_vs_actual_hours(restaurant, start_date, end_date)
    by_staff = {str(r["staff_id"]): r for r in (pv.get("by_staff") or [])}

    rows = []
    for ts in timesheets:
        staff = ts.staff
        sid = str(staff.id) if staff else ""
        extra = by_staff.get(sid, {})
        rows.append({
            "staff_id": sid,
            "email": getattr(staff, "email", "") or "",
            "first_name": getattr(staff, "first_name", "") or "",
            "last_name": getattr(staff, "last_name", "") or "",
            "start_date": ts.start_date.isoformat(),
            "end_date": ts.end_date.isoformat(),
            "total_hours": float(ts.total_hours or 0),
            "hourly_rate": float(ts.hourly_rate or 0),
            "total_earnings": float(ts.total_earnings or 0),
            "late_arrivals": extra.get("late_count", 0),
            "no_shows": extra.get("no_show_count", 0),
            "status": ts.status or "",
        })
    return rows


def _export_excel(rows, start_date, end_date):
    import openpyxl
    from openpyxl.styles import Font, Alignment

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Attendance Report"
    headers = [
        "Staff ID", "First Name", "Last Name", "Email",
        "Start Date", "End Date", "Total Hours", "Hourly Rate", "Total Earnings",
        "Late Arrivals", "No-Shows", "Status",
    ]
    ws.append(headers)
    for h in range(1, len(headers) + 1):
        ws.cell(row=1, column=h).font = Font(bold=True)
    for r in rows:
        ws.append([
            r["staff_id"], r["first_name"], r["last_name"], r["email"],
            r["start_date"], r["end_date"], r["total_hours"], r["hourly_rate"], r["total_earnings"],
            r["late_arrivals"], r["no_shows"], r["status"],
        ])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def _export_pdf(rows, start_date, end_date, restaurant_name=""):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer, PageBreak

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=inch, leftMargin=inch, topMargin=inch, bottomMargin=inch)
    styles = getSampleStyleSheet()
    story = []
    title = Paragraph(f"<b>Staff Attendance Report (for HR / Payroll)</b>", styles["Title"])
    story.append(title)
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"Period: {start_date} to {end_date}", styles["Normal"]))
    if restaurant_name:
        story.append(Paragraph(f"Restaurant: {restaurant_name}", styles["Normal"]))
    story.append(Spacer(1, 16))

    headers = [
        "Staff ID", "First Name", "Last Name", "Email",
        "Start", "End", "Hours", "Rate", "Earnings",
        "Lates", "No-Shows", "Status",
    ]
    data = [headers]
    for r in rows:
        data.append([
            str(r["staff_id"]), r["first_name"], r["last_name"], r["email"],
            r["start_date"], r["end_date"], str(r["total_hours"]), str(r["hourly_rate"]), str(r["total_earnings"]),
            str(r["late_arrivals"]), str(r["no_shows"]), r["status"],
        ])
    t = Table(data, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4472C4")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, 0), 9),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 8),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BACKGROUND", (0, 1), (-1, -1), colors.white),
        ("TEXTCOLOR", (0, 1), (-1, -1), colors.black),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 1), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    story.append(t)
    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


@api_view(["GET"])
@permission_classes([IsAuthenticated, IsAdminOrManager])
def attendance_export(request):
    """
    Export Staff Attendance Report for HR/payroll.
    Query params: format=pdf|excel, start_date=YYYY-MM-DD, end_date=YYYY-MM-DD.
    """
    if request.user.role not in ("ADMIN", "SUPER_ADMIN", "MANAGER"):
        return Response({"detail": "Only managers can export attendance reports."}, status=status.HTTP_403_FORBIDDEN)

    fmt = (request.query_params.get("format") or "excel").lower().strip()
    if fmt not in ("pdf", "excel", "xlsx"):
        return Response(
            {"detail": "format must be 'pdf' or 'excel' (or 'xlsx')."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    start_date = request.query_params.get("start_date")
    end_date = request.query_params.get("end_date")
    if not start_date or not end_date:
        return Response(
            {"detail": "start_date and end_date required (YYYY-MM-DD)."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
        end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
    except ValueError:
        return Response({"detail": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

    restaurant = getattr(request.user, "restaurant", None)
    if not restaurant:
        return Response({"detail": "No restaurant associated."}, status=status.HTTP_403_FORBIDDEN)

    rows = _get_attendance_report_data(restaurant, start_date, end_date)
    restaurant_name = getattr(restaurant, "name", "") or ""

    if fmt == "pdf":
        content = _export_pdf(rows, start_date, end_date, restaurant_name)
        resp = HttpResponse(content, content_type="application/pdf")
        resp["Content-Disposition"] = f'attachment; filename="staff_attendance_report_{start_date}_{end_date}.pdf"'
        return resp
    else:
        content = _export_excel(rows, start_date, end_date)
        resp = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = f'attachment; filename="staff_attendance_report_{start_date}_{end_date}.xlsx"'
        return resp
