"""
The names an A/A of two code stands (#512) shares between its writer and its readers: what its
record holds and how its scenario sets are named.

An A/A backtests one window twice with the same inputs — once on the release the sessions ran,
once on the tree the verdict's backtests run on — and compares what the two computed, event by
event. Its record lies in the divergence store beside the comparisons.
"""

# What one A/A's directory holds besides its scenario sets
AA_REPORT_FILE = 'aa_report.json'
AA_REPORT_TEXT_FILE = 'aa_report.txt'

# An A/A's scenario sets are named after it, one per window and code stand, so each backtest is
# found by its set's name; each set's file carries the same name
AA_SCENARIO_SET_NAME = 'aa_{report_id}_window_{window}_{stand}'
# The scenario's name inside both sets of a window — the two runs are compared scenario by scenario
AA_SCENARIO_NAME = 'window_{window}'

# How many differing values a window's result lists; the count covers all of them
AA_DIFFERENCES_SHOWN = 20
