"""
Detect staff → manager escalations on WhatsApp (wages, payslip, HR docs).

Used by the Django WhatsApp webhook so these land as StaffRequest rows
before Lua/Space can invent inform_staff confirm flows.
"""

from __future__ import annotations

import re
from typing import Optional, TypedDict


class EscalationRoute(TypedDict):
    category: str
    subject: str
    description: str


TELL_MANAGER_RE = re.compile(
    r"\b(tell\s+(my\s+)?manager|pass\s+(this\s+)?(to|on\s+to)\s+(my\s+)?manager|"
    r"let\s+(my\s+)?manager\s+know|inform\s+(my\s+)?manager|"
    r"dis\s+[àa]\s+(mon\s+)?(manager|responsable|patron)|"
    r"قل\s+(ل|لـ)?(المدير|المانجر|المسؤول))\b",
    re.I,
)

SPACE_INFORM_MANAGER_RE = re.compile(
    r"\b(inform|tell|let|notify)\s+(the\s+)?manager\b",
    re.I,
)

PAYROLL_RE = re.compile(
    r"\b(pay\s*slip|payslip|pay\s*stub|salary\s+slip|bulletin\s+de\s+paie|fiche\s+de\s+paie|"
    r"كشف\s+الراتب|ورقة\s+الأجر|my\s+pay|last\s+\d+\s+months?\s+pay|wages?|salary|"
    r"unpaid\s+(pay|wages?|salary)|missing\s+(pay|wages?|salary)|"
    r"haven['']?t\s+received\s+(my\s+)?(pay|wages?|salary|last)|"
    r"yet\s+to\s+receive\s+(my\s+)?(pay|wages?|salary|last)|"
    r"didn['']?t\s+(get|receive)\s+(my\s+)?(pay|wages?|salary)|"
    r"last\s+week['']?s?\s+wages?|paie|salaire|أجرى|راتبي)\b",
    re.I,
)

DOCUMENT_RE = re.compile(
    r"\b(visa|passport|work\s+permit|certificate|attestation|document|papers|وثيقة|تأشيرة|شهادة)\b",
    re.I,
)

HR_RE = re.compile(
    r"\b(leave\s+request|time\s+off|vacation|holiday|sick\s+day|hr\s+request|cong[eé]|arrêt\s+maladie|إجازة)\b",
    re.I,
)

SCHEDULING_RE = re.compile(
    r"\b(swap\s+(my\s+)?shift|change\s+(my\s+)?shift|cover\s+(my\s+)?shift|schedule\s+change|تبديل\s+الشيفت)\b",
    re.I,
)

MAINTENANCE_RE = re.compile(
    r"\b(leak|not\s+working|repair|fix\s+the|maintenance|en\s+panne|fuite|خاسر|معطل|"
    r"(?:broken|down)\s+(?:fridge|freezer|oven|dishwasher|ac|equipment|machine))\b",
    re.I,
)

CONFIRM_SEND_RE = re.compile(
    r"^(yes([,!]?\s*(send(\s+it)?|please)?)?|oui([,!]?\s*(envoie|envoyer|s['']il\s+te\s+pla[iî]t)?)?|"
    r"send(\s+it)?|confirm(ed)?|ok([,!]?\s*send)?|نعم|أرسل|ارسل)\s*[.!]?$",
    re.I,
)

CANCEL_SEND_RE = re.compile(
    r"^(no([,!]?\s*(cancel|thanks)?)?|non([,!]?\s*(annule|merci)?)?|cancel(led)?|never\s*mind|لا|ألغ)\s*[.!]?$",
    re.I,
)

YOU_QUOTE_RE = re.compile(r"(?:^|\n)(?:You|Vous|Tu)\s*:\s*([^\n]+)", re.I)


def _strip_you_prefix(text: str) -> str:
    t = (text or "").strip()
    m = re.match(r"^(?:You|Vous|Tu)\s*:\s*(.+)$", t, re.I)
    return m.group(1).strip() if m else t


def extract_quoted_user_lines(text: str) -> list[str]:
    out: list[str] = []
    for m in YOU_QUOTE_RE.finditer(text or ""):
        line = m.group(1).strip()
        if line:
            out.append(line)
    return out


def classify_whatsapp_escalation(text: str) -> Optional[EscalationRoute]:
    """Return category + subject + description when this is a staff escalation."""
    t = _strip_you_prefix((text or "").strip())
    if not t or len(t) < 8:
        return None

    wants_manager = bool(TELL_MANAGER_RE.search(t)) or (
        bool(SPACE_INFORM_MANAGER_RE.search(t))
        and any(
            rx.search(t)
            for rx in (PAYROLL_RE, DOCUMENT_RE, HR_RE, SCHEDULING_RE, MAINTENANCE_RE)
        )
    )
    is_payroll = bool(PAYROLL_RE.search(t))
    is_doc = bool(DOCUMENT_RE.search(t))
    is_hr = bool(HR_RE.search(t))
    is_sched = bool(SCHEDULING_RE.search(t))
    is_maint = bool(MAINTENANCE_RE.search(t))

    if wants_manager:
        if is_payroll:
            cat = "PAYROLL"
        elif is_doc:
            cat = "DOCUMENT"
        elif is_hr:
            cat = "HR"
        elif is_sched:
            cat = "SCHEDULING"
        elif is_maint:
            cat = "MAINTENANCE"
        else:
            cat = "OTHER"
        return {"category": cat, "subject": t[:200], "description": t}

    if is_payroll and re.search(
        r"\b(need|want|ask|request|can\s+i|please|haven['']?t|yet\s+to|didn['']?t|missing|unpaid|"
        r"بغيت|خاصني|je\s+veux|j['']ai\s+besoin)\b",
        t,
        re.I,
    ):
        return {"category": "PAYROLL", "subject": t[:200], "description": t}

    if is_doc and re.search(
        r"\b(need|want|apply|request|please|بغيت|خاصني|je\s+veux|demande)\b",
        t,
        re.I,
    ):
        return {"category": "DOCUMENT", "subject": t[:200], "description": t}

    return None


def looks_like_staff_manager_escalation(text: str) -> bool:
    """True when Django should own this inbound text (block Lua/Space)."""
    t = _strip_you_prefix((text or "").strip())
    if classify_whatsapp_escalation(t):
        return True
    for quoted in extract_quoted_user_lines(text or ""):
        if classify_whatsapp_escalation(quoted):
            return True
    return False


def is_confirm_send_reply(text: str) -> bool:
    return bool(CONFIRM_SEND_RE.match(_strip_you_prefix((text or "").strip())))


def is_cancel_send_reply(text: str) -> bool:
    return bool(CANCEL_SEND_RE.match(_strip_you_prefix((text or "").strip())))
