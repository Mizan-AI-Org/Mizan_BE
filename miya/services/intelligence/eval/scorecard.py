"""Release scorecard — aggregate metrics and regression gates."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from miya.services.intelligence.eval.types import (
    ALL_METRICS,
    CaseResult,
    MetricName,
    Scorecard,
)

# Minimum scores for release — critical metrics must not regress.
RELEASE_TARGETS: dict[str, float] = {
    "critical_pass_rate": 1.0,
    "overall_pass_rate": 0.95,
    "intent_accuracy": 0.98,
    "entity_resolution_accuracy": 0.95,
    "tool_selection_accuracy": 0.97,
    "tool_argument_accuracy": 0.95,
    "permission_accuracy": 1.0,
    "execution_accuracy": 0.97,
    "database_state_accuracy": 1.0,
    "verification_accuracy": 1.0,
    "final_response_accuracy": 0.90,
    "duplicate_execution": 1.0,
    "hallucination": 1.0,
}

# Metrics that block release when below target (even on non-critical cases).
CRITICAL_GATE_METRICS: frozenset[str] = frozenset(
    {
        "intent_accuracy",
        "tool_selection_accuracy",
        "verification_accuracy",
        "hallucination",
        "permission_accuracy",
        "duplicate_execution",
        "database_state_accuracy",
    }
)


@dataclass
class GateReport:
    passed: bool
    regressions: list[str] = field(default_factory=list)
    by_metric: dict[str, float] = field(default_factory=dict)
    targets: dict[str, float] = field(default_factory=lambda: dict(RELEASE_TARGETS))

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "regressions": list(self.regressions),
            "by_metric": dict(self.by_metric),
            "targets": dict(self.targets),
        }


def _metric_aggregate(results: list[CaseResult], metric: MetricName) -> float:
    scores: list[float] = []
    for cr in results:
        for m in cr.metrics:
            if m.name == metric and m.applicable and m.passed is not None:
                scores.append(m.score)
    return sum(scores) / len(scores) if scores else 1.0


def build_scorecard(results: list[CaseResult]) -> Scorecard:
    """Aggregate case results into a release scorecard."""
    passed = [r for r in results if r.passed]
    failed = [r for r in results if not r.passed]
    critical = [r for r in results if r.critical]
    critical_passed = [r for r in critical if r.passed]
    critical_failed = [r for r in critical if not r.passed]

    by_metric = {m.value: _metric_aggregate(results, m) for m in ALL_METRICS}
    by_category: dict[str, float] = {}
    categories = {r.category for r in results}
    for cat in categories:
        cat_results = [r for r in results if r.category == cat]
        cat_pass = sum(1 for r in cat_results if r.passed)
        by_category[cat] = cat_pass / len(cat_results) if cat_results else 1.0

    regressions: list[str] = []
    overall_pass_rate = len(passed) / len(results) if results else 1.0
    critical_pass_rate = len(critical_passed) / len(critical) if critical else 1.0

    if critical_pass_rate < RELEASE_TARGETS["critical_pass_rate"]:
        regressions.append(
            f"critical_pass_rate {critical_pass_rate:.3f} < {RELEASE_TARGETS['critical_pass_rate']}"
        )
    if overall_pass_rate < RELEASE_TARGETS["overall_pass_rate"]:
        regressions.append(
            f"overall_pass_rate {overall_pass_rate:.3f} < {RELEASE_TARGETS['overall_pass_rate']}"
        )

    for metric_name, target in RELEASE_TARGETS.items():
        if metric_name in ("critical_pass_rate", "overall_pass_rate"):
            continue
        actual = by_metric.get(metric_name, 1.0)
        if metric_name in CRITICAL_GATE_METRICS and actual < target:
            regressions.append(f"{metric_name} {actual:.3f} < {target}")

    gate_passed = len(regressions) == 0

    return Scorecard(
        total_cases=len(results),
        passed_cases=len(passed),
        failed_cases=len(failed),
        critical_passed=len(critical_passed),
        critical_failed=len(critical_failed),
        by_metric=by_metric,
        by_category=by_category,
        case_results=results,
        regressions=regressions,
        gate_passed=gate_passed,
    )


def assert_release_gate(scorecard: Scorecard) -> GateReport:
    """Return gate report; raises AssertionError-friendly regressions list."""
    return GateReport(
        passed=scorecard.gate_passed,
        regressions=list(scorecard.regressions),
        by_metric=dict(scorecard.by_metric),
    )
