"""
The bounds the divergence report's verdict holds a live run to (#512) — fixed before the run's
first figure, by the verdict rules' first sections.

A bound chosen after the figures always confirms them, so these values change only as a new
version of the rules, before the run starts, never after. Each report records the bounds and the
weights it applied.
"""

# Measure 1: one-sided decisions explained by a named kind, at most this many in all
EXPLAINED_FLIPS_MAX = 2

# Measure 2b: (live fill − live submission mid) − (backtest fill − backtest submission mid), in %
# of the submission mid — about three times the largest slippage the field study measured
EXECUTION_PRICE_BOUND_PCT = 0.10

# Measure 2c: the venue's fee rate against the frozen taker rate, in percentage points
FEE_RATE_BOUND_PP = 0.001

# Measure 2d: live lots ÷ backtest lots against live equity ÷ backtest equity, in %
SIZE_BOUND_PCT = 1.0

# The zones of 2b, 2c and 2d: orders outside the bound a measure may hold and still pass, each
# one traced to its cause
YELLOW_ZONE_MAX_ORDERS = 2

# The day attribution: what is left of a day's difference after its parts, in the account
# currency — above it the day is not checked and named as a defect
ATTRIBUTION_RESIDUAL_BOUND = 0.01

# A measure of the run passes only over this many compared trading days of the run's thirty
MINIMUM_COMPARED_DAYS = 25
RUN_TRADING_DAYS = 30

# Below this many fills, measure 2 lists every order and computes no percentile
FEW_FILLS = 20

# The weighted cross-section over the binding categories — a display, never the verdict; keyed by
# the measures' names in the report
CROSS_SECTION_WEIGHTS = {
    'decisions': 0.40,
    'execution_price': 0.20,
    'fill_completeness': 0.15,
    'size': 0.15,
    'fee_rate': 0.10,
}
