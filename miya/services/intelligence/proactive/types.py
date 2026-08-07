"""Phase 6 — Proactive Operational Intelligence types."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Severity(str, Enum):
    CRITICAL = "CRITICAL"  # 🔴
    HIGH = "HIGH"  # 🟠
    MEDIUM = "MEDIUM"  # 💰 / 👥
    LOW = "LOW"  # 📄 / 📅
    INFO = "INFO"


class AttentionCategory(str, Enum):
    OPEN_INCIDENTS = "open_incidents"
    OVERDUE_TASKS = "overdue_tasks"
    BLOCKED_TASKS = "blocked_tasks"
    PENDING_APPROVALS = "pending_approvals"
    EXPIRING_DOCUMENTS = "expiring_documents"
    UPCOMING_MEETINGS = "upcoming_meetings"
    UNCOMPLETED_CHECKLISTS = "uncompleted_checklists"
    STAFF_ISSUES = "staff_issues"
    PAYMENT_ISSUES = "payment_issues"


SEVERITY_EMOJI = {
    Severity.CRITICAL: "🔴",
    Severity.HIGH: "🟠",
    Severity.MEDIUM: "💰",
    Severity.LOW: "📄",
    Severity.INFO: "📅",
}

CATEGORY_EMOJI = {
    AttentionCategory.OPEN_INCIDENTS: "🔴",
    AttentionCategory.OVERDUE_TASKS: "🟠",
    AttentionCategory.BLOCKED_TASKS: "🟠",
    AttentionCategory.PENDING_APPROVALS: "💰",
    AttentionCategory.EXPIRING_DOCUMENTS: "📄",
    AttentionCategory.UPCOMING_MEETINGS: "📅",
    AttentionCategory.UNCOMPLETED_CHECKLISTS: "👥",
    AttentionCategory.STAFF_ISSUES: "👥",
    AttentionCategory.PAYMENT_ISSUES: "💰",
}

CATEGORY_HANDLE_ALIASES = {
    "incident": AttentionCategory.OPEN_INCIDENTS,
    "incidents": AttentionCategory.OPEN_INCIDENTS,
    "overdue": AttentionCategory.OVERDUE_TASKS,
    "task": AttentionCategory.OVERDUE_TASKS,
    "tasks": AttentionCategory.OVERDUE_TASKS,
    "blocked": AttentionCategory.BLOCKED_TASKS,
    "invoice": AttentionCategory.PENDING_APPROVALS,
    "invoices": AttentionCategory.PENDING_APPROVALS,
    "approval": AttentionCategory.PENDING_APPROVALS,
    "approvals": AttentionCategory.PENDING_APPROVALS,
    "insurance": AttentionCategory.EXPIRING_DOCUMENTS,
    "document": AttentionCategory.EXPIRING_DOCUMENTS,
    "documents": AttentionCategory.EXPIRING_DOCUMENTS,
    "meeting": AttentionCategory.UPCOMING_MEETINGS,
    "meetings": AttentionCategory.UPCOMING_MEETINGS,
    "checklist": AttentionCategory.UNCOMPLETED_CHECKLISTS,
    "checklists": AttentionCategory.UNCOMPLETED_CHECKLISTS,
    "staff": AttentionCategory.STAFF_ISSUES,
    "payment": AttentionCategory.PAYMENT_ISSUES,
    "payments": AttentionCategory.PAYMENT_ISSUES,
}


@dataclass
class AttentionItem:
    category: AttentionCategory
    severity: Severity
    title: str
    count: int = 1
    entity_ids: list[str] = field(default_factory=list)
    detail: str = ""
    actionable: bool = True
    handle_hint: str = ""  # e.g. "invoices"

    def fingerprint_parts(self) -> list[str]:
        ids = sorted(str(i) for i in self.entity_ids if i)
        return [self.category.value, self.severity.value, str(self.count), *ids]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["category"] = self.category.value
        d["severity"] = self.severity.value
        return d

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "AttentionItem":
        return cls(
            category=AttentionCategory(row["category"]),
            severity=Severity(row["severity"]),
            title=str(row.get("title") or ""),
            count=int(row.get("count") or 1),
            entity_ids=[str(x) for x in (row.get("entity_ids") or [])],
            detail=str(row.get("detail") or ""),
            actionable=bool(row.get("actionable", True)),
            handle_hint=str(row.get("handle_hint") or ""),
        )


@dataclass
class DailyBriefing:
    restaurant_id: str
    restaurant_name: str = ""
    period: str = "morning"
    items: list[AttentionItem] = field(default_factory=list)
    fingerprint: str = ""
    generated_at: str = ""
    offer_handle: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "restaurant_id": self.restaurant_id,
            "restaurant_name": self.restaurant_name,
            "period": self.period,
            "items": [i.to_dict() for i in self.items],
            "fingerprint": self.fingerprint,
            "generated_at": self.generated_at,
            "offer_handle": self.offer_handle,
            "has_attention": bool(self.items),
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "DailyBriefing":
        return cls(
            restaurant_id=str(row.get("restaurant_id") or ""),
            restaurant_name=str(row.get("restaurant_name") or ""),
            period=str(row.get("period") or "morning"),
            items=[AttentionItem.from_dict(i) for i in (row.get("items") or [])],
            fingerprint=str(row.get("fingerprint") or ""),
            generated_at=str(row.get("generated_at") or ""),
            offer_handle=bool(row.get("offer_handle", True)),
        )
