# Ablation table (dev subset, 25 recordings, official exact-match scorer)

Each row runs the full pipeline (agent start -> smoke test -> 25-recording inference -> score -> official evaluate_tool_calls.py/evaluate_pass_rate.py, unmodified) against the isolated `dev_subset_data/` copy of exactly the 25 recordings in `results/dev_subset.txt`. See `scripts/run_dev_sweep_run.py` and `scripts/dev_subset_eval.py`.

| Config | Tool-sel | Arg acc (exact) | Pass@1 | Latency mean/median (s) | Interruption | Turn-take | N |
|---|---|---|---|---|---|---|---|
| R1: baseline-A (current defaults) | 77.3% | 25.9% | 8.0% | 11.44/9.92 | 11.1% | 72.0% | 25 |
