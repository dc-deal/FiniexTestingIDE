"""
FiniexTestingIDE - Log Layout
The run categories — the API's `group` values, and the keys of file_logging.run_logs.
"""

# A run belongs to exactly ONE category, and the category IS its `group` in the API:
#
#   autotrader    a live session
#   single_runs   a standalone simulation run
#   sweeps        one combination of a parameter sweep
#
# WHERE each category writes is configuration (file_logging.run_logs) — one source read by the
# writers and by ReportStore alike, so a moved log root cannot make runs invisible. The NAMES
# live here because they are a contract: the API publishes them as `group`, and the run index
# skips the sweeps category by this name.
AUTOTRADER_GROUP = 'autotrader'
SINGLE_RUNS_GROUP = 'single_runs'
SWEEPS_GROUP = 'sweeps'
