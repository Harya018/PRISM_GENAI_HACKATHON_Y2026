# Theme 5 — FDB-v3 submission

Scored per `docs/Theme05_Participant_Guide_UPDATED_FBD.docx`: 60% organizers' re-run of
[Full-Duplex-Bench v3](https://github.com/DanielLin94144/Full-Duplex-Bench) (FDB-v3) via
`scripts/run_fdb_v3.sh`, 20% an extension use case, 20% documentation/architecture/video. Full
technical background: `../RESEARCH_FDB.md`. Architecture, results, and the extension writeup land
here as Phases 3–6 complete — this file currently covers environment setup only.

## Dashboard

`extension/dashboard.html`, served at `http://localhost:8450` by `python extension/web_client.py`
(alongside the extension agent, `python extension/device_agent.py start` — never while a
benchmark run is active). Four tabs:

- **Live** — join with mic + camera and talk to the device-support agent. Left: camera preview
  and a live captioned conversation (both speakers). Center: the seven-stage pipeline (Mic →
  Transcript → Resolver → Reasoning → Commit Gate → Tools → Reply), each stage lighting up with
  a one-line live detail and its latency, plus a correction timeline showing corrected-away
  values struck through and the resolved value in green. Right: session counters (turns, tool
  calls executed, corrections caught, duplicates blocked, confirmations requested) and a
  newest-first commit-gate decision feed. A **Replay** dropdown plays back a recorded session
  through the same rendering code at real speed, clearly labelled while active; a **Record this
  session** button saves the next live session as a replay for later (`extension/replays/*.jsonl`,
  via `POST /api/replays/save`).
- **Results** — every number is read from `docs/dashboard_data.json` (built by
  `scripts/build_dashboard_data.py` from the real `result_*.json`/evaluation-report files, never
  hardcoded): full-100 headline metrics for "ours", an ours-vs-baseline comparison over the 51
  recordings baseline actually completed (never "0.0%"/"n/a" for missing data — labelled "no
  baseline data" instead), breakdowns by domain/difficulty/disfluency type, a "commit gate on the
  benchmark" panel with what's honestly derivable from the frozen run's own recorded timestamps,
  and a limitations footnote (scorer strictness, baseline coverage, the known cross-turn
  correction gap).
- **Benchmark Explorer** — browse and filter all 100 recordings (domain/difficulty/disfluency/
  pass-fail), inspect any one: transcript, expected vs. actual tool calls with ✓/✗ markers, and
  the baseline's result on the same recording where one exists. Inspection only — never changes
  the agent.
- **Architecture** — a five-step explanation of the pre-emptive-call problem and how the commit
  gate, block-only resolver, and idempotency/confirmation checks address it.

Regenerate the Results/Explorer data after any new benchmark run: `python
scripts/build_dashboard_data.py` (writes `docs/dashboard_data.json`). See
`extension/EVENTS.md` for the live data-channel event contract.

## Environment variables

Copy `.env.example` to `.env` (gitignored — never commit it; verify with `git check-ignore .env`).

| variable | required? | used for |
|---|---|---|
| `LIVEKIT_URL` | **required** | LiveKit Cloud project URL (free tier: cloud.livekit.io) — inference streams audio through a LiveKit room |
| `LIVEKIT_API_KEY` | **required** | LiveKit Cloud auth |
| `LIVEKIT_API_SECRET` | **required** | LiveKit Cloud auth |
| `GOOGLE_API_KEY` | **required** | our declared provider — Gemini Live (`LK_PROVIDER=gemini2_5`), via `livekit.plugins.google.realtime.RealtimeModel` |
| `OPENAI_API_KEY` | strongly recommended | the LLM judge (`gpt-4o`) that `evaluate_tool_calls.py`/`evaluate_pass_rate.py --use-llm` always call, regardless of which provider ran the agent — the official policy runs with this judge enabled ("a single pinned judge, so every team is judged identically"), so our local numbers need it too to mean the same thing. `scripts/run_fdb_v3.sh` always uses `--use-llm` (never the proxy judge) whenever this key is set. Also doubles as the key for the optional `gpt_realtime`/`cascaded` providers. |

FDB-v3's own evaluation scripts do **not** need LiveKit — only the inference step does
(`Full-Duplex-Bench/v3/README.md`). Without `OPENAI_API_KEY`, pass `--proxy-llm` to
`run_fdb_v3.sh` to use a Gemini-based PROXY judge instead (same prompts as the official gpt-4o
judge — see "Changes to the benchmark harness" below); every report it produces is labelled
`"judge": "... (PROXY ...)"` so it's never mistaken for the official numbers. Without either,
evaluation falls back to exact-string argument matching — a different, easier-to-game metric
than what actually gets scored.

## Declared provider

**Gemini Live** (`LK_PROVIDER=gemini2_5`, `google.realtime.RealtimeModel`) is our default and
declared provider for the official re-run. `scripts/run_fdb_v3.sh --baseline` runs FDB-v3's own
unmodified template for comparison; `LK_PROVIDER=<other>` (see `RESEARCH_FDB.md` §2) can be used
to compare against other supported providers, but only the Gemini Live run is what gets submitted.

## Quickstart

```bash
cp .env.example .env   # fill in the table above
./scripts/run_fdb_v3.sh --baseline   # FDB-v3's own stock template — establishes the baseline
./scripts/run_fdb_v3.sh              # our agent (agent/lk_agent.py)
```

Results, logs, the exact FDB-v3 commit, and pinned dependency versions land in
`results/{baseline,ours}/<run_id>/`.

## Changes to the benchmark harness

`scripts/run_fdb_v3.sh` applies a small set of patches to the FDB-v3 checkout before running
anything (`scripts/patch_fdb_v3.py`, applying `patches/*.diff`). **Every one of them is harness
reliability only — none change scoring logic, what counts as a correct tool call, or the
official judge's behavior.** Full detail, reasons, and exact diffs: `patches/README.md`. In
short:

- Two real upstream bugs, fixed as compatibility patches: a CUDA-availability check that crashed
  unconditionally on any CPU-only machine, and a plugin-import-timing issue against current
  `livekit-agents` releases.
- A real pre-existing bug in the benchmark's own `--asr-only` re-scoring mode: it silently
  dropped tool-call telemetry (`room_name` was never restored from a prior result), so a rescore
  pass would report zero tool calls for every scenario regardless of what the agent actually did.
- Reliability fixes for running this benchmark on a single CPU-only machine: a subprocess timeout
  on the per-example inference call (previously unbounded — a dead agent hung the whole run
  forever), a lower LiveKit worker `num_idle_processes`/higher `load_threshold` (the default tries
  to keep `cpu_count()` warm processes ready, which overloads a laptop), and an `--infer-only` /
  `--asr-only` two-phase split so NeMo's CPU-heavy transcription never runs while the agent is
  live accepting job dispatches.
- An opt-in `--proxy-llm` judge (Gemini, identical prompts to the official gpt-4o judge) for
  environments without `OPENAI_API_KEY`. **Opt-in only, never the default** — `run_fdb_v3.sh`
  always uses the official `--use-llm` (gpt-4o) path unless `--proxy-llm` is passed explicitly,
  and every report the proxy judge produces is labelled as such. Note: the free-tier Gemini
  quota (20 requests/day/project) cannot cover a full-100 run's ~200 judge calls, so treat proxy
  numbers from a full run as indicative only — use `--use-llm` with an `OPENAI_API_KEY` for
  numbers you'd cite.

## Limitations

- **Same-utterance self-correction is handled; cross-turn correction (after a pause, in a
  separate turn) is not.** The commit gate's debounce buffer (`agent/commit_gate.py`) correctly
  resolves a correction that arrives *within* the same continuous utterance — e.g. "flights to
  Paris — actually, no, make that Berlin" never fires the Paris call, because the resolver sees
  the correction before the buffer's debounce window closes. But once the model has already
  emitted a tool call and moved on to a **new turn**, there is no buffer left to intercept a
  correction that arrives later. Two observed failures of exactly this kind: `travel_10`
  (Miami → Paris) and `finance_12` (100 → 150) — in both, the agent's stale call had already
  fired *before* a correction that came after a long pause, in what the transcript treats as a
  separate turn rather than a continuation of the same one.
  - We deliberately did not attempt a fix for this after the code freeze. The two real fixes we
    considered — (a) a **provisional-vs-confirmed commit** state for read-only calls, so a
    same-session correction can still retract a call whose result hasn't been spoken back to the
    user yet, and (b) **longer semantic endpointing**, holding the turn open across a pause when
    the transcript alone can't yet tell whether the user is done or mid-correction — both change
    turn-taking/commit timing behavior broadly enough that we didn't want to risk it against a
    frozen, already-scored agent this late. This is also not a gap unique to our approach: the
    FDB-v3 paper itself (arXiv 2604.04847) treats disambiguating a genuine cross-turn correction
    from an unrelated new request as an open problem for full-duplex tool-use agents, not a
    solved one.
- **`ecommerce_01` (1/100 in the scored `ours_full` run) came back with an empty transcript** on
  two separate attempts, with no exception or state change in the agent's own log — read as a
  rare Gemini Live session-activation flake rather than a reproducible bug, and left as a
  documented residual gap rather than retried indefinitely.
- **The Gemini proxy judge cannot score a full 100-example run** (see "Changes to the benchmark
  harness" above) — a structural quota limit of the free tier, not something patchable in this
  codebase. It remains useful for spot checks and the 17-example verification subset.

## What's next

- Provisional/confirmed commit state for read-only tool calls, to close the cross-turn
  correction gap above without touching same-utterance debounce behavior that's already working.
- Longer, adaptive semantic endpointing keyed on disfluency-feature likelihood rather than a
  fixed pause duration, so a genuine mid-thought pause and a completed turn are told apart more
  reliably before a call commits.
- Extend the extension's (`extension/`) per-stage latency telemetry back into the benchmark path
  itself (currently extension-only, gated off the scored agent by design) once there's a UI to
  consume it, to make future cross-turn-correction failures diagnosable from timing data instead
  of manual transcript reading.
