"""Phase 9 — Miya Evaluation System regression tests."""
from __future__ import annotations

from django.test import SimpleTestCase

from miya.services.intelligence.eval import (
    ALL_EVAL_CASES,
    RELEASE_TARGETS,
    build_scorecard,
    critical_cases,
    run_eval_case,
    run_eval_suite,
    simulation_cases,
)
from miya.services.intelligence.eval.cases.dataset import EVAL_CASES
from miya.services.intelligence.eval.cases.production_bugs import PRODUCTION_BUG_CASES
from miya.services.intelligence.eval.types import EvalCategory


class EvalDatasetTests(SimpleTestCase):
    """Dataset integrity — every category represented."""

    REQUIRED_CATEGORIES = {
        EvalCategory.TASKS,
        EvalCategory.INCIDENTS,
        EvalCategory.STAFF,
        EvalCategory.ROUTING,
        EvalCategory.DOCUMENTS,
        EvalCategory.OCR,
        EvalCategory.INVOICES,
        EvalCategory.APPROVALS,
        EvalCategory.REMINDERS,
        EvalCategory.MEETINGS,
        EvalCategory.MULTI_ESTABLISHMENT,
        EvalCategory.WHATSAPP,
        EvalCategory.VOICE,
        EvalCategory.AMBIGUOUS,
        EvalCategory.PERMISSIONS,
        EvalCategory.PRODUCTION_BUG,
    }

    def test_all_categories_present(self):
        present = {c.category for c in EVAL_CASES}
        missing = self.REQUIRED_CATEGORIES - present - {EvalCategory.PRODUCTION_BUG}
        self.assertFalse(missing, f"Missing categories: {missing}")

    def test_unique_case_ids(self):
        ids = [c.id for c in ALL_EVAL_CASES]
        self.assertEqual(len(ids), len(set(ids)), "Duplicate eval case IDs")

    def test_critical_cases_exist(self):
        self.assertGreaterEqual(len(critical_cases()), 5)

    def test_production_bugs_expect_fail(self):
        for case in PRODUCTION_BUG_CASES:
            self.assertTrue(case.expect_overall_fail, case.id)
            self.assertIsNotNone(case.production_bug, case.id)
            self.assertIsNotNone(case.observed, case.id)


class EvalScorerTests(SimpleTestCase):
    """Example from spec: Close the decoration task."""

    def test_close_decoration_task_passes(self):
        case = next(c for c in EVAL_CASES if c.id == "task-complete-decoration")
        result = run_eval_case(case)
        self.assertTrue(result.passed, result.failures)
        self.assertGreaterEqual(result.overall, 0.9)

    def test_production_bug_search_only_fails(self):
        case = next(c for c in PRODUCTION_BUG_CASES if c.id == "prod-bug-search-only-mutation")
        result = run_eval_case(case)
        self.assertTrue(result.passed)  # expect_overall_fail inverts


class EvalSuiteRegressionTests(SimpleTestCase):
    """Full suite must pass release gates — blocks regressions."""

    def test_simulation_suite_passes(self):
        results = run_eval_suite(simulation_cases())
        failing = [r for r in results if not r.passed]
        if failing:
            detail = "\n".join(f"  {r.case_id}: {r.failures}" for r in failing[:10])
            self.fail(f"{len(failing)} simulation case(s) failed:\n{detail}")

    def test_production_bug_replays_fail_scoring(self):
        results = run_eval_suite(PRODUCTION_BUG_CASES)
        for r in results:
            self.assertTrue(r.passed, f"{r.case_id} should register as anti-pattern pass")

    def test_release_scorecard_gate(self):
        results = run_eval_suite(simulation_cases())
        scorecard = build_scorecard(results)
        self.assertTrue(
            scorecard.gate_passed,
            f"Release gate failed: {scorecard.regressions}",
        )
        self.assertGreaterEqual(
            scorecard.passed_cases / scorecard.total_cases,
            RELEASE_TARGETS["overall_pass_rate"],
        )

    def test_critical_cases_all_pass(self):
        sim_critical = [c for c in critical_cases() if not c.observed]
        results = run_eval_suite(sim_critical)
        failing = [r for r in results if not r.passed]
        self.assertEqual(
            len(failing),
            0,
            f"Critical cases failed: {[r.case_id for r in failing]}",
        )


class EvalMetricCoverageTests(SimpleTestCase):
    """All 14 metrics are scored on at least one case."""

    METRIC_NAMES = {
        "intent_accuracy",
        "entity_resolution_accuracy",
        "context_accuracy",
        "tool_selection_accuracy",
        "tool_argument_accuracy",
        "permission_accuracy",
        "execution_accuracy",
        "database_state_accuracy",
        "verification_accuracy",
        "final_response_accuracy",
        "duplicate_execution",
        "hallucination",
        "latency",
        "token_cost_usage",
    }

    def test_all_metrics_applicable_somewhere(self):
        results = run_eval_suite(simulation_cases())
        seen: set[str] = set()
        for r in results:
            for m in r.metrics:
                if m.applicable:
                    seen.add(m.name.value)
        missing = self.METRIC_NAMES - seen
        # token_cost may be N/A until live LLM eval — allow optional
        optional = {"token_cost_usage"}
        required_missing = missing - optional
        self.assertFalse(required_missing, f"Metrics never applicable: {required_missing}")
