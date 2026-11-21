"""
Where a divergence report keeps its own record, and the names of what it holds (#512).

One directory per comparison under the run tree, beside the simulation and AutoTrader runs; the
backtests it ran are runs of their own and are only named here.
"""

# The store's directory, a sibling of the run tree's simulation directory
DIVERGENCE_SUBDIR = 'divergence'

# What one comparison's directory holds
DIVERGENCE_REPORT_FILE = 'report.json'
DIVERGENCE_REPORT_TEXT_FILE = 'report.txt'
DIVERGENCE_LIVE_INDEX_FILE = 'live_index.parquet'
# The warmup history a segment's backtest started from, kept beside the comparison
DIVERGENCE_WARMUP_RECORD_FILE = 'warmup_segment_{segment}.json'

# A comparison's scenario sets are named after it, so its backtest runs are found by that name;
# each set's file carries the same name
DIVERGENCE_SCENARIO_SET_NAME = 'divergence_{report_id}_segment_{segment}'
# The set of a segment's second backtest, at the latency its live orders measured — beside the
# verdict, never in it
DIVERGENCE_LATENCY_SCENARIO_SET_NAME = 'divergence_{report_id}_segment_{segment}_measured_latency'
