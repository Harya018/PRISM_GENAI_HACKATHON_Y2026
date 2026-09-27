# Dashboard data-channel event contract

Emitted by `device_agent.py` over the LiveKit room's data channel (`publish_data(..., topic=...)`).
All additive: none of this changes what the agent hears, decides, or says — publish failures are
swallowed (see each hook's own comment), so a broken UI can never break the agent.

This is the contract actually implemented, time-boxed for the 17:00 deadline — it covers the
spec's intent (captions, per-stage latency, commit-gate decisions) with fewer, reused topics
rather than one topic per concept. `resolver`/`state`/`grounding` as literally separate topics
were cut; the same information is folded into `gate`/`captions` below (or, for `state`, derived
client-side from the running series of `gate` events — see dashboard.html).

## `captions` topic
`{"speaker": "user"|"agent", "text": str, "is_final": true, "t": unix_time}`
From `AgentSession`'s `conversation_item_added` event (covers both roles in one hook).

## `latency` topic
`{"stage": "turn_end"|"first_audio"|"tool_call"|"answer", "elapsed_ms": float|0.0, "t": unix_time}`
From `LatencyStager`, driven by `agent_state_changed` (`turn_end`/`first_audio`/`answer`) and
`CommitGate.on_committed` (`tool_call`) — all measured from end-of-turn.

## `gate` topic
`{"event": "buffered"|"superseded"|"executed"|"blocked_duplicate"|"confirmation_required"|
"confirmed"|"resolver_corrected_arg"|"resolver_correction_rejected", ...fields}` — one entry per
`commit_gate.py` log line, via its additive `on_event` hook. Fields vary by event (see
`agent/commit_gate.py`'s own `_emit` call sites): `tool`, `args`, `call_id`, `old_args`,
`old_call_id`, `from_value`/`to_value`/`arg` (corrections), `reason`, `result`.

This one topic carries what the spec called `resolver` (the `resolver_corrected_arg` /
`resolver_correction_rejected` events) and most of what it called `state` (the dashboard derives
turn/executed/corrected/blocked counters directly from this stream — see `stats` in
`dashboard.html`).

## Not implemented (cut for time — see PROGRESS.md / the dashboard build report)
- `stage` (structured per-stage status/detail) — the dashboard infers stage lighting from
  `latency`+`gate` events instead of a single dedicated event.
- `grounding` (camera-frame summary caption) — would need a new explicit vision-summary call in
  `device_agent.py`; not added.
- Explicit `state` topic with `intent`/`slots` — the agent has no structured slot-tracking to
  publish; the dashboard shows the most recent gate `tool`/`args` as a proxy instead.
