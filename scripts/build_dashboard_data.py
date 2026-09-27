#!/usr/bin/env python3
"""Precomputes docs/dashboard_data.json for the SentinelEdge dashboard's Results and Benchmark
Explorer tabs, so those tabs work from a static file without re-running anything.

Data sources (all read-only, none touched by the dashboard or its server):
  - Full-Duplex-Bench/v3/benchmark_data_v2.json  -- scenario metadata (domain, difficulty,
    disfluency_features, expected_tool_calls), keyed by scenario id.
  - Full-Duplex-Bench/v3/results/{ours,baseline}_full_toolcalls_exact.json
  - Full-Duplex-Bench/v3/results/{ours,baseline}_full_pass_rate_exact.json
    -- exact-match evaluator reports (generate_overnight_report.py's own output), regenerated
       after the baseline recovery (51/100 completed, 49 never attempted -- see PROGRESS.md).
    Used ONLY for the full-100 "ours" headline/breakdowns (official aggregation, unambiguous).
  - Full-Duplex-Bench/v3/fdb_v3_data_released/{example_id}_{pid}/result_{ours,baseline}_full.json
    -- per-example raw transcripts, tool calls, timestamps, latency, status.

IMPORTANT DATA WRINKLE, found while building this: 21 of the 100 scenario ids were recorded by
TWO different speakers (100 folders, 79 unique example ids). The official reports' own
`scenario_results` entries carry no field to tell the two recordings of the same id apart, so
they cannot be safely used for per-recording (folder-level) lookups -- only for the full-100
AGGREGATE numbers, where this ambiguity doesn't matter (the aggregation already happened inside
the official scorer). Everything that needs a specific recording (common-51 identification,
per-example Explorer rows, common-51 breakdowns) is instead computed directly per FOLDER from
the raw result files, using a documented, simplified tool-selection score (recall x precision,
multiset function-name match -- RESEARCH_FDB.md's own description of the metric) rather than
re-deriving the official scorer's more lenient argument-matching exactly. This is clearly
labelled below and in the UI; it is a real, from-data computation, not a fabricated number, but
it is NOT the same number the official 100-scenario aggregate reports (which are used as-is for
the "ours full-100" headline).

Usage: python scripts/build_dashboard_data.py
Writes: docs/dashboard_data.json
"""
import json
import re
import statistics
from pathlib import Path

FDB_ROOT = Path(__file__).resolve().parent.parent.parent / "Full-Duplex-Bench" / "v3"
DATA_DIR = FDB_ROOT / "fdb_v3_data_released"
RESULTS_DIR = FDB_ROOT / "results"
BENCHMARK_JSON = FDB_ROOT / "benchmark_data_v2.json"
OUT_PATH = Path(__file__).resolve().parent.parent / "docs" / "dashboard_data.json"

COMMIT_GATE_BUFFER_MS = 400  # LK_COMMIT_BUFFER_MS, fixed per-run config for "ours" (see
# overnight_runner.py's AgentHandle.start()) -- not a per-call measured value.

_FOLDER_RE = re.compile(r"^(.+)_([0-9a-f]{24})$")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _scenario_meta() -> dict:
    data = _load(BENCHMARK_JSON)
    scenarios = data.get("scenarios", data) if isinstance(data, dict) else data
    return {s["id"]: s for s in scenarios}


def _all_folders() -> list:
    """[(folder_name, example_id, pid)] for all 100 recordings."""
    out = []
    for d in sorted(DATA_DIR.iterdir()):
        if not d.is_dir():
            continue
        m = _FOLDER_RE.match(d.name)
        if m:
            out.append((d.name, m.group(1), m.group(2)))
    return out


def _raw_result(folder_name: str, provider: str) -> dict | None:
    rf = DATA_DIR / folder_name / f"result_{provider}.json"
    if not rf.exists():
        return None
    try:
        return json.loads(rf.read_text(encoding="utf-8"))
    except Exception:
        return None


def _tool_names(calls: list) -> list:
    return [c.get("function") for c in calls]


def _simple_tool_selection(expected: list, actual: list) -> dict:
    """Multiset function-name match, per RESEARCH_FDB.md's own description of the official
    metric: recall = all expected names were called; precision = no unmatched extra calls.
    Score = recall * precision (a simplified stand-in for the official scorer's continuous
    score, used only for common-51/per-example display -- see module docstring)."""
    exp_names = _tool_names(expected)
    act_names = _tool_names(actual)
    exp_remaining = list(exp_names)
    matched = 0
    for name in act_names:
        if name in exp_remaining:
            exp_remaining.remove(name)
            matched += 1
    recall = matched / len(exp_names) if exp_names else (1.0 if not act_names else 0.0)
    precision = matched / len(act_names) if act_names else (1.0 if not exp_names else 0.0)
    return {"recall": round(recall, 4), "precision": round(precision, 4),
            "score": round(recall * precision, 4)}


def _avg(values: list) -> float | None:
    values = [v for v in values if v is not None]
    return round(statistics.fmean(values), 4) if values else None


def main():
    meta = _scenario_meta()
    folders = _all_folders()  # 100 (example_id, pid) recordings

    ours_tc = _load(RESULTS_DIR / "ours_full_toolcalls_exact.json")
    ours_pr = _load(RESULTS_DIR / "ours_full_pass_rate_exact.json")

    ours_full = {
        "n": len(folders),
        "headline": {
            "tool_selection_acc": ours_tc["by_metric"]["tool_selection_acc"],
            "argument_acc": ours_tc["by_metric"]["argument_acc"],
            "pass_at_1": ours_pr["overall_pass_rate"],
            "turn_take_rate": ours_tc["turn_taking"]["turn_take_rate"],
            "interruption_rate": ours_tc["latency"].get("interruption_rate"),
            "avg_latency_s": ours_tc["latency"].get("avg_response_latency_s"),
        },
        "by_domain": {d: {"tool_selection_acc": v.get("tool_selection_acc"),
                          "argument_acc": v.get("argument_acc")}
                     for d, v in ours_tc["by_domain"].items()},
        "by_difficulty": {d: {"tool_selection_acc": v.get("tool_selection_acc"),
                              "argument_acc": v.get("argument_acc")}
                         for d, v in ours_tc["by_difficulty"].items()},
    }

    # Per-folder, from-scratch computation (the reliable path -- see module docstring) used for
    # common-51 identification, common-51 aggregates, and Explorer rows.
    rows = []
    for folder_name, example_id, pid in folders:
        m = meta.get(example_id)
        if m is None:
            continue
        ours_raw = _raw_result(folder_name, "ours_full") or {}
        base_raw = _raw_result(folder_name, "baseline_full")
        expected = m.get("expected_tool_calls", [])

        ours_actual = ours_raw.get("actual_tool_calls", [])
        ours_score = _simple_tool_selection(expected, ours_actual)

        base_completed = base_raw is not None and base_raw.get("status") == "completed"
        base_actual = base_raw.get("actual_tool_calls", []) if base_completed else []
        base_score = _simple_tool_selection(expected, base_actual) if base_completed else None

        rows.append({
            "folder": folder_name, "id": example_id, "pid": pid,
            "domain": m["domain"], "difficulty": m["difficulty"],
            "disfluency": m.get("disfluency_features", []) or ["none"],
            "title": m.get("title", ""),
            "expected_tool_calls": expected,
            "ours": {
                "actual_tool_calls": ours_actual,
                "transcript": ours_raw.get("transcript", ""),
                "input_transcript": ours_raw.get("input_transcript", ""),
                "user_speech_end_rel": ours_raw.get("user_speech_end_rel"),
                "tool_selection": ours_score,
            },
            "baseline_completed": base_completed,
            "baseline": None if not base_completed else {
                "actual_tool_calls": base_actual,
                "transcript": base_raw.get("transcript", ""),
                "tool_selection": base_score,
            },
        })

    common_rows = [r for r in rows if r["baseline_completed"]]
    domains_covered = sorted({r["domain"] for r in common_rows})
    domains_missing = sorted({r["domain"] for r in rows} - set(domains_covered))

    def group_by(rows_subset, key_fn, side):
        buckets = {}
        for r in rows_subset:
            labels = key_fn(r)
            if not isinstance(labels, list):
                labels = [labels]
            for label in labels:
                b = buckets.setdefault(label, {"scores": [], "n": 0})
                b["scores"].append(r[side]["tool_selection"]["score"])
                b["n"] += 1
        return {k: {"tool_selection_acc": _avg(v["scores"]), "n": v["n"]}
               for k, v in buckets.items()}

    common51 = {
        "n": len(common_rows),
        "note": "Tool-selection score here is a simplified recall x precision multiset match "
            "computed directly per recording (see build_dashboard_data.py docstring) -- not the "
            "same number as the official 100-scenario aggregate above, but computed identically "
            "for ours and baseline so the comparison itself is fair.",
        "ours": {
            "tool_selection_acc": _avg([r["ours"]["tool_selection"]["score"] for r in common_rows]),
            "by_domain": group_by(common_rows, lambda r: r["domain"], "ours"),
            "by_difficulty": group_by(common_rows, lambda r: r["difficulty"], "ours"),
            "by_disfluency": group_by(common_rows, lambda r: r["disfluency"], "ours"),
        },
        "baseline": {
            "tool_selection_acc": _avg([r["baseline"]["tool_selection"]["score"] for r in common_rows]),
            "by_domain": group_by(common_rows, lambda r: r["domain"], "baseline"),
            "by_difficulty": group_by(common_rows, lambda r: r["difficulty"], "baseline"),
            "by_disfluency": group_by(common_rows, lambda r: r["disfluency"], "baseline"),
        },
    }

    preemptive = sum(
        1 for r in rows for c in r["ours"]["actual_tool_calls"]
        if r["ours"]["user_speech_end_rel"] is not None
        and c.get("timestamp_start") is not None
        and c["timestamp_start"] < r["ours"]["user_speech_end_rel"]
    )
    total_calls = sum(len(r["ours"]["actual_tool_calls"]) for r in rows)

    commit_gate_benchmark = {
        "total_tool_calls_executed": total_calls,
        "preemptive_calls_detected": preemptive,
        "preemptive_calls_note": "A call whose timestamp_start precedes the user's own "
            "speech-end (user_speech_end_rel in the raw result) -- derived directly from "
            "recorded timestamps.",
        "buffer_ms_configured": COMMIT_GATE_BUFFER_MS,
        "note": "Per-call buffered/superseded/duplicate-blocked counts are not available for "
            "this already-scored run: commit_gate.py's on_event hook is additive and was kept "
            "extension-only under the code freeze, so it was never wired into the benchmark "
            "agent that produced these 100 results. Live counts for new sessions are shown on "
            "the Live tab instead.",
    }

    out = {
        "generated_at": ours_tc.get("evaluated_at"),
        "judge": ours_tc.get("judge"),
        "ours_full": ours_full,
        "common51": common51,
        "baseline_coverage": {
            "completed": len(common_rows), "total": len(rows),
            "domains_covered": domains_covered, "domains_missing": domains_missing,
        },
        "commit_gate_benchmark": commit_gate_benchmark,
        "limitations": [
            "The exact-match scorer used for the full-100 'ours' numbers above is stricter than "
            "FDB-v3's official gpt-4o-judged scorer -- not directly comparable to any published "
            "gpt-4o-judged results.",
            "Common-51 tool-selection numbers use a simplified recall x precision score computed "
            "per recording (see note in common51 above), not the official continuous scorer.",
            "Baseline coverage is 51/100 recordings (its original run stalled and was stopped "
            "short rather than restarted without asking); domains_missing above have zero "
            "baseline data.",
            "Cross-turn corrections that arrive after a long pause, in a separate turn from the "
            "one that made the call, remain a known failure mode -- see README Limitations.",
        ],
        "examples": rows,
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Wrote {OUT_PATH} ({len(rows)} recordings, common51.n={common51['n']})")


if __name__ == "__main__":
    main()
