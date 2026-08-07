"""Load all permanent eval cases."""
from __future__ import annotations

from miya.services.intelligence.eval.cases.dataset import EVAL_CASES
from miya.services.intelligence.eval.cases.phase12_expansion import PHASE12_EXPANSION_CASES
from miya.services.intelligence.eval.cases.production_bugs import PRODUCTION_BUG_CASES
from miya.services.intelligence.eval.types import EvalCase, EvalCategory, EvalTier

ALL_EVAL_CASES: list[EvalCase] = EVAL_CASES + PRODUCTION_BUG_CASES + PHASE12_EXPANSION_CASES


def cases_by_category(category: EvalCategory) -> list[EvalCase]:
    return [c for c in ALL_EVAL_CASES if c.category == category]


def critical_cases() -> list[EvalCase]:
    return [c for c in ALL_EVAL_CASES if c.critical]


def simulation_cases() -> list[EvalCase]:
    """Cases run through live simulation (excludes observed-only replays and planning-only)."""
    return [
        c
        for c in ALL_EVAL_CASES
        if c.tier not in (EvalTier.OBSERVED, EvalTier.PLANNING)
        and not c.observed
        and not c.expect_overall_fail
    ]


def planning_cases() -> list[EvalCase]:
    """Planning-tier cases — classify + resolve only (Phase 12 expansion)."""
    return [c for c in ALL_EVAL_CASES if c.tier == EvalTier.PLANNING]
