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
| `OPENAI_API_KEY` | **required** | the LLM judge (`gpt-4o`) that `evaluate_tool_calls.py`/`evaluate_pass_rate.py --use-llm` always call, regardless of which provider ran the agent — the official policy runs with this judge enabled ("a single pinned judge, so every team is judged identically"), so our local numbers need it too to mean the same thing. Also doubles as the key for the optional `gpt_realtime`/`cascaded` providers. |

FDB-v3's own evaluation scripts do **not** need LiveKit — only the inference step does
(`Full-Duplex-Bench/v3/README.md`). Without `OPENAI_API_KEY`, evaluation still runs but falls back
to exact-string argument matching instead of the LLM judge — a different, easier-to-game metric
than what actually gets scored; only trust numbers produced with `--use-llm` (which
`scripts/run_fdb_v3.sh` always passes).

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
