"""
Heuristic extraction of guest order fields from voice/text transcripts (WhatsApp → Miya).

Fills StaffCapturedOrder fields so Today's Orders matches manual entry layout when possible.
Explicit API values always override parsed fields.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional


def _strip_len(s: str, n: int) -> str:
    return (s or "")[:n].strip()


def parse_guest_order_from_transcript(text: str) -> Dict[str, str]:
    """
    Returns keys: items_summary, customer_name, customer_phone, order_type,
    table_or_location, dietary_notes, special_instructions.
    """
    raw = (text or "").strip()
    out: Dict[str, str] = {
        "items_summary": raw[:8000] if raw else "",
        "customer_name": "",
        "customer_phone": "",
        "order_type": "DINE_IN",
        "table_or_location": "",
        "dietary_notes": "",
        "special_instructions": "",
    }
    if not raw:
        return out

    t_lower = raw.lower()
    working = raw

    # --- Order type ---
    if any(
        x in t_lower
        for x in (
            "delivery",
            "deliver to",
            "deliver at",
            "livraison",
            "توصيل",
            "توصيل ",
        )
    ):
        out["order_type"] = "DELIVERY"
    elif any(
        x in t_lower
        for x in (
            "takeout",
            "take out",
            "take-away",
            "takeaway",
            "pickup",
            "pick up",
            "pick-up",
            "à emporter",
            "a emporter",
            "emporter",
            "retrait",
            "سفري",
        )
    ):
        out["order_type"] = "TAKEOUT"

    # --- Phone (longest plausible digit run) ---
    best_digits = ""
    for m in re.finditer(r"[\d\s\-\+\(\)]{10,}", raw):
        chunk = m.group(0)
        digits = "".join(c for c in chunk if c.isdigit())
        if len(digits) >= 8 and len(digits) > len(best_digits):
            best_digits = digits
    if best_digits:
        out["customer_phone"] = best_digits[:40]
        # Remove phone-like span from working for cleaner items line
        for m in re.finditer(r"[\d\s\-\+\(\)]{10,}", working):
            digits = "".join(c for c in m.group(0) if c.isdigit())
            if digits == best_digits[: len(digits)] or best_digits.startswith(digits):
                working = working.replace(m.group(0), " ", 1)
                break

    # --- Customer name (multilingual patterns) ---
    name_patterns = [
        r"(?:customer|guest|client)\s+name\s+is\s*[: ]*\s*([^\n\.,]+)",
        r"(?:customer|guest|client)\s*:\s*([^\n\.,]+)",
        r"(?:nom|الاسم)\s*(?:du\s+client\s*)?[: ]*\s*([^\n\.,]+)",
        r"\bname\s+is\s+([^\n\.,]+)",
        r"\bfor\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*(?:,|\.|phone|table|at\b|$)",
    ]
    for pat in name_patterns:
        m = re.search(pat, raw, flags=re.IGNORECASE)
        if m:
            name = m.group(1).strip()
            name = re.split(r"\b(phone|table|delivery|takeout|pickup|order)\b", name, flags=re.I)[0].strip()
            if 2 <= len(name) <= 200:
                out["customer_name"] = name[:255]
                working = working.replace(m.group(0), " ", 1)
                break

    # --- Table / location ---
    loc_patterns = [
        r"\btable\s*(?:number)?\s*#?\s*([A-Za-z0-9][A-Za-z0-9\s\-]{0,40})",
        r"\bbooth\s*#?\s*([A-Za-z0-9][A-Za-z0-9\s\-]{0,20})",
        r"\b(?:at|@)\s+([A-Z][A-Za-z0-9]{2,24})\b",
        r"\b(?:counter|bar)\s*#?\s*([A-Za-z0-9]{1,20})",
    ]
    for pat in loc_patterns:
        m = re.search(pat, raw)
        if m:
            loc = m.group(1).strip()
            if len(loc) >= 2:
                out["table_or_location"] = loc[:120]
                working = working.replace(m.group(0), " ", 1)
                break

    # --- Dietary / allergens (line or phrase) ---
    diet_bits = []
    diet_kw = (
        "allerg",
        "nut",
        "peanut",
        "dairy",
        "lactose",
        "gluten",
        "vegan",
        "vegetarian",
        "halal",
        "kosher",
        "no onion",
        "no garlic",
        "shellfish",
        "sesame",
    )
    for line in raw.splitlines():
        low = line.lower().strip()
        if any(k in low for k in diet_kw):
            diet_bits.append(line.strip())
    if diet_bits:
        out["dietary_notes"] = "; ".join(diet_bits)[:2000]

    # --- Special instructions ---
    spec_patterns = [
        r"(?:special\s*instructions?|notes?|remarque)\s*:\s*([^\n]+)",
        r"\b(?:extra|sans|no\s+|without\s+)([^\n]{3,120})",
    ]
    specs = []
    for pat in spec_patterns:
        for m in re.finditer(pat, raw, flags=re.IGNORECASE):
            snippet = m.group(1).strip() if m.lastindex else m.group(0).strip()
            if len(snippet) >= 3:
                specs.append(snippet[:500])
    if specs:
        out["special_instructions"] = " | ".join(specs)[:2000]

    # --- Items summary: prefer compact line without duplicated metadata ---
    cleaned = re.sub(r"\s+", " ", working).strip(" ,.;")
    if len(cleaned) >= 3:
        out["items_summary"] = cleaned[:8000]
    else:
        out["items_summary"] = raw[:8000]

    return out


def merge_parsed_order_fields(
    transcript: str,
    overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    """
    Parse transcript, then apply overrides where the override is non-empty string.
    """
    base = parse_guest_order_from_transcript(transcript)
    o = overrides or {}
    for key in (
        "items_summary",
        "customer_name",
        "customer_phone",
        "order_type",
        "table_or_location",
        "dietary_notes",
        "special_instructions",
    ):
        val = o.get(key)
        if val is None:
            continue
        s = str(val).strip()
        if s:
            base[key] = s
    # Normalize lengths
    base["items_summary"] = _strip_len(base["items_summary"], 8000)
    base["customer_name"] = _strip_len(base["customer_name"], 255)
    base["customer_phone"] = _strip_len(base["customer_phone"], 40)
    base["table_or_location"] = _strip_len(base["table_or_location"], 120)
    base["dietary_notes"] = _strip_len(base["dietary_notes"], 2000)
    base["special_instructions"] = _strip_len(base["special_instructions"], 2000)
    ot = (base.get("order_type") or "DINE_IN").upper()
    if ot not in ("DINE_IN", "TAKEOUT", "DELIVERY", "OTHER"):
        ot = "DINE_IN"
    base["order_type"] = ot
    return base
