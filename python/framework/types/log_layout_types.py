"""
FiniexTestingIDE - Log Layout
The run types — the API's `group` values, and the keys of file_logging.run_logs.
"""

# A run has exactly ONE type, and the type IS its `group` in the API:
#
#   simulation    a backtest — standalone, or one combination of a parameter sweep
#   live          an AutoTrader session
#
# TWO values, not three, because type and NESTING are different questions and each has its own
# field. A sweep combination is a simulation with a `parent_id`; a live day fragment (#476) is a
# live run with a `parent_id`. Folding nesting into the type would make the most basic question —
# "is this a simulation?" — a two-value comparison, and would need a new value for every new kind
# of parent.
#
# WHERE each type writes is configuration (file_logging.run_logs) — one source read by the
# writers and by ReportStore alike, so a moved log root cannot make runs invisible. The NAMES
# live here because they are a contract: the API publishes them as `group`, and the run index
# lists both types and lets the caller decide, rather than deciding for it.
RUN_TYPE_SIMULATION = 'simulation'
RUN_TYPE_LIVE = 'live'

# A sweep's combinations nest under this subfolder of the simulation root. A directory level,
# never a run type — the type of a combination is `simulation`, and its `parent_id` names the
# sweep it belongs to.
SWEEPS_SUBDIR = 'sweeps'

# A sweep loads its data ONCE and reuses it across every combination. That load's record is
# sweep-level output, so it lands beside the sweep's ranked.csv rather than in a directory
# shaped like a run — the run index counts runs, and a shared data load is not one.
MOUNT_BUILD_LOG = 'mount_build.log'

# The subfolder a run's report artifacts live in, inside its run directory. Part of the layout
# contract like the names above: the store resolves artifacts through it, and the run index needs
# it without importing the store.
IO_SUBDIR = 'io'
