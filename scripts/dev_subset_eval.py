#!/usr/bin/env python3
"""Computes an ablation-table row for the dev subset (results/dev_subset.txt) by filtering the
ALREADY-COMPUTED official evaluator output (results/{provider}_toolcalls_exact.json /
{provider}_pass_rate_exact.json) down to dev-subset scenario ids and re-averaging -- never
re-runs inference, never touches evaluate_tool_calls.py/evaluate_pass_rate.py's own scoring
logic (Rule 6). Same technique as scripts/build_dashboard_data.py's common-51 filtering.

Usage: python scripts/dev_subset_eval.py --provider ours_full --label "baseline (current defaults)"
Appends a row to results/ABLATIONS.md (creates it with a header if missing).
"""
import argparse
import statistics
from pathlib import Path
import json

FDB_ROOT = Path(__file__).resolve().parent.parent.parent / "Full-Duplex-Bench" / "v3"
FDB_RESULTS_DIR = FDB_ROOT / "results"   # official evaluator output lives here (external)
OWN_RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"   # our own deliverables
ABLATIONS_PATH = OWN_RESULTS_DIR / "ABLATIONS.md"

HEADER = ("| Config | Tool-sel | Arg acc (exact) | Pass@1 | Latency mean/median (s) | "
         "Interruption | Turn-take | N (recordings) |\n"
         "|---|---|---|---|---|---|---|---|\n")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True)
    ap.add_argument("--label", required=True)
    args = ap.parse_args()

    dev_ids = set((OWN_RESULTS_DIR / "dev_subset.txt").read_text(encoding="utf-8").split())

    tc = _load(FDB_RESULTS_DIR / f"{args.provider}_toolcalls_exact.json")
    pr = _load(FDB_RESULTS_DIR / f"{args.provider}_pass_rate_exact.json")

    tc_rows = [r for r in tc["scenario_results"] if r["scenario_id"] in dev_ids]
    pr_rows = [r for r in pr["scenario_results"] if r["scenario_id"] in dev_ids]

    def avg(vals):
        vals = [v for v in vals if v is not None]
        return round(statistics.fmean(vals), 4) if vals else None

    tool_sel = avg([r["metrics"]["tool_selection_acc"]["score"] for r in tc_rows])
    arg_acc = avg([r["metrics"]["argument_acc"]["score"] for r in tc_rows])
    turn_take = avg([1.0 if r["turn_take_success"] else 0.0 for r in tc_rows])
    pass_at_1 = avg([1.0 if r["passed"] else 0.0 for r in pr_rows]) if pr_rows else None

    lat_vals = [r["latency"]["agent_response_latency_s"] for r in tc_rows
               if r.get("latency", {}).get("available") and not r["latency"].get("is_interruption")]
    lat_mean = round(statistics.fmean(lat_vals), 2) if lat_vals else None
    lat_median = round(statistics.median(lat_vals), 2) if lat_vals else None
    n_with_latency = sum(1 for r in tc_rows if r.get("latency", {}).get("available"))
    n_interrupt = sum(1 for r in tc_rows if r.get("latency", {}).get("is_interruption"))
    interrupt_rate = round(n_interrupt / n_with_latency, 4) if n_with_latency else None

    def pct(x):
        return "n/a" if x is None else f"{x * 100:.1f}%"

    n_unique = len({r["scenario_id"] for r in tc_rows})
    row = (f"| {args.label} | {pct(tool_sel)} | {pct(arg_acc)} | {pct(pass_at_1)} | "
          f"{lat_mean if lat_mean is not None else 'n/a'}/"
          f"{lat_median if lat_median is not None else 'n/a'} | "
          f"{pct(interrupt_rate)} | {pct(turn_take)} | "
          f"{len(tc_rows)} ({n_unique} unique ids, {len(dev_ids) - n_unique} of the 25 have no "
          f"recording) |\n")

    if not ABLATIONS_PATH.exists():
        ABLATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        ABLATIONS_PATH.write_text(
            "# Ablation table (dev subset, 25 examples, exact-match scoring)\n\n"
            "Every row filters the ALREADY-computed official evaluator output down to "
            "`results/dev_subset.txt`'s 25 scenario ids and re-averages -- never re-derives "
            "scoring logic. See `scripts/dev_subset_eval.py`.\n\n" + HEADER, encoding="utf-8")

    with open(ABLATIONS_PATH, "a", encoding="utf-8") as f:
        f.write(row)
    print("Appended row:")
    print(row.strip())


if __name__ == "__main__":
    main()
