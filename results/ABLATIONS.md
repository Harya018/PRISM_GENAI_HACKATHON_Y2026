# Ablation table (dev subset, 25 recordings, official exact-match scorer)

Each row runs the full pipeline (agent start -> smoke test -> 25-recording inference -> score -> official evaluate_tool_calls.py/evaluate_pass_rate.py, unmodified) against the isolated `dev_subset_data/` copy of exactly the 25 recordings in `results/dev_subset.txt`. See `scripts/run_dev_sweep_run.py` and `scripts/dev_subset_eval.py`.

| Config | Tool-sel | Arg acc (exact) | Pass@1 | Latency mean/median (s) | Interruption | Turn-take | N |
|---|---|---|---|---|---|---|---|
| ~~R1: baseline-A (current defaults)~~ **INVALID** | ~~77.3%~~ | ~~25.9%~~ | ~~8.0%~~ | ~~11.44/9.92~~ | ~~11.1%~~ | ~~72.0%~~ | 25 |

**R1 is INVALID** — 7/25 recordings silent, 1 crash, 6x "server cancelled tool calls" in the
agent log. Suspected degraded/confounded run (environment or quota), not a real measurement of
current defaults. Do not cite these numbers.

## Diagnosis (completed, ~8 examples of quota spent)

- **No quota-exhaustion signature anywhere in the agent log**: zero occurrences of
  RESOURCE_EXHAUSTED/quota/1011/1008/GoAway; the one "429" match was part of an unrelated
  timestamp. `finance_07`'s crash (exit `3221225477` = `STATUS_ACCESS_VIOLATION`) is a native-level
  crash with no captured Python traceback — an isolated flake, not part of the silent-response
  pattern. Of the 7 silent recordings, the agent log shows two distinct sub-patterns: most
  (`ecommerce_01`, `ecommerce_05`, `finance_10`) show a full round-trip (`received job request`
  -> `RoomIO linked to participant` -> clean disconnect) with no response at all; two
  (`ecommerce_17`, `finance_03`) never even reached `RoomIO linked to participant` before the
  room closed. Both patterns co-occur with "server cancelled tool calls" warnings nearby.
- **Default code path confirmed behaviorally identical to the `fdb-run-ours-full` tag**, except
  for one thing: `git diff fdb-run-ours-full -- agent/` shows every Step 1/2/3 addition
  (`LK_THINKING_BUDGET`, `LK_KEY_INFO_FIRST`, etc.) is correctly inert when its env var is unset
  — confirmed directly in code, not just by intent (`thinking_config` is only ever added to the
  `RealtimeModel` kwargs `if THINKING_BUDGET is not None`). **The one real, always-active
  difference is dispatch mode**: the tag registers with `@server.rtc_session()` (anonymous),
  current `main` with `@server.rtc_session(agent_name="fdb-ours")` (explicit) — flipped in commit
  `010e604`, *after* the tag was cut, once the original anonymous-dispatch full-100 run had
  already finished.
- **Discriminator test (4 recordings each, tag/anonymous vs. main/explicit, same 4 both times)
  did NOT match either of the predicted outcomes.** Test A (tag, anonymous dispatch): 0/4 real
  responses — all silent, including two recordings (`ecommerce_08`, `ecommerce_12`) that had
  perfectly good responses in R1 just hours earlier. Test B (main, explicit dispatch), run
  immediately after: 3/4 real responses. This is the *reverse* of "tag OK, main bad," and not
  "both equally degraded" either — **the pattern is short-timescale transient fluctuation
  (worse, then better, within ~15 minutes), not something tied to dispatch mode or to either
  commit's code.** Recommendation: treat R1's high silence rate as noise-floor variance, not a
  regression — safe to retry the real 25-recording run.

Full narrative, timestamps, and log excerpts: see `PROGRESS.md`.
