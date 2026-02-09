"""
Schedule photo parsing service.
Uses OpenAI Vision to extract shifts, roles, departments, and employee names from a schedule image.
"""
import base64
import json
import logging
import re
from typing import Any, Dict, List, Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Role normalization: map common labels from photos to STAFF_ROLES_CHOICES
ROLE_ALIASES = {
    'super admin': 'SUPER_ADMIN',
    'admin': 'ADMIN',
    'manager': 'MANAGER',
    'chef': 'CHEF',
    'cook': 'CHEF',
    'waiter': 'WAITER',
    'waitress': 'WAITER',
    'server': 'WAITER',
    'kitchen help': 'KITCHEN_HELP',
    'prep': 'KITCHEN_HELP',
    'bartender': 'BARTENDER',
    'bar': 'BARTENDER',
    'receptionist': 'RECEPTIONIST',
    'host': 'RECEPTIONIST',
    'cleaner': 'CLEANER',
    'cleaning': 'CLEANER',
    'security': 'SECURITY',
    'cashier': 'CASHIER',
    'register': 'CASHIER',
}

VALID_ROLES = {c[0] for c in getattr(settings, 'STAFF_ROLES_CHOICES', [])}


def _normalize_role(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    norm = (raw or '').strip().lower()
    if norm in ROLE_ALIASES:
        return ROLE_ALIASES[norm]
    # Try direct match
    for choice in VALID_ROLES:
        if choice.lower() == norm or choice.replace('_', ' ').lower() == norm:
            return choice
    return None


def _parse_time(s: Optional[str]) -> Optional[str]:
    """Return HH:MM or None. Handles 9am, 9:00, 17:00, 5pm, etc."""
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    # 24h: 09:00, 17:30
    m = re.match(r'^(\d{1,2}):(\d{2})\s*(?:am|pm)?$', s, re.I)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 'pm' in s.lower() and h < 12:
            h += 12
        if 'am' in s.lower() and h == 12:
            h = 0
        return f"{h:02d}:{mi:02d}"
    # 9am, 5pm
    m = re.match(r'^(\d{1,2})\s*(am|pm)$', s, re.I)
    if m:
        h = int(m.group(1))
        if m.group(2).lower() == 'pm' and h < 12:
            h += 12
        if m.group(2).lower() == 'am' and h == 12:
            h = 0
        return f"{h:02d}:00"
    return None


def _parse_day_of_week(s: Optional[str]) -> Optional[int]:
    """Return 0=Monday .. 6=Sunday."""
    if s is None:
        return None
    if isinstance(s, int) and 0 <= s <= 6:
        return s
    t = (str(s)).strip().lower()[:3]
    days = {'mon': 0, 'tue': 1, 'wed': 2, 'thu': 3, 'fri': 4, 'sat': 5, 'sun': 6}
    return days.get(t)


def parse_schedule_image(image_bytes: bytes, content_type: str = "image/jpeg") -> Dict[str, Any]:
    """
    Send image to OpenAI Vision and parse response into structured schedule data.
    Returns:
        {
            "template_name": str (optional),
            "shifts": [
                {
                    "employee_name": str (optional),
                    "role": str (normalized),
                    "department": str (optional),
                    "day_of_week": int 0-6,
                    "start_time": "HH:MM",
                    "end_time": "HH:MM",
                }
            ],
            "departments": list (optional),
            "roles_seen": list (optional),
            "raw_response": str (optional, for debugging),
        }
    """
    api_key = getattr(settings, 'OPENAI_API_KEY', '') or ''
    if not api_key:
        return {
            "error": "OPENAI_API_KEY not configured",
            "shifts": [],
        }

    prompt = """You are analyzing a photo of a work schedule (restaurant, retail, or similar).
Extract every shift/row you can see. For each row, provide:
- employee_name: full name of the person (if visible)
- role: job role (e.g. Chef, Waiter, Manager, Bartender, Cashier, Kitchen Help, etc.)
- department: if the schedule has departments (e.g. Kitchen, Front of House)
- day_of_week: 0=Monday, 1=Tuesday, 2=Wednesday, 3=Thursday, 4=Friday, 5=Saturday, 6=Sunday
- start_time: time in 24h "HH:MM" (e.g. "09:00", "17:30")
- end_time: time in 24h "HH:MM"

If the schedule uses day names or dates, map them to day_of_week (0-6).
If times are in 12h (e.g. 9am-5pm), convert to 24h.
If the image shows a week range or title, set template_name to a short name for this schedule (e.g. "Week of Jan 15" or "Main Floor Schedule").

Respond with ONLY a valid JSON object, no markdown or explanation, in this exact shape:
{
  "template_name": "optional short name",
  "shifts": [
    {
      "employee_name": "John Doe",
      "role": "Chef",
      "department": "Kitchen",
      "day_of_week": 0,
      "start_time": "09:00",
      "end_time": "17:00"
    }
  ],
  "departments": ["Kitchen", "FOH"],
  "roles_seen": ["Chef", "Waiter"]
}
If you cannot read the image or there are no shifts, return {"shifts": [], "template_name": null}.
"""

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{content_type};base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 2000,
    }

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
    except requests.RequestException as e:
        logger.exception("OpenAI Vision request failed")
        return {"error": str(e), "shifts": []}

    if r.status_code != 200:
        return {"error": f"Vision API error: {r.status_code}", "shifts": []}

    data = r.json()
    choices = data.get("choices") or []
    if not choices:
        return {"shifts": []}

    text = (choices[0].get("message") or {}).get("content") or ""
    text = text.strip()
    # Strip markdown code block if present
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return {"error": "Invalid JSON from vision", "raw_response": text[:500], "shifts": []}

    shifts_in = parsed.get("shifts") or []
    normalized = []
    for s in shifts_in:
        if not isinstance(s, dict):
            continue
        role = _normalize_role(s.get("role"))
        if not role:
            role = _normalize_role(s.get("position")) or "WAITER"  # fallback
        day = _parse_day_of_week(s.get("day_of_week"))
        if day is None:
            continue
        start = _parse_time(s.get("start_time")) or "09:00"
        end = _parse_time(s.get("end_time")) or "17:00"
        normalized.append({
            "employee_name": (s.get("employee_name") or s.get("name") or "").strip() or None,
            "role": role,
            "department": (s.get("department") or "").strip() or None,
            "day_of_week": day,
            "start_time": start,
            "end_time": end,
        })

    return {
        "template_name": parsed.get("template_name") or "Imported from photo",
        "shifts": normalized,
        "departments": list(set(d.get("department") for d in shifts_in if d.get("department"))),
        "roles_seen": list(set(d.get("role") for d in shifts_in if d.get("role"))),
        "raw_response": text[:300] if logger.isEnabledFor(logging.DEBUG) else None,
    }
