"""Unit tests for agent/resolver.py against tests/resolver_examples.py (40 self-written
disfluent sentences — see that file's own docstring for the integrity rule this satisfies).
"""

import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from agent.resolver import resolve_turn
from resolver_examples import CASES


def _check_case(raw_text, schema, expected, note):
    resolved = resolve_turn(raw_text, schema)
    got = resolved.final_values()
    ok = True
    mismatches = []
    for k, v in expected.items():
        got_v = got.get(k)
        if got_v is None or str(got_v).strip().lower() != str(v).strip().lower():
            ok = False
            mismatches.append(f"{k}: expected={v!r} got={got_v!r}")
    return ok, mismatches, got


def test_all_examples_report():
    """Not a hard pytest assertion on every case (some are genuinely hard for a pure rule-based
    pass, by design — see the module's own limitations note) — reports overall accuracy, which
    is what decides (per the task) whether the resolver is worth keeping."""
    total = len(CASES)
    passed = 0
    failures = []
    for raw_text, schema, expected, note in CASES:
        ok, mismatches, got = _check_case(raw_text, schema, expected, note)
        if ok:
            passed += 1
        else:
            failures.append((raw_text, note, mismatches, got))

    print(f"\nresolver accuracy: {passed}/{total} = {passed/total:.1%}")
    for raw_text, note, mismatches, got in failures:
        print(f"  FAIL [{note}] {raw_text!r}")
        print(f"       {mismatches}  (full got={got})")

    assert passed / total >= 0.80, f"resolver accuracy {passed}/{total} below the 80% keep-bar"
