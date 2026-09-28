#!/usr/bin/env python3
"""Step 0 measurement: per-example latency breakdown for the dev subset (results/dev_subset.txt),
computed entirely from our own recorded outputs -- timestamps, actual_tool_calls, and the
agent's own output transcript (asr_chunks). Never reads metadata.json, expected_tool_calls, or
input_transcript (integrity rule: measurement must not use ground truth).

dev_subset.txt holds 25 RECORDING FOLDER NAMES (every 4th of the 100 sorted folders under
fdb_v3_data_released), not scenario ids -- fixed after the first version sampled scenario ids,
5 of which had zero recordings at all (21 of the 100 defined scenarios were never released as
audio), and scenario ids are ambiguous anyway (21 of the 79 released scenario ids have TWO
recordings from different speakers). Folder names are always unambiguous and always exist.

Stages (all relative, seconds):
  1. user_speech_end -> first_tool_call   : actual_tool_calls[0].timestamp_start - user_speech_end_rel
  2. tool_call -> tool_result             : sum(timestamp_end - timestamp_start) over all calls
                                             (wall time actually spent inside tool execution)
  3. last_tool_result -> first_agent_audio: audio_agent_speech_start - max(call.timestamp_end)
  4. first_agent_audio -> key_info        : timestamp of the first asr_chunk word that matches a
                                             token from one of OUR OWN actual_tool_calls' args
                                             (not expected_tool_calls) - a proxy for "how long
                                             until the agent actually says the answer", not an
                                             exact NLU parse.
  5. commit_gate_buffer                   : NOT measurable from these result files -- the
                                             benchmark agent's CommitGate.on_event hook was never
                                             wired (kept extension-only under the prior freeze).
                                             Reported as the fixed configured buffer_ms instead
                                             of a per-call measurement; see report footer.

A scenario with no tool calls skips stages 1-4 (nothing to measure) but is still counted for
turn-take/response presence. A scenario with tool calls but no agent response (the ecommerce_01-
style flake) skips stages 3-4.

Usage: python scripts/latency_breakdown.py [--provider ours_full] [--all-100]
Writes: results/LATENCY_BREAKDOWN.md
"""
import argparse
import json
import re
import statistics
from pathlib import Path

FDB_ROOT = Path(__file__).resolve().parent.parent.parent / "Full-Duplex-Bench" / "v3"
FULL_DATA_DIR = FDB_ROOT / "fdb_v3_data_released"     # all 100 recordings (external)
DEV_DATA_DIR = FDB_ROOT / "dev_subset_data"           # isolated copy of just the 25 dev
                                                       # recordings, used by the sweep runs
FDB_RESULTS_DIR = FDB_ROOT / "results"                # official evaluator output (external)
RESULTS_DIR = Path(__file__).resolve().parent.parent / "results"   # our own deliverables --
# dev_subset.txt / LATENCY_BREAKDOWN.md live here so they're part of the committed submission,
# not the gitignored Full-Duplex-Bench clone.
COMMIT_GATE_BUFFER_MS = 400  # LK_COMMIT_BUFFER_MS default for this run -- see agent/lk_agent.py


def _tokens(value) -> list:
    return re.findall(r"[a-zA-Z0-9]+", str(value).lower())


def _load_result(data_dir: Path, folder_name: str, provider: str) -> dict | None:
    rf = data_dir / folder_name / f"result_{provider}.json"
    if not rf.exists():
        return None
    try:
        return json.loads(rf.read_text(encoding="utf-8"))
    except Exception:
        return None


def _key_info_chunk_time(raw: dict, first_turn_calls: list) -> float | None:
    """First asr_chunk (agent's own spoken output) whose word matches a token drawn from OUR
    OWN first-turn actual_tool_calls' argument values -- never from expected_tool_calls. Scoped
    to first-turn calls (see compute_row) so a later turn's argument doesn't get matched against
    the FIRST utterance's timing."""
    arg_tokens = set()
    for call in first_turn_calls:
        for v in (call.get("args") or {}).values():
            arg_tokens.update(_tokens(v))
    arg_tokens = {t for t in arg_tokens if len(t) >= 2}  # skip 1-char noise
    if not arg_tokens:
        return None
    for chunk in raw.get("asr_chunks", []):
        chunk_tokens = set(_tokens(chunk.get("text", "")))
        if chunk_tokens & arg_tokens:
            return chunk["timestamp"][0]
    return None


def compute_row(data_dir: Path, folder_name: str, provider: str) -> dict:
    raw = _load_result(data_dir, folder_name, provider)
    row = {"id": folder_name, "status": raw.get("status") if raw else "missing",
          "has_calls": False, "has_response": False}
    if raw is None:
        return row

    calls = raw.get("actual_tool_calls", [])
    user_end = raw.get("user_speech_end_rel")
    audio_start = raw.get("audio_agent_speech_start")
    asr_chunks = raw.get("asr_chunks", [])
    row["has_calls"] = bool(calls)
    row["has_response"] = bool(asr_chunks) or bool(raw.get("transcript"))

    if calls and user_end is not None:
        calls_sorted = sorted(calls, key=lambda c: c["timestamp_start"])
        row["user_end_to_first_call_s"] = round(calls_sorted[0]["timestamp_start"] - user_end, 3)

        # user_speech_end_rel marks only the END OF THE FIRST TURN (run_tool_benchmark.py looks
        # for the first >2s gap in the input audio, or falls back to end-of-recording); it is
        # NOT the end of the whole multi-turn conversation. actual_tool_calls and asr_chunks
        # cover the ENTIRE session. Mixing later-turn calls into a "first response" latency
        # figure produced nonsensical outliers (e.g. huge negative values) on multi-call,
        # multi-turn scenarios -- restrict stages 2-3 to calls that finished before the agent's
        # first spoken word, i.e. calls plausibly behind that first utterance.
        first_turn_calls = [c for c in calls_sorted
                            if audio_start is None or c["timestamp_end"] <= audio_start]
        if first_turn_calls:
            row["tool_exec_total_s"] = round(
                sum(c["timestamp_end"] - c["timestamp_start"] for c in first_turn_calls), 3)
            last_call_end = max(c["timestamp_end"] for c in first_turn_calls)
            if audio_start is not None:
                row["last_result_to_first_audio_s"] = round(audio_start - last_call_end, 3)

    if audio_start is not None and calls:
        first_turn_calls_for_key = [c for c in calls if c["timestamp_end"] <= audio_start] or calls[:1]
        key_t = _key_info_chunk_time(raw, first_turn_calls_for_key)
        if key_t is not None:
            row["first_audio_to_key_info_s"] = round(key_t - audio_start, 3)

    row["perceived_total_latency_s"] = raw.get("perceived_total_latency")
    return row


def _mean_median(rows: list, key: str) -> tuple:
    vals = [r[key] for r in rows if key in r and r[key] is not None]
    if not vals:
        return None, None, 0
    return round(statistics.fmean(vals), 3), round(statistics.median(vals), 3), len(vals)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default="ours_full")
    ap.add_argument("--all-100", action="store_true",
                    help="use all 100 folders under fdb_v3_data_released instead of dev_subset.txt")
    args = ap.parse_args()

    data_dir = FULL_DATA_DIR   # dev_subset.txt's folder names are a subset of the completed
    # ours_full run's 100 folders -- reuse those already-scored results directly rather than the
    # empty dev_subset_data/ scratch copy (that one's for the sweep runs' own fresh providers).
    if args.all_100:
        folders = sorted(d.name for d in data_dir.iterdir() if d.is_dir())
        subset_label = "all 100"
    else:
        folders = (RESULTS_DIR / "dev_subset.txt").read_text(encoding="utf-8").split()
        subset_label = "dev subset (25 recordings)"

    rows = [compute_row(data_dir, folder_name, args.provider) for folder_name in folders]

    stages = [
        ("user_end_to_first_call_s", "User speech end -> first tool call"),
        ("tool_exec_total_s", "Tool call -> tool result (total exec time)"),
        ("last_result_to_first_audio_s", "Last tool result -> first agent audio"),
        ("first_audio_to_key_info_s", "First agent audio -> key-info word"),
        ("perceived_total_latency_s", "Perceived total latency (FDB's own metric)"),
    ]

    lines = [f"# Latency breakdown — {args.provider}, {subset_label}", "",
            f"Computed from our own recorded `result_{args.provider}.json` timestamps, "
            f"`actual_tool_calls`, and agent output `asr_chunks` only — never `metadata.json`, "
            f"`expected_tool_calls`, or `input_transcript`.", "",
            "| Stage | Mean (s) | Median (s) | N |", "|---|---|---|---|"]
    for key, label in stages:
        mean, median, n = _mean_median(rows, key)
        lines.append(f"| {label} | {mean if mean is not None else 'n/a'} | "
                     f"{median if median is not None else 'n/a'} | {n}/{len(rows)} |")

    lines.append(f"| Commit-gate buffer (configured, not per-call measured) | "
                f"{COMMIT_GATE_BUFFER_MS / 1000:.3f} | {COMMIT_GATE_BUFFER_MS / 1000:.3f} | "
                f"{len(rows)}/{len(rows)} |")

    n_no_calls = sum(1 for r in rows if not r.get("has_calls"))
    n_no_response = sum(1 for r in rows if not r.get("has_response"))
    preemptive = [r["id"] for r in rows if r.get("user_end_to_first_call_s", 0) is not None
                 and r.get("user_end_to_first_call_s", 0) < 0]
    lines += ["", f"Examples with zero tool calls: {n_no_calls}/{len(rows)}. "
             f"Examples with no agent response at all: {n_no_response}/{len(rows)}.",
             f"**Pre-emptive first tool call (fired before the first-turn speech-end mark, "
             f"i.e. negative user_end_to_first_call_s): {len(preemptive)}/{len(rows)}** "
             f"({', '.join(preemptive) if preemptive else 'none'}) -- a qualitatively different "
             f"problem from slow-but-correct latency; averaging it into the mean above "
             f"understates how often this actually happens.", "",
             "## Per-example rows", "",
             "| id | status | first_call | tool_exec | result->audio | audio->key_info | "
             "perceived_total |",
             "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['id']} | {r['status']} | "
                     f"{r.get('user_end_to_first_call_s', '—')} | "
                     f"{r.get('tool_exec_total_s', '—')} | "
                     f"{r.get('last_result_to_first_audio_s', '—')} | "
                     f"{r.get('first_audio_to_key_info_s', '—')} | "
                     f"{r.get('perceived_total_latency_s', '—')} |")

    out_path = RESULTS_DIR / "LATENCY_BREAKDOWN.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote {out_path}")
    for key, label in stages:
        mean, median, n = _mean_median(rows, key)
        print(f"  {label}: mean={mean} median={median} n={n}/{len(rows)}")


if __name__ == "__main__":
    main()
