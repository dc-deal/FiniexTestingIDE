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

# A comparison's scenario sets are named after it, so its backtest runs are found by that name;
# each set's file carries the same name
DIVERGENCE_SCENARIO_SET_NAME = 'divergence_{report_id}_segment_{segment}'
