"""
Server-side intent router for staff-request ingest.

Why this exists
---------------
Miya (the LLM agent) is supposed to pick the right tool for every
manager / staff message: ``report_incident`` for safety/maintenance
issues, ``request_time_off`` for dated leave, ``capture_guest_order``
for orders, etc.  In practice the model sometimes falls back to the
generic ``staff_request`` tool with ``category='OTHER'`` — that is
how everything ended up in the same Team-inbox bucket.

This module is the *deterministic safety net*. It runs **after** the
agent payload arrives at ``agent_ingest_staff_request`` and:

* re-routes obvious incidents (broken equipment, fire, leak, injury,
  pest, theft, harassment, food-safety hazards) to the Reported
  Incidents surface (``SafetyConcernReport``) instead of the inbox;
* otherwise, when the agent left the category empty / set ``OTHER``,
  it infers the correct ``StaffRequest.category`` from the message so
  the inbox is self-organised by HR / Payroll / Document / Scheduling
  / Maintenance / Inventory / Reservations / Operations.

It is **purely keyword/regex based** — no LLM, no network. That keeps
the inbox classifier predictable, debuggable, and free.

The classifier is intentionally conservative: when in doubt, we keep
``category='OTHER'`` so the manager still sees the request — we never
silently drop or reroute something we don't recognise.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

# These must stay in sync with ``StaffRequest.CATEGORY_CHOICES``. The
# extra ``MEETING`` slot is a *task-only* bucket (used by the Tasks &
# Demands → Meetings widget on the dashboard); StaffRequest rows can't
# carry it, but the classifier still emits it for ``dashboard.Task``
# rows.
INBOX_CATEGORIES = (
    "DOCUMENT",
    "HR",
    "SCHEDULING",
    "PAYROLL",
    "FINANCE",
    "OPERATIONS",
    "MAINTENANCE",
    "RESERVATIONS",
    "INVENTORY",
    "MEETING",
    "OTHER",
)

# Subset valid for ``StaffRequest.category`` — MEETING isn't a request
# bucket (meetings come from the calendar, not the inbox).
STAFF_REQUEST_CATEGORIES = tuple(c for c in INBOX_CATEGORIES if c != "MEETING")

# Destination buckets returned by the classifier.
DEST_INCIDENT = "INCIDENT"
DEST_INBOX = "INBOX"

# The incident sub-categories must match
# ``staff/incident_routing.CANONICAL_INCIDENT_CATEGORIES``.
INCIDENT_SAFETY = "Safety"
INCIDENT_MAINTENANCE = "Maintenance"
INCIDENT_HR = "HR"
INCIDENT_FOOD_SAFETY = "Food Safety"
INCIDENT_CUSTOMER = "Customer Issue"
INCIDENT_GENERAL = "General"


@dataclass(frozen=True)
class IntentDecision:
    """Outcome of running ``classify_request`` on a free-text message.

    Attributes:
        destination: ``"INCIDENT"`` (route to ``SafetyConcernReport``)
            or ``"INBOX"`` (keep as ``StaffRequest`` row).
        category: For inbox destinations, the canonical
            ``StaffRequest.category``. For incident destinations, the
            canonical ``SafetyConcernReport.incident_type``.
        priority: Suggested priority (``LOW``/``MEDIUM``/``HIGH``/
            ``URGENT``/``CRITICAL`` for incidents). Returned only when
            we have a strong signal — otherwise ``None`` so callers can
            fall back to whatever the agent passed.
        confidence: ``"high"`` if we matched a strong keyword on a
            description long enough to trust, ``"medium"`` if the
            match came from a shorter / weaker signal, ``"low"`` if
            we are just guessing from the subject.
        matched_terms: The terms that triggered the decision — useful
            for logging and "why did this land here?" debugging.
    """

    destination: str
    category: str
    priority: str | None = None
    confidence: str = "medium"
    matched_terms: tuple[str, ...] = ()

    def is_incident(self) -> bool:
        return self.destination == DEST_INCIDENT


# ---------------------------------------------------------------------------
# Keyword tables
# ---------------------------------------------------------------------------
#
# Each tuple is ``(canonical_category, [keyword, ...])``. Keywords are
# matched as **whole words** (with simple stemming for plurals) on a
# normalised lowercase / accent-stripped version of the text. We keep
# the lists explicit rather than clever — easy to audit, easy to extend.
#
# Order matters for inbox categorisation: the *first* category whose
# keywords appear wins. We rank "stronger" / less-ambiguous categories
# first (PAYROLL before HR, DOCUMENT before HR, etc.) because words
# like "salary" should trip PAYROLL even though the request also
# mentions "manager".

# Critical-hazard words → always upgrade incident priority to CRITICAL.
_CRITICAL_HAZARDS: tuple[str, ...] = (
    "fire", "explosion", "smoke", "gas leak", "gas-leak",
    "electrocution", "electric shock", "flood", "flooding",
    "life threatening", "life-threatening",
)

# Things that almost always mean "this is an incident, not a request".
_INCIDENT_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        INCIDENT_FOOD_SAFETY,
        (
            "expired food", "expired ingredient", "food poisoning",
            "spoiled", "rotten", "moldy", "contaminat",
            "raw chicken", "undercooked", "cross contamination",
        ),
    ),
    (
        INCIDENT_SAFETY,
        (
            "fire", "explosion", "smoke", "gas leak", "gas-leak",
            "burn", "burned", "burnt", "scald",
            "injur", "injury", "bleeding", "bleeds",
            "fell", "fall", "slipped", "slip",
            "hazard", "hazardous", "danger",
            "electrocut", "electric shock",
            "flood", "flooding", "water leak", "leak",
            "robbery", "robbed", "theft", "stolen",
            "harass", "harassment", "assault",
            "pest", "rat", "mice", "mouse", "cockroach", "roach",
            "infestation",
        ),
    ),
    (
        INCIDENT_MAINTENANCE,
        (
            "broken", "broke", "not working", "doesn't work",
            "doesnt work", "stopped working", "out of order",
            "malfunction", "smoking",
            "fryer broken", "oven broken", "freezer broken",
            "fridge broken", "ac broken", "ac not working",
            "air conditioning", "no power", "power outage",
            "blackout", "lights out", "no light",
            "clogged", "blocked drain", "toilet clogged",
        ),
    ),
    (
        INCIDENT_CUSTOMER,
        (
            "customer complain", "guest complain", "customer angry",
            "guest angry", "bad review", "refund demand",
            "customer hurt", "guest hurt",
        ),
    ),
)

# Inbox category routing — used when destination=INBOX.
_INBOX_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "PAYROLL",
        (
            # Strictly *employee* pay topics. Vendor invoices belong in
            # FINANCE (handled below) — keep PAYROLL for the HR-adjacent
            # "where is my money" requests so the Payroll team only sees
            # what's theirs.
            "salary", "payslip", "pay slip", "pay-slip", "payroll",
            "wage", "wages", "bonus", "overtime pay", "ot pay",
            "tips ", "tip share", "deduction", "missing pay",
            "haven't been paid", "havent been paid", "not been paid",
            "not paid yet", "no pay this month", "missing payslip",
            "garnishment", "tax form for employee",
        ),
    ),
    (
        "FINANCE",
        (
            # Vendor / accounts-payable / treasury / fiscal items. These
            # are the rows that fill the Finance widget on the dashboard.
            "invoice", "invoices", "bill to pay", "bills to pay",
            "supplier payment", "vendor payment", "vendor invoice",
            "supplier invoice", "pay supplier", "pay vendor",
            "purchase order", "po number", "credit note",
            "rent", "rental fee", "lease payment",
            "utility", "utilities", "electricity bill", "water bill",
            "internet bill", "phone bill", "gas bill", "telecom bill",
            "tax", "city tax", "vat", "tva", "income tax",
            "property tax", "tax declaration", "fiscal", "patente",
            "license fee", "renewal fee", "permit fee", "subscription fee",
            "insurance premium", "insurance renewal",
            "accountant", "bookkeeper", "audit", "statement of account",
            "bank fee", "bank charge", "loan repayment", "credit card statement",
        ),
    ),
    (
        "DOCUMENT",
        (
            "contract", "id card", "passport", "visa", "work permit",
            "residency", "resident permit", "cnie", "cin",
            "certificate", "diploma", "letter of employment",
            "employment letter", "attestation", "bank letter",
            "loan letter", "tax certificate",
            # Documents that frequently come up on the HR widget:
            "print contract", "sign contract", "contracts to sign",
            "staff picture", "staff photo", "id photo",
        ),
    ),
    (
        "SCHEDULING",
        (
            "shift swap", "swap shift", "swap my shift",
            "cover my shift", "cover shift", "cover for me",
            "schedule change", "rota", "roster",
            "day off", "off on", "leave on", "vacation",
            "holiday", "annual leave", "time off", "time-off",
            "sick leave", "sick day", "absent tomorrow",
            "can't come", "cant come", "won't be in",
        ),
    ),
    (
        "INVENTORY",
        (
            "out of stock", "ran out", "running out", "low stock",
            "restock", "re-stock", "reorder", "re-order", "supplier",
            "delivery missing", "stock missing", "stock count",
            "inventory", "wastage", "waste",
        ),
    ),
    (
        "RESERVATIONS",
        (
            "booking", "reservation", "table for", "book a table",
            "no-show", "no show",
        ),
    ),
    (
        "MAINTENANCE",
        (
            # Soft-maintenance — handled as inbox MAINTENANCE only when
            # the incident rules above didn't fire (e.g. a *request* to
            # fix something rather than a report of something broken).
            # Routine / preventive items (recharge extinguishers, oven
            # deepclean, annual sink service) live here too.
            "please fix", "needs repair", "needs maintenance",
            "service the", "tune up", "tune-up",
            "extinguisher", "fire extinguisher", "extinguishers recharge",
            "deep cleaning", "deepcleaning", "deep clean",
            "annual maintenance", "annual service", "annual sink",
            "preventive maintenance", "scheduled maintenance",
            "duct cleaning", "hood cleaning", "filter change",
            "oven cleaning", "fryer cleaning", "freezer service",
            "fridge service", "compressor service",
            "bad smell", "weird smell", "strange smell",
            "pest control schedule", "pest control visit",
        ),
    ),
    (
        "HR",
        (
            "complaint about", "complain about",
            "uniform", "training", "onboarding", "onboard",
            "new hire", "new joiner", "new starter", "induction",
            "orientation", "trainee",
            "policy", "harassment policy",
            "promotion", "raise", "salary review",
            "performance review", "appraisal",
            "grievance", "discipline", "disciplinary",
            # Hire/exit paperwork that frequently shows up on the HR widget:
            "dismissal", "dismissal letter", "termination",
            "termination letter", "resignation", "resignation letter",
            "warning letter", "exit interview",
            # HR-curated employee assets shown on the HR widget — the
            # "pictures of staff" mockup row is a typical example.
            "pictures of staff", "photos of staff", "staff pictures",
            "staff photos", "employee photos", "employee pictures",
            "team photo", "team pictures",
        ),
    ),
    (
        "MEETING",
        (
            # Calendar-style reminders that should land in the Meetings
            # & Reminders dashboard widget rather than a category lane.
            "meeting with", "meet with", "schedule a meeting",
            "set up a meeting", "set up meeting", "book a meeting",
            "team meeting", "weekly meeting", "monthly meeting",
            "1:1 with", "one on one", "1 on 1",
            "remind me to", "reminder to", "reminder for",
            "appointment with", "call with", "zoom with", "teams meeting",
            "interview with", "interview candidate",
            # "Demand <person> <day/time>" framing — the dashboard mock
            # shows rows like "Demand Nadir SAT3:30pm" landing in the
            # Meetings & Reminders widget, so we treat any "demand" /
            # "demande" prefix paired with a named time as a meeting.
            # The pure word "demand" alone is too broad, so we require
            # an actual demand-meeting phrase.
            "demand client", "demande client",
            "demand at ", "demande a ",
        ),
    ),
    (
        "OPERATIONS",
        (
            "task", "to-do", "todo", "checklist",
            "clean the", "deep clean", "restocking task",
            "open early", "close late", "opening checklist",
            "closing checklist", "prep list",
        ),
    ),
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalise(text: str) -> str:
    """Lowercase, strip diacritics, collapse whitespace, drop punctuation
    that breaks naive ``in`` matching. Returns a single padded string so
    callers can match on whole words via ``" word " in normalised``.
    """
    if not text:
        return " "
    s = unicodedata.normalize("NFKD", str(text))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.lower()
    # Replace any non-alphanumeric run with a single space — keeps phrases
    # like "gas leak" / "out of stock" detectable while killing noise.
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return f" {s.strip()} "


def _matches_any(haystack: str, needles: Iterable[str]) -> tuple[str, ...]:
    """Return every needle (already normalised) that appears in ``haystack``.

    Both inputs are expected to already be space-padded lowercase
    strings produced by ``_normalise``.
    """
    hits: list[str] = []
    for raw in needles:
        n = _normalise(raw).strip()
        if not n:
            continue
        # Match as a sub-string but only when surrounded by spaces so
        # ``"leak"`` doesn't fire on ``"leaking"`` is intentionally allowed
        # (we *do* want "leaking" to count) — the trailing space comes
        # from the padded haystack, the leading space from the haystack
        # too. Compromise: we accept prefix-style matches.
        if f" {n}" in haystack:
            hits.append(n)
    return tuple(hits)


def _infer_incident_priority(text: str) -> str:
    """Hazard-aware default priority for incident destinations."""
    norm = _normalise(text)
    if any(f" {kw} " in norm or f" {kw}" in norm for kw in _CRITICAL_HAZARDS):
        return "CRITICAL"
    if any(
        kw in norm
        for kw in (
            " injur", " bleeding", " hurt ", " hurts ",
            " emergency ", " urgent ",
        )
    ):
        return "HIGH"
    return "MEDIUM"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def classify_request(
    *,
    subject: str = "",
    description: str = "",
    agent_category: str | None = None,
) -> IntentDecision:
    """Decide where this incoming message should land.

    Args:
        subject: Short title from the agent payload (may be empty).
        description: The full message body — primary signal source.
        agent_category: Whatever ``category`` the agent already sent.
            We treat anything other than ``"OTHER"`` / blank as a hint
            we should respect unless an *incident* keyword fires (an
            obvious safety report should never sit in the inbox even
            if Miya labelled it ``HR``).

    Returns:
        An :class:`IntentDecision` describing where to route the
        request and which canonical category/incident_type to use.
    """
    normalised = _normalise(f"{subject}\n{description}")
    agent_cat = (agent_category or "").upper().strip() or "OTHER"

    # 1. Incident routing always wins for unambiguous safety/food-safety
    #    hits, even if the agent already chose an inbox category.
    #
    # Guard rails for false-positive incidents:
    #   * "fire extinguisher recharge" / "smoke detector test" are
    #     *preventive maintenance*, not active fires. We demote to inbox
    #     MAINTENANCE if the only safety hit was the word "fire" or
    #     "smoke" AND the message also matches a maintenance/preventive
    #     keyword.
    #   * "the fryer is broken, please add to task list" is a task, not
    #     an incident — handled by the existing `task_framing` check.
    _PREVENTIVE_HINTS = (
        "extinguisher", "fire extinguisher", "smoke detector",
        "smoke alarm", "fire alarm", "sprinkler test",
        "annual maintenance", "annual service", "preventive maintenance",
        "scheduled maintenance", "recharge",
    )
    for incident_type, keywords in _INCIDENT_RULES:
        hits = _matches_any(normalised, keywords)
        if hits:
            # Be slightly more cautious for INCIDENT_MAINTENANCE: a manager
            # writing "the fryer is broken, please fix this week" is an
            # incident; a manager writing "please add 'fix fryer' to the
            # task list" is a task. If the message is short and contains
            # explicit task framing, demote to inbox MAINTENANCE.
            task_framing = any(
                phrase in normalised
                for phrase in (
                    " add task ", " create task ", " add to checklist ",
                    " add to the checklist ", " task list ",
                )
            )
            if incident_type == INCIDENT_MAINTENANCE and task_framing:
                return IntentDecision(
                    destination=DEST_INBOX,
                    category="MAINTENANCE",
                    confidence="medium",
                    matched_terms=hits,
                )
            # Preventive-maintenance demotion for safety false positives
            # (extinguisher recharge / smoke alarm test / annual fire
            # service). These are routine tasks, not active incidents.
            if incident_type == INCIDENT_SAFETY and _matches_any(
                normalised, _PREVENTIVE_HINTS,
            ):
                return IntentDecision(
                    destination=DEST_INBOX,
                    category="MAINTENANCE",
                    confidence="medium",
                    matched_terms=hits,
                )
            return IntentDecision(
                destination=DEST_INCIDENT,
                category=incident_type,
                priority=_infer_incident_priority(normalised),
                confidence="high",
                matched_terms=hits,
            )

    # 2. Honour an explicit, valid agent category (so Miya stays in
    #    control when she did do her job). We still re-validate against
    #    INBOX_CATEGORIES so typos / unknown labels fall through.
    if agent_cat != "OTHER" and agent_cat in INBOX_CATEGORIES:
        return IntentDecision(
            destination=DEST_INBOX,
            category=agent_cat,
            confidence="high",
            matched_terms=(f"agent:{agent_cat}",),
        )

    # 3. Otherwise infer category from keywords.
    for category, keywords in _INBOX_RULES:
        hits = _matches_any(normalised, keywords)
        if hits:
            return IntentDecision(
                destination=DEST_INBOX,
                category=category,
                confidence="medium" if len(hits) >= 1 else "low",
                matched_terms=hits,
            )

    # 4. Total miss — keep as OTHER but mark the confidence so callers
    #    (and dashboards) can surface "uncategorised" rows for review.
    return IntentDecision(
        destination=DEST_INBOX,
        category="OTHER",
        confidence="low",
        matched_terms=(),
    )
