# Theme 5 — FDB-v3 submission

Scored per `docs/Theme05_Participant_Guide_UPDATED_FBD.docx`: 60% organizers' re-run of
[Full-Duplex-Bench v3](https://github.com/DanielLin94144/Full-Duplex-Bench) (FDB-v3) via
`scripts/run_fdb_v3.sh`, 20% an extension use case, 20% documentation/architecture/video. Full
technical background: `../RESEARCH_FDB.md`. Architecture, results, and the extension writeup land
here as Phases 3–6 complete — this file currently covers environment setup only.

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
  and every report the proxy judge produces is labelled as such.
