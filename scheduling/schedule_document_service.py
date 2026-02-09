"""
Schedule document parsing: Excel (.xlsx) and CSV.
Adapts to different column names and date/time formats to create a normalized schedule.
"""
import csv
import io
import logging
import re
from datetime import datetime, timedelta
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
    # DD/MM/YYYY or DD-MM-YYYY
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", s)
    if m:
        try:
            d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            wd = datetime(y, mo, d).weekday()
            return wd % 7
        except (ValueError, TypeError):
            pass
    # MM/DD/YYYY
    m = re.match(r"^(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", s)
    if m:
        try:
            mo, d, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            wd = datetime(y, mo, d).weekday()
            return wd % 7
        except (ValueError, TypeError):
            pass
    return None


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

    start_str = get("start_time") or get("start")
    end_str = get("end_time") or get("end")
    start = _parse_time(start_str) or "09:00"
    end = _parse_time(end_str) or "17:00"

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
    headers = [str(h).strip() for h in rows[0]]
    data = [row for row in rows[1:] if any(cell for cell in row)]
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
    headers = [str(h).strip() if h is not None else "" for h in rows[0]]
    data = []
    for row in rows[1:]:
        row_list = [cell for cell in row]
        if any(v is not None and str(v).strip() for v in row_list):
            data.append(row_list)
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
