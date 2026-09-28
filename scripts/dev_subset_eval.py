#!/usr/bin/env python3
"""Evaluates an already-inferred-and-scored provider against the isolated dev subset
(Full-Duplex-Bench/v3/dev_subset_data/, the 25 recordings named in results/dev_subset.txt) using
the UNMODIFIED official evaluate_tool_calls.py / evaluate_pass_rate.py (Rule 6 -- never touches
scoring logic), then appends a row to results/ABLATIONS.md from their own aggregate output.

Because dev_subset_data/ contains exactly the 25 dev-subset recordings and nothing else, the
official reports' own `by_metric`/`turn_taking`/`latency`/`overall_pass_rate` aggregates ARE the
dev-subset numbers directly -- no post-hoc filtering by scenario_id needed (the original version
of this script filtered scenario_id out of the full-100 ours_full report, which broke once the
dev subset became folder-based: 21 of the 100 scenario ids have two recordings from different
speakers, so scenario_id alone can't identify a single recording).

Called standalone (to re-generate a row for an already-scored provider without re-running
inference) or imported by scripts/run_dev_sweep_run.py, which drives the full agent+inference
pipeline first and then calls append_ablation_row().

Usage: python scripts/dev_subset_eval.py --provider dev_r1 --label "baseline-A"
"""
import argparse
import json
import statistics
from pathlib import Path

FDB_ROOT = Path(__file__).resolve().parent.parent.parent / "Full-Duplex-Bench" / "v3"
DEV_DATA_DIR = FDB_ROOT / "dev_subset_data"
FDB_RESULTS_DIR = FDB_ROOT / "results"
OWN_RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"
ABLATIONS_PATH = OWN_RESULTS_DIR / "ABLATIONS.md"

HEADER = ("| Config | Tool-sel | Arg acc (exact) | Pass@1 | Latency mean/median (s) | "
         "Interruption | Turn-take | N |\n"
         "|---|---|---|---|---|---|---|---|\n")


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def evaluate_provider(provider: str) -> tuple:
    """Runs the official evaluators (unmodified) against dev_subset_data/ for `provider`.
    Returns (toolcalls_report, pass_rate_report)."""
    import os
    import subprocess
    venv_py = Path(__file__).resolve().parent.parent.parent / "fdb-venv" / "Scripts" / "python.exe"
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    tc_out = FDB_RESULTS_DIR / f"{provider}_toolcalls_exact.json"
    pr_out = FDB_RESULTS_DIR / f"{provider}_pass_rate_exact.json"
    # Deliberately NOT capturing output: capturing+decoding it in THIS process is what crashed
    # R1's smoke test (Windows console cp1252 decode on the script's own emoji prints, in a
    # background reader thread) -- letting stdout/stderr flow through uncaptured avoids the
    # decode step here entirely, same pattern overnight_runner.py already used reliably all
    # session (redirect to a file, never decode in-process).
    subprocess.run([str(venv_py), "evaluate_tool_calls.py", "--benchmark", "benchmark_data_v2.json",
                   "--results-dir", str(DEV_DATA_DIR), "--provider", provider,
                   "--output", str(tc_out)], cwd=str(FDB_ROOT), env=env, check=False)
    subprocess.run([str(venv_py), "evaluate_pass_rate.py", "--benchmark", "benchmark_data_v2.json",
                   "--results-dir", str(DEV_DATA_DIR), "--provider", provider,
                   "--output", str(pr_out)], cwd=str(FDB_ROOT), env=env, check=False)
    return _load(tc_out), _load(pr_out)


def append_ablation_row(label: str, tc: dict, pr: dict) -> str:
    def pct(x):
        return "n/a" if x is None else f"{x * 100:.1f}%"

    bm = tc["by_metric"]
    lat = tc["latency"]
    # evaluate_tool_calls.py's own latency_report has mean/std but no median -- compute median
    # from the same per-scenario values it used (available, non-interruption).
    per_scenario_lat = [r["latency"]["agent_response_latency_s"] for r in tc["scenario_results"]
                        if r.get("latency", {}).get("available")
                        and not r["latency"].get("is_interruption")]
    median_lat = round(statistics.median(per_scenario_lat), 2) if per_scenario_lat else None

    row = (f"| {label} | {pct(bm.get('tool_selection_acc'))} | "
          f"{pct(bm.get('argument_acc'))} | {pct(pr.get('overall_pass_rate'))} | "
          f"{lat.get('avg_response_latency_s', 'n/a')}/{median_lat if median_lat is not None else 'n/a'} | "
          f"{pct(lat.get('interruption_rate'))} | {pct(tc['turn_taking'].get('turn_take_rate'))} | "
          f"{tc['total_scenarios']} |\n")

    if not ABLATIONS_PATH.exists():
        ABLATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
        ABLATIONS_PATH.write_text(
            "# Ablation table (dev subset, 25 recordings, official exact-match scorer)\n\n"
            "Each row runs the full pipeline (agent start -> smoke test -> 25-recording "
            "inference -> score -> official evaluate_tool_calls.py/evaluate_pass_rate.py, "
            "unmodified) against the isolated `dev_subset_data/` copy of exactly the 25 "
            "recordings in `results/dev_subset.txt`. See `scripts/run_dev_sweep_run.py` and "
            "`scripts/dev_subset_eval.py`.\n\n" + HEADER, encoding="utf-8")
    with open(ABLATIONS_PATH, "a", encoding="utf-8") as f:
        f.write(row)
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", required=True)
    ap.add_argument("--label", required=True)
    args = ap.parse_args()

    tc, pr = evaluate_provider(args.provider)
    row = append_ablation_row(args.label, tc, pr)
    print("Appended row:")
    print(row.strip())


if __name__ == "__main__":
    main()
