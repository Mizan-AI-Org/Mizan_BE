"""
Map free-text widget titles (English / French / Arabic / Spanish / Portuguese)
to the canonical, data-bound built-in dashboard widget id.

Why:
    Miya's ``create_custom`` action exists for *true* shortcut tiles
    (e.g. "Sales report PDF" → /reports/sales). When a manager says
    "Create a Purchases widget", they don't want a placeholder tile that
    only says "Ask Miya"; they want the data-bound ``purchase_orders``
    widget that renders open POs / procurement requests.

    Rather than relying on the LLM to always pick the right ``action``
    + built-in id, this resolver lets the *backend* recognise titles
    that map to a known operational lane and silently redirect the
    request to ``add`` the built-in widget instead.

The resolver is also used at *render time* (custom widget list) so any
tile a tenant already created with an aliased title is auto-replaced by
the data-bound built-in widget without manual cleanup.

Each entry maps a *normalised* alias (lowercased, stripped, accent-folded)
to the canonical widget id. Keep aliases concise — full natural-language
intent extraction belongs in the persona, not here.
"""

from __future__ import annotations

import unicodedata
from typing import Iterable

# All built-in widgets that are already data-bound (i.e. they render real
# operational data, not a "Ask Miya" placeholder). These are the only
# valid targets the resolver may redirect to.
DATA_BOUND_BUILTIN_IDS: frozenset[str] = frozenset(
    [
        "urgent_top",
        "human_resources",
        "finance",
        "maintenance",
        "purchase_orders",
        "miscellaneous",
        "meetings_reminders",
        "staff_inbox",
        "staff_messages",
        "clock_ins",
        "incidents",
        "inventory_delivery",
        "tasks_demands",
        "live_attendance",
        "ops_reports",
    ]
)

# Alias table. Keys are normalised (see _normalise) — keep them lowercased
# without accents. Values are the canonical built-in widget id.
#
# Coverage focus: the operational lanes the user actually creates by
# free-text on the dashboard ("Purchases", "HR", "Finance", "Maintenance",
# "Urgent", "Inbox", "Clock-ins", "Calendar", "Inventory", etc.).
_ALIASES: dict[str, str] = {
    # ---- purchase_orders -------------------------------------------------
    "purchases": "purchase_orders",
    "purchase": "purchase_orders",
    "purchase order": "purchase_orders",
    "purchase orders": "purchase_orders",
    "po": "purchase_orders",
    "pos": "purchase_orders",
    "procurement": "purchase_orders",
    "procurements": "purchase_orders",
    "vendor request": "purchase_orders",
    "vendor requests": "purchase_orders",
    "supplier order": "purchase_orders",
    "supplier orders": "purchase_orders",
    "buying": "purchase_orders",
    "achats": "purchase_orders",  # FR
    "achat": "purchase_orders",
    "bons de commande": "purchase_orders",
    "bon de commande": "purchase_orders",
    "approvisionnement": "purchase_orders",
    "compras": "purchase_orders",  # ES / PT
    "compra": "purchase_orders",
    "ordenes de compra": "purchase_orders",
    "orden de compra": "purchase_orders",
    "مشتريات": "purchase_orders",  # AR
    "طلبات شراء": "purchase_orders",
    # ---- human_resources -------------------------------------------------
    "hr": "human_resources",
    "human resources": "human_resources",
    "human resource": "human_resources",
    "ressources humaines": "human_resources",  # FR
    "ressource humaine": "human_resources",
    "rh": "human_resources",
    "recursos humanos": "human_resources",  # ES / PT
    "personal": "human_resources",
    "موارد بشرية": "human_resources",  # AR
    "الموارد البشرية": "human_resources",
    # ---- finance ---------------------------------------------------------
    "finance": "finance",
    "finances": "finance",
    "financial": "finance",
    "billing": "finance",
    "bills": "finance",
    "invoices": "finance",
    "factures": "finance",
    "facture": "finance",
    "facturas": "finance",
    "facturation": "finance",
    "comptabilite": "finance",
    "contabilidad": "finance",
    "payroll": "finance",
    "paie": "finance",
    "salarios": "finance",
    "fatiha": "finance",
    "مالية": "finance",
    "محاسبة": "finance",
    # ---- maintenance ------------------------------------------------------
    "maintenance": "maintenance",
    "repairs": "maintenance",
    "repair": "maintenance",
    "fix": "maintenance",
    "broken": "maintenance",
    "reparations": "maintenance",
    "reparation": "maintenance",
    "entretien": "maintenance",
    "mantenimiento": "maintenance",
    "manutencao": "maintenance",
    "صيانة": "maintenance",
    # ---- urgent_top -------------------------------------------------------
    "urgent": "urgent_top",
    "urgents": "urgent_top",
    "urgent top": "urgent_top",
    "top urgent": "urgent_top",
    "top 5 urgents": "urgent_top",
    "urgences": "urgent_top",
    "urgence": "urgent_top",
    "emergencies": "urgent_top",
    "emergency": "urgent_top",
    "urgentes": "urgent_top",
    "urgencias": "urgent_top",
    "عاجل": "urgent_top",
    "طوارئ": "urgent_top",
    # ---- miscellaneous ----------------------------------------------------
    "miscellaneous": "miscellaneous",
    "misc": "miscellaneous",
    "other": "miscellaneous",
    "others": "miscellaneous",
    "general": "miscellaneous",
    "divers": "miscellaneous",  # FR
    "autre": "miscellaneous",
    "autres": "miscellaneous",
    "varios": "miscellaneous",
    "diversos": "miscellaneous",
    "متفرقات": "miscellaneous",
    "أخرى": "miscellaneous",
    # ---- meetings_reminders ----------------------------------------------
    "meetings": "meetings_reminders",
    "meeting": "meetings_reminders",
    "calendar": "meetings_reminders",
    "calendars": "meetings_reminders",
    "reminders": "meetings_reminders",
    "reunions": "meetings_reminders",
    "reunion": "meetings_reminders",
    "calendrier": "meetings_reminders",
    "rappels": "meetings_reminders",
    "reuniones": "meetings_reminders",
    "calendario": "meetings_reminders",
    "اجتماعات": "meetings_reminders",
    "اجتماع": "meetings_reminders",
    "تقويم": "meetings_reminders",
    # ---- staff_inbox ------------------------------------------------------
    "inbox": "staff_inbox",
    "staff inbox": "staff_inbox",
    "staff requests": "staff_inbox",
    "demandes du personnel": "staff_inbox",
    "boite de reception": "staff_inbox",
    "bandeja de entrada": "staff_inbox",
    "البريد الوارد": "staff_inbox",
    # ---- staff_messages ---------------------------------------------------
    "staff messages": "staff_messages",
    "messages staff": "staff_messages",
    "send whatsapp": "staff_messages",
    "whatsapp": "staff_messages",
    "messages au personnel": "staff_messages",
    "mensajes al personal": "staff_messages",
    "رسائل الموظفين": "staff_messages",
    # ---- clock_ins --------------------------------------------------------
    "clock in": "clock_ins",
    "clock ins": "clock_ins",
    "clockins": "clock_ins",
    "clock-in": "clock_ins",
    "clocking": "clock_ins",
    "attendance": "clock_ins",
    "pointage": "clock_ins",
    "pointages": "clock_ins",
    "presence": "clock_ins",
    "asistencia": "clock_ins",
    "marcacion": "clock_ins",
    "حضور": "clock_ins",
    "تسجيل الحضور": "clock_ins",
    # ---- incidents --------------------------------------------------------
    "incidents": "incidents",
    "incident": "incidents",
    "issues": "incidents",
    "issue": "incidents",
    "incidentes": "incidents",
    "incidente": "incidents",
    "حوادث": "incidents",
    # ---- inventory_delivery ----------------------------------------------
    "inventory": "inventory_delivery",
    "stock": "inventory_delivery",
    "deliveries": "inventory_delivery",
    "delivery": "inventory_delivery",
    "stocks": "inventory_delivery",
    "magasin": "inventory_delivery",
    "inventaire": "inventory_delivery",
    "livraisons": "inventory_delivery",
    "inventario": "inventory_delivery",
    "almacen": "inventory_delivery",
    "مخزون": "inventory_delivery",
    "مستودع": "inventory_delivery",
    # ---- tasks_demands ----------------------------------------------------
    "tasks": "tasks_demands",
    "tasks and demands": "tasks_demands",
    "demands": "tasks_demands",
    "todo": "tasks_demands",
    "todos": "tasks_demands",
    "to do": "tasks_demands",
    "taches": "tasks_demands",
    "tache": "tasks_demands",
    "tareas": "tasks_demands",
    "مهام": "tasks_demands",
    # ---- live_attendance --------------------------------------------------
    "live attendance": "live_attendance",
    "presence en direct": "live_attendance",
    # ---- ops_reports ------------------------------------------------------
    "reports": "ops_reports",
    "ops reports": "ops_reports",
    "rapports": "ops_reports",
    "reportes": "ops_reports",
    "تقارير": "ops_reports",
}


def _normalise(s: str) -> str:
    """Lowercase, strip, accent-fold, collapse whitespace.

    Matches the way alias keys are stored. Latin diacritics are
    decomposed and stripped (``réservations`` → ``reservations``);
    Arabic / CJK characters are left untouched (Unicode normalisation
    only removes combining marks, not letters).
    """
    if not s:
        return ""
    norm = unicodedata.normalize("NFKD", str(s))
    folded = "".join(ch for ch in norm if not unicodedata.combining(ch))
    folded = folded.lower().strip()
    # Collapse internal whitespace runs to a single space so "purchase
    # orders" and "purchase  orders" hit the same key.
    folded = " ".join(folded.split())
    # Strip a leading "the " — common in spoken intents ("the purchases
    # widget") — to widen the match without bloating the alias table.
    if folded.startswith("the "):
        folded = folded[4:]
    return folded


def resolve_widget_alias(*candidates: str | None) -> str | None:
    """
    Return the canonical built-in widget id for the first candidate that
    matches a known alias, or ``None`` if nothing matches.

    Multiple candidates can be passed (e.g. title + subtitle +
    category_name). The first matching candidate wins.

    >>> resolve_widget_alias("Purchases")
    'purchase_orders'
    >>> resolve_widget_alias("Achats", "Supplier orders")
    'purchase_orders'
    >>> resolve_widget_alias("Random shortcut")
    None
    """
    for raw in candidates:
        if not raw:
            continue
        key = _normalise(raw)
        if not key:
            continue
        hit = _ALIASES.get(key)
        if hit:
            return hit
        # Also try the singular if the key looks plural and isn't
        # already in the table — cheap "Purchases" -> "purchase" hop
        # that doesn't require duplicating every entry.
        if key.endswith("s") and key[:-1] in _ALIASES:
            return _ALIASES[key[:-1]]
    return None


def is_alias_for_data_bound_widget(*candidates: str | None) -> bool:
    """Convenience: True iff at least one candidate maps to a known
    data-bound built-in widget."""
    hit = resolve_widget_alias(*candidates)
    return hit is not None and hit in DATA_BOUND_BUILTIN_IDS


def known_aliases_for(widget_id: str) -> list[str]:
    """Return all aliases that resolve to ``widget_id``. Useful for
    rendering "did you mean…" suggestions and for tests."""
    return [k for k, v in _ALIASES.items() if v == widget_id]


def all_data_bound_widget_ids() -> Iterable[str]:
    return DATA_BOUND_BUILTIN_IDS
