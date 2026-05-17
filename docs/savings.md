# Savings benchmark

Synthetic 500-row workload, deterministic, hermetic. Numbers are what `scripts/bench_savings.py` produced on the host that wrote this file — re-run the script to refresh.

- Cheap model: `claude-haiku-4-5` ($1.00/MTok input)
- Strong model: `claude-opus-4-7` ($15.00/MTok input)
- Workload mix: {'redundant': 300, 'easy': 150, 'hard': 50}
- Total prompt tokens (sum across rows): 57,674

| Strategy | Rows | $ spent | $ saved | % saved | Mean quality | Extra |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| baseline (no optimization, cheap model) | 500 | $0.0577 | $0.0000 | 0.0% | 0.886 | — |
| prompt caching (system prefix) | 500 | $0.0092 | $0.0485 | 84.0% | 0.886 | cache_writes=1, cache_reads=499 |
| semantic cache (HashEmbedder, threshold 0.95) | 500 | $0.0253 | $0.0324 | 56.2% | 0.886 | hits=280, misses=220, hit_rate=0.56 |
| uncertainty router (entropy threshold 1.5) | 500 | $0.1469 | $-0.0892 | -154.8% | 0.921 | escalated=50, escalation_rate=0.1 |
| batch API (discount 0.50×) | 500 | $0.0288 | $0.0288 | 50.0% | 0.886 | discount_factor=0.5, compare_savings_pct_with_outputs=0.5 |

Cumulative savings per row (per strategy) live in `savings.json`.
The Streamlit dashboard renders those series; see the repo README.
