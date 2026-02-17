"""
Schedule document parsing: Excel (.xlsx) and CSV.
Adapts to different column names and date/time formats to create a normalized schedule.
"""
import csv
import io
import logging
import re
from datetime import datetime, timedelta, time as _time
from typing import Any, Dict, List, Optional, Tuple

from django.conf import settings

from .schedule_photo_service import (
    _normalize_role,
    _parse_time,
    _parse_day_of_week,
)

logger = logging.getLogger(__name__)

VALID_ROLES = {c[0] for c in getattr(settings, "STAFF_ROLES_CHOICES", [])}

# Flexible column mapping: header (lower) substring -> our key
NAME_KEYS = ("name", "employee", "staff", "full name", "fullname", "worker", "person")
ROLE_KEYS = ("role", "position", "title", "job", "function", "type")
DATE_KEYS = ("date", "day", "schedule date", "shift date", "dátum")
START_KEYS = ("start", "in", "from", "begin", "clock in", "start time", "time in")
END_KEYS = ("end", "out", "to", "finish", "clock out", "end time", "time out")
DEPT_KEYS = ("department", "dept", "section", "area")


def _normalize_header(h: str) -> str:
    return (h or "").strip().lower().replace("_", " ").replace("-", " ")


def _map_headers(headers: List[str]) -> Dict[str, int]:
    """Return mapping: our_key -> column index (0-based)."""
    out: Dict[str, int] = {}
    for idx, h in enumerate(headers):
        n = _normalize_header(str(h))
        if not n:
            continue
        if any(k in n for k in NAME_KEYS) and "name" not in out:
            out["name"] = idx
        if any(k in n for k in ROLE_KEYS) and "role" not in out:
            out["role"] = idx
        if any(k in n for k in DATE_KEYS) and "date" not in out:
            out["date"] = idx
        if any(k in n for k in START_KEYS) and "start_time" not in out:
            out["start_time"] = idx
        if any(k in n for k in END_KEYS) and "end_time" not in out:
            out["end_time"] = idx
        if any(k in n for k in DEPT_KEYS) and "department" not in out:
            out["department"] = idx
    return out


def _header_score(headers: List[str]) -> int:
    """Heuristic score for a header row: higher means more recognizable schedule columns."""
    m = _map_headers(headers)
    score = 0
    for k in ("name", "role", "department", "date", "start_time", "end_time"):
        if k in m:
            score += 1
    # date/time are most important
    if "date" in m:
        score += 2
    if "start_time" in m or "end_time" in m:
        score += 2
    return score


def _choose_header_row(rows: List[List[Any]], max_scan: int = 6) -> Tuple[List[str], List[List[Any]]]:
    """
    Choose the most likely header row from the first N rows.
    This handles files where row 1 is a title and the header is on row 2/3.
    """
    if not rows:
        return [], []
    best_idx = 0
    best_score = -1
    scan = min(max_scan, len(rows))
    for i in range(scan):
        candidate = [str(h).strip() if h is not None else "" for h in rows[i]]
        score = _header_score(candidate)
        if score > best_score:
            best_score = score
            best_idx = i
    headers = [str(h).strip() if h is not None else "" for h in rows[best_idx]]
    data = [list(r) for r in rows[best_idx + 1 :] if r and any(v is not None and str(v).strip() for v in r)]
    return headers, data


def _parse_date_to_day_of_week(val: Any) -> Optional[int]:
    """Convert date (string or Excel number) to day_of_week 0=Monday .. 6=Sunday."""
    if val is None or val == "":
        return None
    if hasattr(val, "weekday"):
        return val.weekday() % 7
    if isinstance(val, (int, float)):
        # Excel serial date: 1 = 1900-01-01, 44927 = 2023-01-15
        try:
            if val < 1:
                return None
            # Excel serial date: epoch 1899-12-30
            d = datetime(1899, 12, 30) + timedelta(days=int(val))
            return d.weekday() % 7  # 0=Mon .. 6=Sun
        except Exception:
            return None
    s = str(val).strip()
    if not s:
        return None
    # Try day name first
    day = _parse_day_of_week(s)
    if day is not None:
        return day
    # Try ISO YYYY-MM-DD
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", s)
    if m:
        try:
            y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            wd = datetime(y, mo, d).weekday()
            return wd % 7
        except (ValueError, TypeError):
            pass
    # DD/MM/YYYY or MM/DD/YYYY (ambiguous). Use heuristics:
    # - If first part > 12 => DD/MM
    # - If second part > 12 => MM/DD
    # - Else default to DD/MM (common outside US)
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", s)
    if m:
        try:
            a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if a > 12 and 1 <= b <= 12:
                d, mo = a, b
            elif b > 12 and 1 <= a <= 12:
                mo, d = a, b
            else:
                d, mo = a, b
            wd = datetime(y, mo, d).weekday()
            return wd % 7
        except (ValueError, TypeError):
            pass
    return None


def _parse_time_any(val: Any) -> Optional[str]:
    """
    Return HH:MM or None. Supports:
    - strings like "9am", "09:00", "17:30", "09:00:00", "9:30 pm"
    - datetime/time objects from openpyxl
    - excel serial time (float fraction of day)
    """
    if val is None or val == "":
        return None
    if isinstance(val, datetime):
        return f"{val.hour:02d}:{val.minute:02d}"
    if isinstance(val, _time):
        return f"{val.hour:02d}:{val.minute:02d}"
    if isinstance(val, (int, float)) and 0 < float(val) < 1.0:
        # Excel time as fraction of day
        total_minutes = int(round(float(val) * 24 * 60))
        h = (total_minutes // 60) % 24
        m = total_minutes % 60
        return f"{h:02d}:{m:02d}"
    s = str(val).strip()
    if not s:
        return None
    # Handle time range like "09:00-17:00" by taking first token
    if "-" in s or "–" in s:
        parts = re.split(r"\s*[\-–]\s*", s)
        if parts:
            s = parts[0].strip()
    # Accept HH:MM:SS
    m = re.match(r"^(\d{1,2}):(\d{2})(?::\d{2})?\s*(am|pm)?$", s, re.I)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        mer = (m.group(3) or "").lower()
        if mer == "pm" and h < 12:
            h += 12
        if mer == "am" and h == 12:
            h = 0
        return f"{h:02d}:{mi:02d}"
    # 9am, 5pm, 9 a, 9 p
    m = re.match(r"^(\d{1,2})\s*(am|pm|a|p)$", s, re.I)
    if m:
        h = int(m.group(1))
        mer = m.group(2).lower()
        is_pm = mer in ("pm", "p")
        is_am = mer in ("am", "a")
        if is_pm and h < 12:
            h += 12
        if is_am and h == 12:
            h = 0
        return f"{h:02d}:00"
    return _parse_time(s)


def _parse_time_ranges(val: Any) -> List[Tuple[str, str]]:
    """
    Parse a cell that may contain one or more time ranges like:
      "9-5", "9am-5pm", "09:00–17:00", "09:00-13:00, 14:00-18:00"
    Returns list of (start, end) in HH:MM.
    """
    if val is None:
        return []
    if isinstance(val, (datetime, _time, int, float)):
        t = _parse_time_any(val)
        return [(t, t)] if t else []
    s = str(val).strip()
    if not s:
        return []
    low = s.lower()
    if low in ("off", "x", "-", "na", "n/a", "rest", "vacation"):
        return []
    chunks = re.split(r"[,\n;/]+", s)
    out: List[Tuple[str, str]] = []
    for ch in chunks:
        ch = ch.strip()
        if not ch:
            continue
        if "-" not in ch and "–" not in ch:
            # single time
            t = _parse_time_any(ch)
            if t:
                out.append((t, t))
            continue
        parts = re.split(r"\s*[\-–]\s*", ch)
        if len(parts) < 2:
            continue
        st = _parse_time_any(parts[0])
        en = _parse_time_any(parts[1])
        if st and en:
            out.append((st, en))
    return out


def _is_grid_schedule(headers: List[str]) -> Dict[int, int]:
    """
    Detect 'matrix' schedules where columns are days (Mon..Sun or dates).
    Returns mapping of col_idx -> day_of_week for day columns if it looks like a grid.
    """
    day_cols: Dict[int, int] = {}
    for idx, h in enumerate(headers):
        n = _normalize_header(str(h))
        if not n:
            continue
        d = _parse_day_of_week(n)
        if d is None:
            d = _parse_date_to_day_of_week(n)
        if d is not None:
            day_cols[idx] = d
    return day_cols if len(day_cols) >= 3 else {}


def _row_to_shift(row: List[Any], col_map: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """Convert a data row to a normalized shift dict or None if invalid."""
    def get(key: str) -> Optional[str]:
        if key not in col_map:
            return None
        idx = col_map[key]
        if idx >= len(row):
            return None
        v = row[idx]
        if v is None or (isinstance(v, float) and v != v):  # NaN
            return None
        return str(v).strip() or None

    date_val = row[col_map["date"]] if "date" in col_map and col_map["date"] < len(row) else None
    day = _parse_date_to_day_of_week(date_val)
    if day is None and "date" in col_map:
        # If we have a date column but couldn't parse, try "day" as day name in same column
        day = _parse_day_of_week(date_val)
    if day is None:
        return None

    role_raw = get("role") or get("position")
    role = _normalize_role(role_raw) if role_raw else None
    if not role:
        role = "WAITER"

    start_val = row[col_map["start_time"]] if "start_time" in col_map and col_map["start_time"] < len(row) else (get("start_time") or get("start"))
    end_val = row[col_map["end_time"]] if "end_time" in col_map and col_map["end_time"] < len(row) else (get("end_time") or get("end"))
    start = _parse_time_any(start_val) or "09:00"
    end = _parse_time_any(end_val) or "17:00"

    name = get("name") or get("employee") or get("staff")
    dept = get("department")

    return {
        "employee_name": name or None,
        "role": role,
        "department": dept,
        "day_of_week": day,
        "start_time": start,
        "end_time": end,
    }


def _read_csv_rows(content: bytes, filename: str) -> Tuple[List[str], List[List[Any]]]:
    """Decode CSV and return (headers, data_rows). Tries UTF-8 then latin-1."""
    text = content.decode("utf-8-sig", errors="replace")
    if not text.strip():
        return [], []
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return [], []
    headers, data = _choose_header_row(rows)
    return headers, data


def _read_excel_rows(content: bytes, filename: str) -> Tuple[List[str], List[List[Any]]]:
    """Read first sheet of Excel file; return (headers, data_rows)."""
    try:
        import openpyxl
    except ImportError:
        logger.warning("openpyxl not installed; cannot parse Excel files")
        return [], []

    wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.active
    if not ws:
        return [], []
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return [], []
    rows_list: List[List[Any]] = []
    for row in rows:
        rows_list.append([cell for cell in row])
    headers, data = _choose_header_row(rows_list)
    return headers, data


def parse_schedule_document(
    file_bytes: bytes,
    filename: str = "",
    content_type: str = "",
) -> Dict[str, Any]:
    """
    Parse an Excel (.xlsx) or CSV file into normalized schedule data.
    Adapts to different column names (name/employee, role/position, date/day, start/end, department).
    Returns same structure as parse_schedule_image:
      template_name, shifts: [{ employee_name, role, department, day_of_week, start_time, end_time }], departments, roles_seen.
    """
    filename_lower = (filename or "").lower()
    shifts: List[Dict[str, Any]] = []
    template_name = "Imported from file"
    if filename:
        base = filename.replace(".xlsx", "").replace(".xls", "").replace(".csv", "").strip()
        if base:
            template_name = base[:100]

    if ".csv" in filename_lower or (content_type and "csv" in content_type.lower()):
        headers, data_rows = _read_csv_rows(file_bytes, filename)
    elif ".xlsx" in filename_lower or ".xls" in filename_lower or (
        content_type and ("spreadsheet" in content_type.lower() or "excel" in content_type.lower())
    ):
        headers, data_rows = _read_excel_rows(file_bytes, filename)
    else:
        # Try CSV first, then Excel
        try:
            headers, data_rows = _read_csv_rows(file_bytes, filename)
            if not headers and file_bytes:
                headers, data_rows = _read_excel_rows(file_bytes, filename)
        except Exception:
            headers, data_rows = _read_excel_rows(file_bytes, filename)

    if not headers:
        return {"error": "Could not read file or no header row found.", "shifts": [], "template_name": template_name}

    col_map = _map_headers(headers)
    grid_day_cols = _is_grid_schedule(headers)

    # Grid/matrix schedule: columns are days; rows are staff (with optional role/department)
    if grid_day_cols:
        name_idx = col_map.get("name", 0)
        role_idx = col_map.get("role")
        dept_idx = col_map.get("department")
        for row in data_rows:
            if not row:
                continue
            name_val = row[name_idx] if name_idx < len(row) else None
            employee_name = str(name_val).strip() if name_val is not None else ""
            if not employee_name:
                continue
            role_raw = (row[role_idx] if (role_idx is not None and role_idx < len(row)) else None)
            role = _normalize_role(str(role_raw)) if role_raw else None
            if not role:
                role = "WAITER"
            dept_val = row[dept_idx] if (dept_idx is not None and dept_idx < len(row)) else None
            department = str(dept_val).strip() if dept_val is not None else None
            for col_idx, day in grid_day_cols.items():
                if col_idx >= len(row):
                    continue
                cell = row[col_idx]
                for st, en in _parse_time_ranges(cell):
                    # If cell was a single time, skip (cannot infer end). Only accept ranges.
                    if st == en:
                        continue
                    shifts.append({
                        "employee_name": employee_name,
                        "role": role,
                        "department": department,
                        "day_of_week": day,
                        "start_time": st,
                        "end_time": en,
                    })
        departments = list(set(s.get("department") for s in shifts if s.get("department")))
        roles_seen = list(set(s.get("role") for s in shifts if s.get("role")))
        return {
            "template_name": template_name,
            "shifts": shifts,
            "departments": departments,
            "roles_seen": roles_seen,
        }

    if "date" not in col_map and "day" not in col_map:
        # Require at least date/day and (start or end) or role
        if "start_time" not in col_map and "end_time" not in col_map and "role" not in col_map:
            return {
                "error": "No recognizable date/day or time/role columns. Use columns like Date, Employee, Role, Start, End.",
                "shifts": [],
                "template_name": template_name,
            }
    # If no date column, try using first column as day name or assume row order = Mon-Sun
    if "date" not in col_map:
        col_map["date"] = 0  # use first column for date/day

    for row in data_rows:
        if not row:
            continue
        shift = _row_to_shift(row, col_map)
        if shift:
            shifts.append(shift)

    departments = list(set(s.get("department") for s in shifts if s.get("department")))
    roles_seen = list(set(s.get("role") for s in shifts if s.get("role")))

    return {
        "template_name": template_name,
        "shifts": shifts,
        "departments": departments,
        "roles_seen": roles_seen,
    }
