# Latency breakdown — ours_full, dev subset (25 recordings)

Computed from our own recorded `result_ours_full.json` timestamps, `actual_tool_calls`, and agent output `asr_chunks` only — never `metadata.json`, `expected_tool_calls`, or `input_transcript`.

| Stage | Mean (s) | Median (s) | N |
|---|---|---|---|
| User speech end -> first tool call | 4.798 | 5.185 | 22/25 |
| Tool call -> tool result (total exec time) | 1.108 | 0.795 | 20/25 |
| Last tool result -> first agent audio | 2.325 | 2.435 | 20/25 |
| First agent audio -> key-info word | 3.98 | 1.48 | 20/25 |
| Perceived total latency (FDB's own metric) | 10.063 | 9.0 | 24/25 |
| Commit-gate buffer (configured, not per-call measured) | 0.400 | 0.400 | 25/25 |

Examples with zero tool calls: 3/25. Examples with no agent response at all: 1/25.
**Pre-emptive first tool call (fired before the first-turn speech-end mark, i.e. negative user_end_to_first_call_s): 5/25** (ecommerce_25_69a9cf80f4d7668d5c815038, finance_10_66c4f3cb14cbfc4db836bd4e, finance_22_5f4a4da1575d605c43bef871, housing_06_5f4a4da1575d605c43bef871, travel_18_66f59c766e7e22e1f90d08f6) -- a qualitatively different problem from slow-but-correct latency; averaging it into the mean above understates how often this actually happens.

## Where the 11.9s actually goes

The official full-100 headline (`avg_response_latency_s = 11.90s`) uses the same formula as
`perceived_total_latency` above. The dev-subset mean here (10.06s, n=24/25 — full data coverage
now that the subset is folder-based) lands close to it.

Decomposing the tool-calling recordings (22/25 have a measurable first-call gap):

- **User speech end → first tool call: 4.80s mean / 5.19s median — still the largest single
  component**, roughly 45-50% of the total. Same conclusion as the first pass: this is the
  highest-leverage target for Step 1's `thinking_budget` and endpointing sweeps.
- **Tool call → tool result: 1.11s mean / 0.80s median.** Tool execution is fast; not the
  bottleneck.
- **Last tool result → first agent audio: 2.33s mean / 2.44s median.** Response-generation + TTS
  turnaround after the tool result is in hand.
- **Commit-gate buffer: fixed 400ms** — small next to the stages either side of it.

**Pre-emptive calls are more common than the first (incomplete) pass showed**: 5/25 now, once
every recording actually had data to check (was 1/25 when 5 of the 25 dev-subset ids had no
recording at all). Worth treating as a real, non-trivial failure rate, not a rare edge case.

## Per-example rows

| id | status | first_call | tool_exec | result->audio | audio->key_info | perceived_total |
|---|---|---|---|---|---|---|
| ecommerce_01_65e8cf8f4c7424fa062e54a3 | completed | — | — | — | — | None |
| ecommerce_05_695bd157114f0d2317f88617 | completed | 17.15 | 0.39 | 2.54 | 18.08 | 20.08 |
| ecommerce_08_61517db6a7589569521b2356 | completed | 4.32 | 0.4 | 2.56 | 1.12 | 7.28 |
| ecommerce_12_6998abd731d2ec50d067d5bd | completed | 4.78 | 0.4 | 2.34 | 0.4 | 7.52 |
| ecommerce_14_61517db6a7589569521b2356 | completed | 5.69 | 0.41 | 2.38 | 2.0 | 8.48 |
| ecommerce_17_5f4a4da1575d605c43bef871 | completed | 5.24 | 0.4 | 2.2 | 0.48 | 7.84 |
| ecommerce_21_65e8cf8f4c7424fa062e54a3 | completed | 10.13 | — | — | 32.56 | -15.28 |
| ecommerce_25_69a9cf80f4d7668d5c815038 | completed | -1.81 | 1.59 | 3.59 | 3.52 | 15.28 |
| finance_03_5ff07b5ee7a1d23e719e421e | completed | 6.07 | — | — | 1.44 | 6.4 |
| finance_07_5ff07b5ee7a1d23e719e421e | completed | 7.14 | 0.38 | 0.24 | 0.96 | 7.76 |
| finance_10_66c4f3cb14cbfc4db836bd4e | completed | -5.39 | 1.18 | 2.17 | 0.24 | 8.08 |
| finance_14_5f4a4da1575d605c43bef871 | completed | 4.68 | 0.8 | 2.66 | — | 9.52 |
| finance_18_6998abd731d2ec50d067d5bd | completed | 6.7 | 0.79 | 2.81 | 3.44 | 10.72 |
| finance_22_5f4a4da1575d605c43bef871 | completed | -5.52 | 0.38 | 0.66 | — | -0.4 |
| housing_03_5f4a4da1575d605c43bef871 | completed | 7.13 | 0.54 | 0.01 | 1.36 | 7.68 |
| housing_06_5f4a4da1575d605c43bef871 | completed | -0.63 | 2.11 | 2.36 | 1.84 | 10.88 |
| housing_11_62a885d5b6af18b3d4579e1b | completed | — | — | — | — | 7.44 |
| housing_15_5ff07b5ee7a1d23e719e421e | completed | — | — | — | — | 13.92 |
| housing_18_66c4f3cb14cbfc4db836bd4e | completed | 12.86 | 0.9 | 4.88 | 2.4 | 18.64 |
| housing_22_695bd157114f0d2317f88617 | completed | 15.25 | 0.58 | 2.33 | 1.52 | 18.16 |
| travel_01_62a885d5b6af18b3d4579e1b | completed | 0.89 | 1.8 | 2.49 | 1.12 | 7.36 |
| travel_05_6998abd731d2ec50d067d5bd | completed | 1.88 | 1.94 | 2.49 | 3.28 | 11.36 |
| travel_10_5f4a4da1575d605c43bef871 | completed | 5.13 | 3.13 | 2.77 | 2.0 | 22.24 |
| travel_18_66f59c766e7e22e1f90d08f6 | completed | -6.62 | 2.79 | 2.13 | 0.96 | 15.52 |
| travel_21_69a9cf80f4d7668d5c815038 | completed | 10.49 | 1.25 | 2.9 | 0.88 | 15.04 |
