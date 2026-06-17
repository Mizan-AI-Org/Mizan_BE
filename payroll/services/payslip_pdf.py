"""Build payslip PDFs from clock hours + profile rates."""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO

from reporting.services_labor import get_staff_hourly_rate

MIZAN_GREEN = "#059669"


def _monthly_gross(profile, hours: Decimal, hourly_rate: Decimal) -> Decimal:
    salary_type = getattr(profile, "salary_type", "HOURLY") if profile else "HOURLY"
    monthly = getattr(profile, "monthly_salary", None) if profile else None
    if salary_type == "MONTHLY" and monthly and monthly > 0:
        return Decimal(str(monthly))
    return (hours * hourly_rate).quantize(Decimal("0.01"))


def build_payslip_pdf(
    *,
    staff,
    profile,
    restaurant_name: str,
    period_start: date,
    period_end: date,
    hours: Decimal,
    hourly_rate: Decimal,
    gross_pay: Decimal,
    currency: str,
) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, rightMargin=0.75 * inch, leftMargin=0.75 * inch)
    styles = getSampleStyleSheet()
    story = []

    title_style = ParagraphStyle(
        name="MizanTitle",
        parent=styles["Title"],
        fontSize=16,
        textColor=colors.HexColor(MIZAN_GREEN),
    )
    story.append(Paragraph("<b>Mizan AI — Fiche de paie / Payslip</b>", title_style))
    story.append(Paragraph(f"<i>{restaurant_name}</i>", styles["Normal"]))
    story.append(Spacer(1, 12))

    full_name = f"{staff.first_name or ''} {staff.last_name or ''}".strip() or staff.email
    story.append(Paragraph(f"<b>{full_name}</b>", styles["Heading1"]))
    story.append(Paragraph(
        f"Période / Period: {period_start.isoformat()} → {period_end.isoformat()}",
        styles["Normal"],
    ))
    story.append(Spacer(1, 12))

    rows = [
        ["Description", "Montant / Amount"],
        ["Heures travaillées / Hours worked", f"{hours:.2f}"],
        ["Taux horaire / Hourly rate", f"{hourly_rate:.2f} {currency}"],
        ["Salaire brut / Gross pay", f"{gross_pay:.2f} {currency}"],
    ]
    table = Table(rows, colWidths=[3.5 * inch, 2 * inch])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(MIZAN_GREEN)),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    story.append(table)
    story.append(Spacer(1, 16))
    story.append(
        Paragraph(
            "<i>Document informatif — déductions CNSS/IR non calculées automatiquement. "
            "Informational payslip — statutory deductions not auto-calculated.</i>",
            ParagraphStyle(name="Fine", parent=styles["Normal"], fontSize=8, textColor=colors.grey),
        )
    )
    doc.build(story)
    return buf.getvalue()


def generate_payslip_for_staff(
    *,
    staff,
    restaurant,
    period_start: date,
    period_end: date,
    hours: Decimal | None = None,
    acting_user=None,
):
    from payroll.models import Payslip
    from payroll.services.hours import staff_hours_from_clock_events

    profile = getattr(staff, "profile", None)
    if hours is None:
        hours = staff_hours_from_clock_events(staff, period_start, period_end)
    hourly_rate = Decimal(str(get_staff_hourly_rate(staff)))
    gross = _monthly_gross(profile, hours, hourly_rate)
    currency = getattr(restaurant, "currency", None) or "MAD"

    payslip, created = Payslip.objects.update_or_create(
        restaurant=restaurant,
        staff=staff,
        period_start=period_start,
        period_end=period_end,
        defaults={
            "hours_worked": hours,
            "hourly_rate": hourly_rate,
            "gross_pay": gross,
            "currency": currency,
            "status": Payslip.STATUS_ISSUED,
            "created_by": acting_user,
        },
    )
    pdf_bytes = build_payslip_pdf(
        staff=staff,
        profile=profile,
        restaurant_name=getattr(restaurant, "name", "Restaurant"),
        period_start=period_start,
        period_end=period_end,
        hours=hours,
        hourly_rate=hourly_rate,
        gross_pay=gross,
        currency=currency,
    )
    return payslip, created, pdf_bytes
