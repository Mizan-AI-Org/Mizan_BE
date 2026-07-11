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

import re
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
        "operations_tasks",
        "purchase_orders",
        "miscellaneous",
        "meetings_reminders",
        "staff_inbox",
        "team_travel",
        "team_medical_service",
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
    # ---- leave / time-off / travel (scheduling lane) --------------------
    "leave request": "team_travel",
    "leave requests": "team_travel",
    "team leave": "team_travel",
    "team leave request": "team_travel",
    "time off": "team_travel",
    "time off request": "team_travel",
    "time off requests": "team_travel",
    "holiday request": "team_travel",
    "holiday requests": "team_travel",
    "conge": "team_travel",
    "conges": "team_travel",
    "congé": "team_travel",
    "congés": "team_travel",
    "demande de conge": "team_travel",
    "demande de congé": "team_travel",
    "demandes de conge": "team_travel",
    "demandes de congé": "team_travel",
    "team travel": "team_travel",
    "team travelling": "team_travel",
    "team traveling": "team_travel",
    "travel request": "team_travel",
    "travel requests": "team_travel",
    "travelling": "team_travel",
    "traveling": "team_travel",
    "travel": "team_travel",
    "voyage": "team_travel",
    "voyages": "team_travel",
    "deplacement": "team_travel",
    "deplacements": "team_travel",
    "team retreat": "team_travel",
    "team retreats": "team_travel",
    "retreat": "team_travel",
    "retreats": "team_travel",
    "offsite": "team_travel",
    "team offsite": "team_travel",
    "اجازة": "team_travel",
    "اجازات": "team_travel",
    # ---- team_medical_service (occupational health lane) ----------------
    "team medical service": "team_medical_service",
    "team medical services": "team_medical_service",
    "medical service": "team_medical_service",
    "medical services": "team_medical_service",
    "team medical": "team_medical_service",
    "team health service": "team_medical_service",
    "health service": "team_medical_service",
    "occupational health": "team_medical_service",
    "medical care": "team_medical_service",
    "clinic visit": "team_medical_service",
    "doctor appointment": "team_medical_service",
    "medical appointment": "team_medical_service",
    "medical certificate": "team_medical_service",
    "service medical": "team_medical_service",
    "services medicaux": "team_medical_service",
    "visite medicale": "team_medical_service",
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
    # ---- operations_tasks (dashboard.Task category=OPERATIONS) ----------
    "operations tasks": "operations_tasks",
    "operations task": "operations_tasks",
    "operations lane": "operations_tasks",
    "ops tasks": "operations_tasks",
    "ops task": "operations_tasks",
    "taches operations": "operations_tasks",
    "tâches opérations": "operations_tasks",
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
    "group": "staff_inbox",
    "groups": "staff_inbox",
    "groupe": "staff_inbox",
    "groupes": "staff_inbox",
    "group request": "staff_inbox",
    "group requests": "staff_inbox",
    "demandes de groupe": "staff_inbox",
    "demande de groupe": "staff_inbox",
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
    "attendance widget": "live_attendance",
    "widget attendance": "live_attendance",
    "who is here": "live_attendance",
    "presence board": "live_attendance",
    # ---- ops_reports ------------------------------------------------------
    "reports": "ops_reports",
    "ops reports": "ops_reports",
    "rapports": "ops_reports",
    "reportes": "ops_reports",
    "تقارير": "ops_reports",
}


_WIDGET_BOILERPLATE_START = re.compile(
    r"^(?:create|add|make|put|show|display|cr[eé]e|cr[eé]er|ajoute|ajouter|zid|agrega)\s+"
    r"(?:a|an|the|un|une|my|le|la|to|for|pour)?\s*",
    re.IGNORECASE,
)


def _strip_widget_boilerplate(s: str) -> str:
    """``Create a Team retreat widget`` → ``team retreat`` for alias lookup."""
    key = _normalise(s)
    if not key:
        return ""
    key = _WIDGET_BOILERPLATE_START.sub("", key)
    key = re.sub(r"\s+widget\s*$", "", key, flags=re.IGNORECASE).strip()
    return key


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


_EXPLICIT_CUSTOM_WIDGET_RE = re.compile(
    r"\bwidget\b[\s\S]{0,40}\b("
    r"called|named|titled|for|pour|about|"
    r"to\s+handle|to\s+track|to\s+manage"
    r")\b",
    re.IGNORECASE,
)

# LanguageMirror / Space may prefix the user turn; never persist those as titles.
_LANGUAGE_DIRECTIVE_BLOCK_RE = re.compile(
    r"\[(?:REPLY LANGUAGE[^\]]*|LANGUAGE DETECTED)\][^\n]*(?:\n(?!\n)[^\n]*)*(?:\n\n)?",
    re.IGNORECASE,
)
_SYSTEM_CONTEXT_BLOCK_RE = re.compile(
    r"\[SYSTEM: (?:PERSISTENT|PARTIAL) CONTEXT\][\s\S]*?"
    r"(?:AGENT_IDENTITY_VERIFIED:\s*TRUE|(?=\n\n\[)|$)",
    re.IGNORECASE,
)


def sanitize_widget_user_text(text: str | None) -> str:
    """Strip injected language / system blocks before title or alias parsing."""
    if not text:
        return ""
    out = _SYSTEM_CONTEXT_BLOCK_RE.sub("", str(text))
    out = _LANGUAGE_DIRECTIVE_BLOCK_RE.sub("", out)
    while True:
        trimmed = out.strip()
        if not re.match(r"^\[(?:REPLY LANGUAGE|LANGUAGE DETECTED)", trimmed, re.I):
            out = trimmed
            break
        idx = trimmed.find("\n\n")
        if idx < 0:
            return ""
        out = trimmed[idx + 2 :]
    return " ".join(out.split()).strip() if out else ""


def is_explicit_custom_widget_request(*texts: str | None) -> bool:
    """True when the manager named a specific custom tile (not a lane alias).

    Examples:
        "create a widget called Gitex Marrakesh" → True
        "create a new widget for next week staff retreat in Bali" → True
        "Create a new widget to handle vehicle petrol expenses" → True
        "create a Purchases widget" → False
    """
    blob = " ".join(
        sanitize_widget_user_text(t) for t in texts if t and str(t).strip()
    )
    if not blob:
        return False
    return bool(_EXPLICIT_CUSTOM_WIDGET_RE.search(blob))


def resolve_widget_alias(*candidates: str | None, strict: bool = False) -> str | None:
    """
    Return the canonical built-in widget id for the first candidate that
    matches a known alias, or ``None`` if nothing matches.

    Multiple candidates can be passed (e.g. title + subtitle +
    category_name). The first matching candidate wins.

    When ``strict=True``, only exact normalised keys match — no fuzzy
    substring scan. Use this for explicit custom titles like
    "PRELEVEMENTS STOCK" that must not match the word "stock".

    >>> resolve_widget_alias("Purchases")
    'purchase_orders'
    >>> resolve_widget_alias("Achats", "Supplier orders")
    'purchase_orders'
    >>> resolve_widget_alias("PRELEVEMENTS STOCK", strict=True)
    None
    >>> resolve_widget_alias("Random shortcut")
    None
    """
    for raw in candidates:
        if not raw:
            continue
        variant_keys: list[str] = []
        for variant in (raw, _strip_widget_boilerplate(raw)):
            key = _normalise(variant)
            if key and key not in variant_keys:
                variant_keys.append(key)
        for key in variant_keys:
            hit = _ALIASES.get(key)
            if hit:
                return hit
            # Also try the singular if the key looks plural and isn't
            # already in the table — cheap "Purchases" -> "purchase" hop
            # that doesn't require duplicating every entry.
            if key.endswith("s") and key[:-1] in _ALIASES:
                return _ALIASES[key[:-1]]
            if strict:
                continue
            # Phrase / keyword match: managers often paste a full sentence as the
            # tile title ("Créé un widget pour les groupes…"). Exact-key lookup
            # misses those; scan for known multi-word aliases (longest wins).
            relaxed = re.sub(r"[^\w\s]+", " ", key, flags=re.UNICODE)
            relaxed = " ".join(relaxed.split())
            padded_variants = {f" {key} ", f" {relaxed} "}
            _SHORT_OK = frozenset({"hr", "rh", "po"})
            best_len = 0
            best_wid: str | None = None
            for pad in padded_variants:
                for alias_key, wid in _ALIASES.items():
                    if len(alias_key) < 3 and alias_key not in _SHORT_OK:
                        continue
                    if f" {alias_key} " in pad and len(alias_key) > best_len:
                        best_len = len(alias_key)
                        best_wid = wid
            if best_wid:
                return best_wid
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
