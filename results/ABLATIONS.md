# Ablation table (dev subset, 25 examples, exact-match scoring)

Every row filters the ALREADY-computed official evaluator output down to `results/dev_subset.txt`'s 25 scenario ids and re-averages -- never re-derives scoring logic. See `scripts/dev_subset_eval.py`.

| Config | Tool-sel | Arg acc (exact) | Pass@1 | Latency mean/median (s) | Interruption | Turn-take | N (recordings) |
|---|---|---|---|---|---|---|---|
| baseline (current defaults, 400ms buffer) | 76.4% | 51.2% | 37.0% | 11.84/9.72 | 7.7% | 96.3% | 27 (20 unique ids, 5 of the 25 have no recording) |
