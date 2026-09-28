# Latency breakdown — ours_full, dev subset (25)

Computed from our own recorded `result_ours_full.json` timestamps, `actual_tool_calls`, and agent output `asr_chunks` only — never `metadata.json`, `expected_tool_calls`, or `input_transcript`.

| Stage | Mean (s) | Median (s) | N |
|---|---|---|---|
| User speech end -> first tool call | 6.095 | 5.13 | 17/25 |
| Tool call -> tool result (total exec time) | 0.946 | 0.4 | 16/25 |
| Last tool result -> first agent audio | 2.531 | 2.425 | 16/25 |
| First agent audio -> key-info word | 5.28 | 0.96 | 11/25 |
| Perceived total latency (FDB's own metric) | 10.653 | 8.8 | 19/25 |
| Commit-gate buffer (configured, not per-call measured) | 0.400 | 0.400 | 25/25 |

Examples with zero tool calls: 8/25. Examples with no agent response at all: 6/25.
**Pre-emptive first tool call (fired before the first-turn speech-end mark, i.e. negative user_end_to_first_call_s): 1/25** (travel_18) -- a qualitatively different problem from slow-but-correct latency; averaging it into the mean above understates how often this actually happens.

## Where the 11.9s actually goes

The official full-100 headline (`avg_response_latency_s = 11.90s`, `evaluate_tool_calls.py`'s
`evaluate_latency()`) is computed as `audio_agent_speech_start - user_speech_end_rel` — the exact
same formula as `perceived_total_latency` above. The dev-subset mean (10.65s, n=19/25) lands
close to it, as expected for a 25-example sample of the 100.

Decomposing the tool-calling examples (17/25 on the dev subset) into the three measurable
stages:

- **User speech end → first tool call: 6.10s mean / 5.13s median — by far the largest single
  component**, roughly 55-60% of the total. This is the time from "user stops talking" to "model
  decides to call a tool," which bundles end-of-turn detection, the model's own "thinking"
  latency, and however long it takes the model to commit to a function call. **This is the
  highest-leverage target** — directly why Step 1's `thinking_budget` sweep (L1) and endpointing
  sweep (L4) are worth trying before anything else.
- **Tool call → tool result: 0.95s mean / 0.40s median — small, and the mode across individual
  calls is ~0.39-0.40s.** Tool execution itself is fast; this is not the bottleneck, so nothing
  in the mock tool implementations needs speeding up.
- **Last tool result → first agent audio: 2.53s mean / 2.43s median.** The model's own
  response-generation + TTS turnaround after it has the tool result in hand — also plausibly
  `thinking_budget`-sensitive (a second "thinking" pass can happen here, after tool results come
  back, before the spoken reply starts).
- **Commit-gate buffer: a fixed 400ms**, not per-call measured (see header) — small next to the
  6.1s+2.5s either side of it; even eliminating it entirely wouldn't move the needle much on its
  own. Lowering it (L3, 250ms) is worth trying but isn't where the time actually is.

Sum of the three measurable stages (~9.6s) undershoots the 10.65s perceived-total mean because
(a) each stage has a different N (not every example has all three measurable), and (b) 8/25
examples make zero tool calls yet still carry a `perceived_total_latency` value not decomposed
by these stages at all (pure conversational turns, response-generation latency only).

**One qualitatively different finding, not a latency number**: `travel_18` shows a *negative*
user-end-to-first-call gap (-6.62s) — its first tool call fired 6.6s *before* the recorded
first-turn speech-end mark, i.e. a genuine pre-emptive call. 1/25 on this subset; worth watching
on the full 100 once any turn-detection/endpointing change is tried, since that's exactly the
failure mode Step 4's turn-detector experiment targets.

## Per-example rows

| id | status | first_call | tool_exec | result->audio | audio->key_info | perceived_total |
|---|---|---|---|---|---|---|
| ecommerce_01 | completed | — | — | — | — | None |
| ecommerce_05 | completed | 17.15 | 0.39 | 2.54 | 18.08 | 20.08 |
| ecommerce_09 | completed | 1.32 | 0.4 | 6.28 | 0.72 | 8.0 |
| ecommerce_13 | completed | 7.37 | 0.78 | 2.78 | — | 12.0 |
| ecommerce_17 | completed | 5.24 | 0.4 | 2.2 | 0.48 | 7.84 |
| ecommerce_21 | completed | 10.13 | — | — | 32.56 | -15.28 |
| ecommerce_25 | completed | 4.9 | 1.59 | 2.79 | 0.48 | 19.44 |
| finance_04 | completed | 4.61 | 0.39 | 2.68 | — | 7.68 |
| finance_08 | completed | 4.8 | 0.4 | 2.24 | — | 7.44 |
| finance_12 | completed | 4.36 | 0.39 | 2.29 | — | 7.04 |
| finance_16 | completed | 12.47 | 0.39 | 2.42 | — | 15.28 |
| finance_20 | completed | 17.14 | 0.39 | 2.31 | — | 19.84 |
| finance_24 | missing | — | — | — | — | — |
| housing_03 | completed | 7.13 | 0.54 | 0.01 | 1.36 | 7.68 |
| housing_07 | missing | — | — | — | — | — |
| housing_11 | completed | — | — | — | — | 7.44 |
| housing_15 | completed | — | — | — | — | 13.92 |
| housing_19 | completed | 5.3 | 1.06 | 2.44 | 0.0 | 8.8 |
| housing_23 | missing | — | — | — | — | — |
| travel_02 | completed | 0.36 | 0.4 | 2.18 | 0.32 | 7.92 |
| travel_06 | missing | — | — | — | — | — |
| travel_10 | completed | 5.13 | 3.13 | 2.77 | 2.0 | 22.24 |
| travel_14 | completed | 2.82 | 1.7 | 2.43 | 1.12 | 9.52 |
| travel_18 | completed | -6.62 | 2.79 | 2.13 | 0.96 | 15.52 |
| travel_22 | missing | — | — | — | — | — |
