"""Morocco compliance reminder templates (reminders only — no filing)."""
from __future__ import annotations

from calendar import monthrange
from datetime import date


def morocco_compliance_templates(reference: date | None = None) -> list[dict]:
    """
    Build statutory reminder rows for the current pay period.

    Calendar nudges for managers — not automated CNSS/IR filing.
    """
    today = reference or date.today()
    year, month = today.year, today.month
    if month == 12:
        next_month, next_year = 1, year + 1
    else:
        next_month, next_year = month + 1, year

    quarter = (month - 1) // 3 + 1
    ir_months = {1: 1, 2: 4, 3: 7, 4: 10}
    ir_month = ir_months[quarter]
    ir_due = date(year if ir_month >= month else year + 1, ir_month, 15)

    return [
        {
            "code": f"cnss-monthly-{year}-{month:02d}",
            "title": f"Déclaration CNSS — {month:02d}/{year}",
            "description": (
                "Reminder: CNSS cotisations declaration for this pay period are typically "
                "due by the 10th of the following month. Verify amounts before filing on cnss.ma."
            ),
            "category": "CNSS",
            "due_date": date(next_year, next_month, 10),
            "remind_days_before": 7,
            "external_id": f"cnss-monthly:{year}-{month:02d}",
        },
        {
            "code": f"ir-professional-q{quarter}-{year}",
            "title": f"IR / Professional tax instalment — Q{quarter} {year}",
            "description": (
                "Reminder: check professional income tax (IR) instalment deadlines with your "
                "accountant. This is a calendar nudge only — Miya does not file with DGI."
            ),
            "category": "TAX",
            "due_date": ir_due,
            "remind_days_before": 14,
            "external_id": f"ir-q{quarter}:{year}",
        },
        {
            "code": f"payroll-close-{year}-{month:02d}",
            "title": f"Close payroll — {month:02d}/{year}",
            "description": (
                "Generate and review staff payslips, then prepare CNSS declaration for the period."
            ),
            "category": "LABOR",
            "due_date": date(next_year, next_month, min(5, monthrange(next_year, next_month)[1])),
            "remind_days_before": 3,
            "external_id": f"payroll-close:{year}-{month:02d}",
        },
    ]
