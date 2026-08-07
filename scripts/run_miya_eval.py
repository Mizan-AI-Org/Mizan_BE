#!/usr/bin/env python
"""Run the Miya evaluation suite and print a release scorecard."""
from __future__ import annotations

import json
import os
import sys

# Django setup
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mizan.settings")

import django

django.setup()

from miya.services.intelligence.eval import (  # noqa: E402
    RELEASE_TARGETS,
    build_scorecard,
    run_eval_suite,
    simulation_cases,
)
from miya.services.intelligence.eval.cases.production_bugs import (  # noqa: E402
    PRODUCTION_BUG_CASES,
)


def main() -> int:
    sim_results = run_eval_suite(simulation_cases())
    scorecard = build_scorecard(sim_results)

    bug_results = run_eval_suite(PRODUCTION_BUG_CASES)
    bugs_ok = all(r.passed for r in bug_results)

    print("=" * 60)
    print("MIYA EVALUATION SCORECARD — Phase 9")
    print("=" * 60)
    print(json.dumps(scorecard.to_dict(), indent=2))
    print("\nTargets:")
    for k, v in RELEASE_TARGETS.items():
        actual = scorecard.by_metric.get(k, scorecard.to_dict().get("pass_rate"))
        print(f"  {k}: target={v:.2f}  actual={actual}")

    print(f"\nProduction bug anti-patterns: {'PASS' if bugs_ok else 'FAIL'}")
    print(f"Release gate: {'PASS' if scorecard.gate_passed and bugs_ok else 'FAIL'}")

    if not scorecard.gate_passed or not bugs_ok:
        failing = [r.case_id for r in sim_results if not r.passed]
        if failing:
            print("\nFailing cases:", ", ".join(failing[:20]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
