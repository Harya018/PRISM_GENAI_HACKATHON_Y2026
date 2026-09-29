# Patches to the FDB-v3 benchmark harness

Every one of these is **harness reliability only — no scoring logic changed.** None of them
touch how a scenario is scored, what counts as a correct tool call, or the official judge's
behavior. They exist because this is a single CPU-only dev machine running a benchmark designed
around GPU inference + separate scoring, and running both concurrently on one machine caused real,
reproducible failures (documented in each diff's own header and in `AI_USAGE_LOG.md`).

| File | What changed | Why |
|---|---|---|
| `lk_agent_tool.py.diff` | `AgentServer(load_threshold=0.95, num_idle_processes=1)`; eager plugin import; explicit dispatch `agent_name="fdb-baseline"` | Default `num_idle_processes` is `cpu_count()` warm processes — too heavy here; caused the worker to go "unresponsive" and silently stop accepting jobs mid-run. Explicit dispatch means this worker can only ever be assigned a room created with `--agent-name fdb-baseline`, never one meant for `fdb-ours` or the extension. |
| `run_tool_benchmark.py.diff` | Subprocess timeout on the per-example inference call; `infer_only` mode; fixes `--asr-only` losing `room_name`/tool-call telemetry; fixes an unconditional `hasattr(model, 'cuda')` crash on CPU-only machines; threads `agent_name` through to `livekit_inference.py` | An unbounded subprocess call hung forever on a dead agent with no recovery path. The `--asr-only` bug (telemetry silently empty) and the CUDA-check bug pre-date this session's changes — real upstream bugs, not something introduced here. |
| `run_tool_benchmark_all_released.py.diff` | `--infer-only` flag (batch-level counterpart); `--agent-name`/`LK_AGENT_NAME` flag | Lets a full run do all inference first, then all NeMo scoring, never at the same time. `--agent-name` is required end-to-end once an agent uses explicit dispatch — see `livekit_inference.py.diff`. |
| `livekit_inference.py.diff` | Opt-in `--agent-name` requesting explicit LiveKit dispatch (`RoomConfiguration`/`RoomAgentDispatch`) when minting the join token | **Without this, a batch run would hang forever**: `fdb-ours`/`fdb-baseline` register for explicit dispatch only (see the two diffs above), and a token minted with plain anonymous dispatch can never be routed to them — no job is ever assigned, so `run_tool_benchmark_all_released.py` would wait indefinitely for a room the agent will never receive. Caught by a fresh-clone reproduction test in a Docker container, which is exactly the failure mode that test exists to catch. Defaults to `None` (anonymous dispatch), unchanged from stock when omitted. |
| `evaluate_tool_calls.py.diff` | Opt-in `--proxy-llm` (Gemini) judge | No `OPENAI_API_KEY` is available in this environment. `--proxy-llm` is **never the default** — the official `--use-llm` (gpt-4o) path is unchanged and is what the one-command reproduction script uses when a key is present. Every proxy-judged report is labelled `"judge": "gemini-3.8-flash (PROXY — not the official judge...)"`. Note: the free tier's 20-requests/day/project quota cannot cover a full-100 run — proxy numbers from a full run should be treated as indicative only. |
| `evaluate_pass_rate.py.diff` | Same `--proxy-llm` addition, same rules | — |

**`scripts/run_fdb_v3.sh` itself also needed a fix once these were verified**: it never actually
passed `--agent-name` when invoking `run_tool_benchmark_all_released.py`, so even with every patch
above applied correctly, the one-command script would still have deadlocked against an
explicit-dispatch agent. Fixed by passing `--agent-name fdb-ours`/`fdb-baseline` (matching each
agent's own `@server.rtc_session(agent_name=...)`) in step 6. Found and fixed via an actual
fresh-clone Docker reproduction test — see `AI_USAGE_LOG.md`/`PROGRESS.md` for the full story.

## New files (not modifications — nothing to diff)

These are additions to the `v3/` working copy, not changes to existing benchmark logic:

- `overnight_runner.py` — orchestrates a full run with auto-recovery (restarts the agent if it
  dies or stalls; resumes without `--force`, never re-scoring or re-running anything already
  done). Infrastructure only; doesn't touch what either agent does.
- `run_baseline_with_retry.py` — earlier, simpler retry wrapper (superseded by
  `overnight_runner.py` for anything long-running, kept for quick one-off checks).
- `lk_agent_ours.py`, `commit_gate.py`, `resolver.py`, `instructions.py`, `normalize.py` — our
  submitted agent and its supporting modules, copied in from `fdb-agent/agent/` (see that
  project's own README for what these do). These implement our agent's actual behavior and are
  the thing being
  evaluated, not a harness patch.

## Applying these to a fresh clone

`fdb-agent/scripts/run_fdb_v3.sh` (the one-command reproduction script) applies the reliability
patches automatically via `scripts/patch_fdb_v3.py` before running anything, and always uses
the official `--use-llm` judge when `OPENAI_API_KEY` is set — `--proxy-llm` requires an explicit
flag and is never invoked by that script on its own.
