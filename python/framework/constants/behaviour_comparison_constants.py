"""
The behaviour comparison's shared declarations (#641): which changed paths no backtest executes,
what a stand's scratch data home copies, and the names a comparison gives its scratch folder and
its scenario sets. Imports nothing, so every unit of the comparison and its tests can read it.
"""

# Path prefixes no backtest executes: a change confined to them ends a comparison at its first
# step. Held to what the shipped scenario sets and profiles load by a completeness test.
NOT_EXECUTED_PREFIXES = ('docs/', 'tests/', '.vscode/', '.github/')

# Below a not-executed prefix, the paths a run does execute: shipped scenario sets and profiles
# load their decision logics and workers from the test fixtures
EXECUTED_BELOW_NOT_EXECUTED = ('tests/fixtures/',)

# File suffixes no backtest executes, wherever they lie
NOT_EXECUTED_SUFFIXES = ('.md',)

# The entries of the processed-data directory a stand's home COPIES: the indexes and the caches a
# run may rewrite. Every other entry is archive data and is linked, so nothing is duplicated and
# nothing a run writes reaches the source.
STAND_HOME_COPIED_PROCESSED = ('bars_index.parquet', 'ticks_index.parquet', 'discovery_caches',
                               'signals')

# The scratch folder of one comparison, below the system's temporary directory
SCRATCH_DIR_PATTERN = 'finiex_behaviour_{comparison_id}'

# The scenario set name each stand's copy of the set carries, so its run is found by name
SCENARIO_SET_NAME_PATTERN = 'bc_{comparison_id}_{stand}'

# What the new stand's second run puts in that name's place, so its run is found apart from its
# first
SECOND_RUN_LABEL = 'new_again'

# The comparison's id: the instant it started, UTC
COMPARISON_ID_FORMAT = '%Y%m%d_%H%M%S'
