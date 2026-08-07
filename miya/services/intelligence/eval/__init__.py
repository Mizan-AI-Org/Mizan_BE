"""
Phase 9 — Miya Evaluation System.

Permanent regression suite for operational intelligence quality.
Every production bug becomes an eval case; releases must pass scorecard gates.
"""
from __future__ import annotations

from miya.services.intelligence.eval.cases import (
    ALL_EVAL_CASES,
    critical_cases,
    planning_cases,
    simulation_cases,
)
from miya.services.intelligence.eval.runner import run_eval_case, run_eval_suite
from miya.services.intelligence.eval.scorecard import (
    CRITICAL_GATE_METRICS,
    RELEASE_TARGETS,
    assert_release_gate,
    build_scorecard,
)
from miya.services.intelligence.eval.scorer import score_case
from miya.services.intelligence.eval.types import (
    ALL_METRICS,
    CaseResult,
    EvalCase,
    EvalCategory,
    EvalExpectation,
    EvalTier,
    EvalTrace,
    MetricName,
    MetricScore,
    Scorecard,
    WorldEntity,
)

__all__ = [
    "ALL_EVAL_CASES",
    "ALL_METRICS",
    "CRITICAL_GATE_METRICS",
    "CaseResult",
    "EvalCase",
    "EvalCategory",
    "EvalExpectation",
    "EvalTier",
    "EvalTrace",
    "MetricName",
    "MetricScore",
    "RELEASE_TARGETS",
    "Scorecard",
    "WorldEntity",
    "assert_release_gate",
    "build_scorecard",
    "critical_cases",
    "run_eval_case",
    "run_eval_suite",
    "score_case",
    "planning_cases",
    "simulation_cases",
]
