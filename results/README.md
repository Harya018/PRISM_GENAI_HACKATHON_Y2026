# Results

- **`ours/20260927_overnight_full/`** / **`baseline/20260927_overnight_full/`** — the raw,
  unmodified `evaluate_tool_calls.py`/`evaluate_pass_rate.py` reports from our best full-100
  run (ours: 100/100 complete; baseline: 51/100, see that folder's `run_config.json` for why).
  Each folder's `run_config.json` documents provider, agent script, judge, and an honest note on
  what wasn't independently recorded (seed) — read it before citing a number from here.
- **`OVERNIGHT_REPORT.md`** — the human-readable head-to-head summary of that run (by domain, by
  difficulty, by disfluency type).
- **`ABLATIONS.md`** — the dev-subset (25-recording) sweep for the accuracy/latency improvement
  round after the full-100 run above; separate methodology, see that file's own header.
- **`LATENCY_BREAKDOWN.md`**, **`dev_subset.txt`** — supporting data for the ablation sweep.
- **`../docs/dashboard_data.json`** — the curated, aggregated version of the full-100 numbers
  that `extension/dashboard.html`'s Results tab actually reads (built by
  `scripts/build_dashboard_data.py` from the same source reports as `ours/`/`baseline/` above).

**Important, stated plainly:** the reports in `ours/`/`baseline/` above used `judge="none"`
(exact-match only) — **not** the official `--use-llm` (gpt-4o) judge that the graded re-run of
`scripts/run_fdb_v3.sh` uses, because no `OPENAI_API_KEY` was available in this environment when
this run was made. Treat these as our own best self-reported numbers, not a stand-in for the
official re-run's numbers — per the participant guide, "only our re-run counts; your
self-reported numbers guide us but are not scored."

A fresh run via `./scripts/run_fdb_v3.sh` (or `--baseline`) writes its own dated run to
`results/{ours,baseline}/<run_id>/`, alongside these.
