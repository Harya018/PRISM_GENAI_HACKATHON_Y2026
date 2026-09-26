# Patches to the FDB-v3 benchmark harness

Every one of these is **harness reliability only — no scoring logic changed.** None of them
touch how a scenario is scored, what counts as a correct tool call, or the official judge's
behavior. They exist because this is a single CPU-only dev machine running a benchmark designed
around GPU inference + separate scoring, and running both concurrently on one machine caused real,
reproducible failures (documented in each diff's own header and in `AI_USAGE_LOG.md`).

| File | What changed | Why |
|---|---|---|
| `lk_agent_tool.py.diff` | `AgentServer(load_threshold=0.95, num_idle_processes=1)` | Default `num_idle_processes` is `cpu_count()` warm processes — too heavy here; caused the worker to go "unresponsive" and silently stop accepting jobs mid-run. |
| `run_tool_benchmark.py.diff` | Subprocess timeout on the per-example inference call; `infer_only` mode; fixes `--asr-only` losing `room_name`/tool-call telemetry | An unbounded subprocess call hung forever on a dead agent with no recovery path. The `--asr-only` bug (telemetry silently empty) pre-dates this session's changes — a real upstream bug, not something introduced here. |
| `run_tool_benchmark_all_released.py.diff` | `--infer-only` flag (batch-level counterpart) | Lets a full run do all inference first, then all NeMo scoring, never at the same time. |
| `evaluate_tool_calls.py.diff` | Opt-in `--proxy-llm` (Gemini) judge | No `OPENAI_API_KEY` is available in this environment. `--proxy-llm` is **never the default** — the official `--use-llm` (gpt-4o) path is unchanged and is what the one-command reproduction script uses when a key is present. Every proxy-judged report is labelled `"judge": "gemini-2.5-flash (PROXY — not the official judge...)"`. |
| `evaluate_pass_rate.py.diff` | Same `--proxy-llm` addition, same rules | — |

## New files (not modifications — nothing to diff)

These are additions to the `v3/` working copy, not changes to existing benchmark logic:

- `overnight_runner.py` — orchestrates a full run with auto-recovery (restarts the agent if it
  dies or stalls; resumes without `--force`, never re-scoring or re-running anything already
  done). Infrastructure only; doesn't touch what either agent does.
- `run_baseline_with_retry.py` — earlier, simpler retry wrapper (superseded by
  `overnight_runner.py` for anything long-running, kept for quick one-off checks).
- `lk_agent_ours.py`, `commit_gate.py`, `resolver.py`, `instructions.py` — our submitted agent
  and its supporting modules, copied in from `fdb-agent/agent/` (see that project's own README
  for what these do). These implement our agent's actual behavior and are the thing being
  evaluated, not a harness patch.

## Applying these to a fresh clone

`fdb-agent/scripts/run_fdb_v3.sh` (the one-command reproduction script) applies the reliability
patches automatically via `scripts/patch_fdb_v3.py` before running anything, and always uses
the official `--use-llm` judge when `OPENAI_API_KEY` is set — `--proxy-llm` requires an explicit
flag and is never invoked by that script on its own.
