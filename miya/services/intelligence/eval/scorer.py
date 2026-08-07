"""Score observed Miya behavior against eval expectations."""
from __future__ import annotations

import re
from typing import Any

from miya.services.intelligence.actions import resolve_action_name
from miya.services.intelligence.eval.types import (
    ALL_METRICS,
    CaseResult,
    EvalCase,
    EvalExpectation,
    EvalTier,
    EvalTrace,
    MetricName,
    MetricScore,
)

_SEARCH_ONLY_TOOLS = frozenset(
    {
        "operational_search",
        "ops_search",
        "semantic_search",
        "find_tasks",
        "find_incidents",
        "find_invoices",
        "find_documents",
        "find_staff",
        "find_establishments",
        "get_current_task",
        "get_current_incident",
        "get_current_staff",
        "get_current_document",
        "get_current_invoice",
        "retrieve_document",
        "get_event_history",
        "recall_operational_memory",
    }
)
_MUTATION_TOOLS = frozenset(
    name
    for name, meta in __import__(
        "miya.services.intelligence.actions", fromlist=["ACTION_CATALOG"]
    ).ACTION_CATALOG.items()
    if meta.get("mutates")
)
_SUCCESS_CLAIM = re.compile(
    r"\b(done|completed|closed|finished|approved|created|assigned|marked|updated|success)\b",
    re.I,
)


def _normalize_intent(value: str | None) -> str:
    v = (value or "").strip().upper()
    return v.replace("_TASK", "").replace("-", "_")


def _tool_names(trace: EvalTrace) -> list[str]:
    names: list[str] = []
    for call in trace.tools_called:
        raw = call.get("name") or call.get("tool") or ""
        names.append(resolve_action_name(str(raw)))
    return names


def _contains_args(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    for key, val in expected.items():
        if key not in actual:
            return False
        if str(actual[key]).lower() != str(val).lower():
            return False
    return True


def _score_metric(
    metric: MetricName,
    case: EvalCase,
    exp: EvalExpectation,
    trace: EvalTrace,
) -> MetricScore:
    tools = _tool_names(trace)

    if metric == MetricName.INTENT:
        if not exp.intent:
            return MetricScore(metric, None, 1.0, "N/A", applicable=False)
        got = _normalize_intent(trace.intent)
        want = _normalize_intent(exp.intent)
        passed = got == want
        return MetricScore(
            metric,
            passed,
            1.0 if passed else 0.0,
            f"expected {want}, got {got or '∅'}",
        )

    if metric == MetricName.ENTITY:
        applicable = bool(exp.entity_type or exp.entity_query or exp.entity_id)
        if not applicable:
            return MetricScore(metric, None, 1.0, "N/A", applicable=False)
        ok_entity = True
        details: list[str] = []
        if exp.entity_type and (trace.entity_type or "") != exp.entity_type:
            ok_entity = False
            details.append(f"type expected {exp.entity_type}, got {trace.entity_type}")
        if exp.entity_query:
            q = (trace.entity_query or "").lower()
            if exp.entity_query.lower() not in q and q not in exp.entity_query.lower():
                ok_entity = False
                details.append(f"query expected {exp.entity_query!r}, got {trace.entity_query!r}")
        if exp.entity_id and trace.entity_id != exp.entity_id:
            ok_entity = False
            details.append(f"id expected {exp.entity_id}, got {trace.entity_id}")
        return MetricScore(metric, ok_entity, 1.0 if ok_entity else 0.0, "; ".join(details) or "ok")

    if metric == MetricName.CONTEXT:
        if not exp.context:
            return MetricScore(metric, None, 1.0, "N/A", applicable=False)
        passed = all(trace.context.get(k) == v for k, v in exp.context.items())
        return MetricScore(
            metric,
            passed,
            1.0 if passed else 0.0,
            f"expected {exp.context}, got {trace.context}",
        )

    if metric == MetricName.TOOL_SELECTION:
        if exp.clarify:
            passed = len(tools) == 0 or trace.clarified
            return MetricScore(
                metric,
                passed,
                1.0 if passed else 0.0,
                "clarify expected — no mutation tool" if passed else f"tools={tools}",
            )
        if not exp.tool:
            return MetricScore(metric, None, 1.0, "N/A", applicable=False)
        want = resolve_action_name(exp.tool)
        passed = want in tools
        return MetricScore(
            metric,
            passed,
            1.0 if passed else 0.0,
            f"expected {want}, got {tools or '∅'}",
        )

    if metric == MetricName.TOOL_ARGS:
        if not exp.tool_args_contains or not exp.tool:
            return MetricScore(metric, None, 1.0, "N/A", applicable=False)
        want = resolve_action_name(exp.tool)
        matched = False
        for call in trace.tools_called:
            name = resolve_action_name(str(call.get("name") or call.get("tool") or ""))
            if name != want:
                continue
            args = call.get("args") or call.get("arguments") or {}
            if _contains_args(args, exp.tool_args_contains):
                matched = True
                break
        return MetricScore(
            metric,
            matched,
            1.0 if matched else 0.0,
            f"args must contain {exp.tool_args_contains}",
        )

    if metric == MetricName.PERMISSION:
        if exp.permission_allowed is None:
            return MetricScore(metric, None, 1.0, "N/A", applicable=False)
        if exp.permission_allowed:
            passed = not trace.permission_denied
        else:
            passed = trace.permission_denied or any(
                (call.get("result") or {}).get("code") == "permission_denied"
                for call in trace.tools_called
            )
        return MetricScore(metric, passed, 1.0 if passed else 0.0)

    if metric == MetricName.EXECUTION:
        if exp.clarify:
            passed = trace.clarified and not any(t in _MUTATION_TOOLS for t in tools)
            return MetricScore(metric, passed, 1.0 if passed else 0.0)
        if not exp.tool:
            return MetricScore(metric, None, 1.0, "N/A", applicable=False)
        want = resolve_action_name(exp.tool)
        passed = want in tools and not trace.error
        return MetricScore(metric, passed, 1.0 if passed else 0.0, trace.error or "ok")

    if metric == MetricName.DATABASE:
        if not exp.db_state:
            return MetricScore(metric, None, 1.0, "N/A", applicable=False)
        passed = all(trace.db_after.get(k) == v for k, v in exp.db_state.items())
        return MetricScore(
            metric,
            passed,
            1.0 if passed else 0.0,
            f"expected {exp.db_state}, after {trace.db_after}",
        )

    if metric == MetricName.VERIFICATION:
        if exp.verified is None:
            return MetricScore(metric, None, 1.0, "N/A", applicable=False)
        passed = trace.verified == exp.verified
        return MetricScore(metric, passed, 1.0 if passed else 0.0)

    if metric == MetricName.RESPONSE:
        text = (trace.response or "").lower()
        passed = True
        for needle in exp.response_must_contain:
            if needle.lower() not in text:
                passed = False
        for forbidden in exp.response_must_not_contain:
            if forbidden.lower() in text:
                passed = False
        return MetricScore(metric, passed, 1.0 if passed else 0.0)

    if metric == MetricName.DUPLICATE:
        if not tools:
            return MetricScore(metric, None, 1.0, "N/A", applicable=False)
        dup = len(tools) > exp.max_tool_calls
        passed = not dup
        return MetricScore(
            metric,
            passed,
            1.0 if passed else 0.0,
            f"{len(tools)} calls (max {exp.max_tool_calls})",
        )

    if metric == MetricName.HALLUCINATION:
        if not exp.forbid_hallucination:
            return MetricScore(metric, None, 1.0, "N/A", applicable=False)
        flags = list(trace.hallucination_flags)
        if exp.require_mutation_tool and not any(t in _MUTATION_TOOLS for t in tools):
            flags.append("search_only_on_mutation")
        if trace.claimed_success and not trace.verified and exp.verified is not False:
            flags.append("success_without_verification")
        if trace.search_only and exp.require_mutation_tool:
            flags.append("search_only")
        passed = len(flags) == 0
        return MetricScore(
            metric,
            passed,
            1.0 if passed else 0.0,
            ", ".join(flags) or "clean",
        )

    if metric == MetricName.LATENCY:
        if exp.max_latency_ms is None:
            return MetricScore(metric, None, 1.0, "N/A", applicable=False)
        passed = trace.latency_ms <= exp.max_latency_ms
        return MetricScore(
            metric,
            passed,
            1.0 if passed else 0.0,
            f"{trace.latency_ms:.1f}ms",
        )

    if metric == MetricName.TOKEN_COST:
        if exp.max_tokens is None and exp.max_cost_usd is None:
            return MetricScore(metric, None, 1.0, "N/A", applicable=False)
        passed = True
        if exp.max_tokens is not None:
            passed = passed and (trace.tokens_in + trace.tokens_out) <= exp.max_tokens
        if exp.max_cost_usd is not None:
            passed = passed and trace.cost_usd <= exp.max_cost_usd
        return MetricScore(metric, passed, 1.0 if passed else 0.0)

    return MetricScore(metric, None, 1.0, "unknown", applicable=False)


def detect_hallucination_flags(trace: EvalTrace, exp: EvalExpectation) -> list[str]:
    """Populate hallucination flags on a trace before scoring."""
    flags: list[str] = []
    tools = _tool_names(trace)
    response = trace.response or ""

    if exp.require_mutation_tool and not exp.clarify:
        if not tools:
            flags.append("no_tool_called")
        elif all(t in _SEARCH_ONLY_TOOLS for t in tools):
            flags.append("search_only_on_mutation")

    if trace.claimed_success and not trace.verified and exp.verified is True:
        flags.append("success_without_verification")

    if exp.db_state and not trace.db_after and not exp.clarify:
        flags.append("database_not_changed")

    if (
        exp.tool
        and resolve_action_name(exp.tool) not in tools
        and not exp.clarify
        and exp.verified is not False
    ):
        if _SUCCESS_CLAIM.search(response):
            flags.append("false_success_claim")

    if len(tools) > exp.max_tool_calls:
        flags.append("duplicate_execution")

    return flags


def score_case(case: EvalCase, trace: EvalTrace) -> CaseResult:
    """Score all metrics for one case."""
    exp = case.expected
    trace.hallucination_flags = detect_hallucination_flags(trace, exp)

    metrics = [_score_metric(m, case, exp, trace) for m in ALL_METRICS]

    # Planning-tier cases: only require intent/entity/context/clarify metrics.
    if case.tier == EvalTier.PLANNING:
        planning_metrics = {
            MetricName.INTENT,
            MetricName.ENTITY,
            MetricName.CONTEXT,
            MetricName.TOOL_SELECTION,
            MetricName.PERMISSION,
            MetricName.RESPONSE,
            MetricName.LATENCY,
        }
        for m in metrics:
            if m.name not in planning_metrics:
                m.applicable = False
                m.passed = None
                m.score = 1.0

    applicable = [m for m in metrics if m.applicable and m.passed is not None]
    scores = [m.score for m in applicable]
    overall = sum(scores) / len(scores) if scores else 1.0

    failures = [
        f"{m.name.value}: {m.detail}"
        for m in metrics
        if m.applicable and m.passed is False
    ]

    passed = len(failures) == 0
    if case.expect_overall_fail:
        passed = not passed

    return CaseResult(
        case_id=case.id,
        category=case.category.value,
        critical=case.critical,
        metrics=metrics,
        overall=overall,
        passed=passed,
        failures=failures,
        trace=trace,
        production_bug=case.production_bug,
    )
