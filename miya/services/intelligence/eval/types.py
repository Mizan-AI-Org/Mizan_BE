"""Phase 9 evaluation types — cases, traces, metric scores."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class EvalCategory(str, Enum):
    TASKS = "TASKS"
    INCIDENTS = "INCIDENTS"
    STAFF = "STAFF"
    ROUTING = "ROUTING"
    DOCUMENTS = "DOCUMENTS"
    OCR = "OCR"
    INVOICES = "INVOICES"
    APPROVALS = "APPROVALS"
    REMINDERS = "REMINDERS"
    MEETINGS = "MEETINGS"
    MULTI_ESTABLISHMENT = "MULTI-ESTABLISHMENT"
    WHATSAPP = "WHATSAPP"
    VOICE = "VOICE"
    AMBIGUOUS = "AMBIGUOUS REQUESTS"
    PERMISSIONS = "PERMISSION TESTS"
    PRODUCTION_BUG = "PRODUCTION_BUG"


class MetricName(str, Enum):
    INTENT = "intent_accuracy"
    ENTITY = "entity_resolution_accuracy"
    CONTEXT = "context_accuracy"
    TOOL_SELECTION = "tool_selection_accuracy"
    TOOL_ARGS = "tool_argument_accuracy"
    PERMISSION = "permission_accuracy"
    EXECUTION = "execution_accuracy"
    DATABASE = "database_state_accuracy"
    VERIFICATION = "verification_accuracy"
    RESPONSE = "final_response_accuracy"
    DUPLICATE = "duplicate_execution"
    HALLUCINATION = "hallucination"
    LATENCY = "latency"
    TOKEN_COST = "token_cost_usage"


ALL_METRICS: tuple[MetricName, ...] = tuple(MetricName)


@dataclass
class EvalExpectation:
    """Ground truth for a scenario."""

    intent: str | None = None  # e.g. COMPLETE (or COMPLETE_TASK alias)
    entity_type: str | None = None  # task, incident, …
    entity_query: str | None = None  # "decoration"
    entity_id: str | None = None
    tool: str | None = None  # complete_task
    tool_args_contains: dict[str, Any] = field(default_factory=dict)
    context: dict[str, Any] = field(default_factory=dict)
    permission_allowed: bool | None = None
    clarify: bool | None = None  # True → must ask, not execute
    db_state: dict[str, Any] = field(default_factory=dict)  # {"status": "COMPLETED"}
    response_must_contain: list[str] = field(default_factory=list)
    response_must_not_contain: list[str] = field(default_factory=list)
    verified: bool | None = None
    max_tool_calls: int = 1
    require_mutation_tool: bool = False  # FAIL if search-only
    forbid_hallucination: bool = True
    max_latency_ms: float | None = 5000.0
    max_tokens: int | None = None
    max_cost_usd: float | None = None


@dataclass
class WorldEntity:
    """In-memory fixture entity for deterministic simulation."""

    id: str
    kind: str  # task, incident, …
    title: str = ""
    status: str = ""
    location_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class EvalTier(str, Enum):
    """How deeply the case is exercised."""

    SIMULATION = "simulation"  # classify → resolve → mocked workflow execution
    PLANNING = "planning"  # classify + resolve only (agent-deferred NL)
    OBSERVED = "observed"  # score a recorded production trace
    ESTABLISHMENT = "establishment"  # multi-est gate / context switch


@dataclass
class EvalCase:
    id: str
    category: EvalCategory
    input: str
    expected: EvalExpectation
    world: list[WorldEntity] = field(default_factory=list)
    session: dict[str, Any] = field(default_factory=dict)
    channel: str = "dashboard"
    role: str = "MANAGER"
    critical: bool = False
    tier: EvalTier = EvalTier.SIMULATION
    production_bug: str | None = None
    notes: str = ""
    # If set, judge this recorded trace instead of simulating (prod replay / anti-pattern).
    observed: dict[str, Any] | None = None
    # When True, the case documents a FAIL pattern — overall must be False on observed.
    expect_overall_fail: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category.value,
            "input": self.input,
            "critical": self.critical,
            "production_bug": self.production_bug,
        }


@dataclass
class EvalTrace:
    """Observed Miya behavior for one case."""

    intent: str | None = None
    entity_type: str | None = None
    entity_query: str | None = None
    entity_id: str | None = None
    context: dict[str, Any] = field(default_factory=dict)
    tools_called: list[dict[str, Any]] = field(default_factory=list)  # {name, args}
    permission_denied: bool = False
    clarified: bool = False
    db_before: dict[str, Any] = field(default_factory=dict)
    db_after: dict[str, Any] = field(default_factory=dict)
    verified: bool = False
    response: str = ""
    claimed_success: bool = False
    latency_ms: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    hallucination_flags: list[str] = field(default_factory=list)
    search_only: bool = False
    error: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> EvalTrace:
        d = dict(data or {})
        return cls(
            intent=d.get("intent"),
            entity_type=d.get("entity_type"),
            entity_query=d.get("entity_query"),
            entity_id=d.get("entity_id"),
            context=dict(d.get("context") or {}),
            tools_called=list(d.get("tools_called") or []),
            permission_denied=bool(d.get("permission_denied")),
            clarified=bool(d.get("clarified")),
            db_before=dict(d.get("db_before") or {}),
            db_after=dict(d.get("db_after") or {}),
            verified=bool(d.get("verified")),
            response=str(d.get("response") or ""),
            claimed_success=bool(d.get("claimed_success")),
            latency_ms=float(d.get("latency_ms") or 0),
            tokens_in=int(d.get("tokens_in") or 0),
            tokens_out=int(d.get("tokens_out") or 0),
            cost_usd=float(d.get("cost_usd") or 0),
            hallucination_flags=list(d.get("hallucination_flags") or []),
            search_only=bool(d.get("search_only")),
            error=str(d.get("error") or ""),
        )


@dataclass
class MetricScore:
    name: MetricName
    passed: bool | None  # None = not applicable
    score: float  # 0.0–1.0 (1.0 = perfect / N/A treated as 1.0 for aggregates when skipped)
    detail: str = ""
    applicable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name.value,
            "passed": self.passed,
            "score": self.score,
            "detail": self.detail,
            "applicable": self.applicable,
        }


@dataclass
class CaseResult:
    case_id: str
    category: str
    critical: bool
    metrics: list[MetricScore]
    overall: float
    passed: bool
    failures: list[str] = field(default_factory=list)
    trace: EvalTrace | None = None
    production_bug: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "category": self.category,
            "critical": self.critical,
            "overall": self.overall,
            "passed": self.passed,
            "failures": list(self.failures),
            "metrics": [m.to_dict() for m in self.metrics],
            "production_bug": self.production_bug,
        }


@dataclass
class Scorecard:
    """Aggregated suite scores vs release targets."""

    total_cases: int
    passed_cases: int
    failed_cases: int
    critical_passed: int
    critical_failed: int
    by_metric: dict[str, float] = field(default_factory=dict)
    by_category: dict[str, float] = field(default_factory=dict)
    case_results: list[CaseResult] = field(default_factory=list)
    regressions: list[str] = field(default_factory=list)
    gate_passed: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "critical_passed": self.critical_passed,
            "critical_failed": self.critical_failed,
            "by_metric": dict(self.by_metric),
            "by_category": dict(self.by_category),
            "regressions": list(self.regressions),
            "gate_passed": self.gate_passed,
            "pass_rate": (self.passed_cases / self.total_cases) if self.total_cases else 0.0,
        }
