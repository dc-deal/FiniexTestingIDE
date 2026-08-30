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
# live here because they are a contract: the API publishes them as `group`, and a consumer
# filters by these names — the run index lists ALL THREE categories and lets the caller
# decide, rather than deciding for it.
AUTOTRADER_GROUP = 'autotrader'
SINGLE_RUNS_GROUP = 'single_runs'
SWEEPS_GROUP = 'sweeps'

# A sweep loads its data ONCE and reuses it across every combination. That load's record is
# sweep-level output, so it lands beside the sweep's ranked.csv rather than in a directory
# shaped like a run — the run index counts runs, and a shared data load is not one.
MOUNT_BUILD_LOG = 'mount_build.log'

# The subfolder a run's report artifacts live in, inside its run directory. Part of the layout
# contract like the names above: the store resolves artifacts through it, and the run index needs
# it without importing the store.
IO_SUBDIR = 'io'
