"""
The validation checks, declared once — what each `check` id a finding carries means to a reader.

A finding names the assertion that produced it by a stable id (`warmup_quality`), and that id is
what makes "which scenarios did warmup cost me" a filter instead of a text search. An id alone is
a label nobody outside this repository can read, so every check is declared here with a short
title and one sentence; `GET /api/v1/validation-checks` serves exactly this list.

Held by `tests/framework/batch_validations/test_validation_check_catalog.py`, in both directions:
every id the code emits is declared here, and every entry here is emitted somewhere — an entry for
a check that no longer exists would describe a finding nobody can receive.
"""

from typing import Dict, Tuple

from python.framework.types.validation_types import ValidationCheckInfo

VALIDATION_CHECKS: Tuple[ValidationCheckInfo, ...] = (
    # --- Configuration of a scenario ---------------------------------------------------------
    ValidationCheckInfo(
        'scenario_name_missing', 'Scenario without a name',
        'A scenario in the set has no name.'),
    ValidationCheckInfo(
        'scenario_name_duplicate', 'Duplicate scenario name',
        'Two or more scenarios in the set share a name; every copy is refused.'),
    ValidationCheckInfo(
        'scenario_boundary', 'No end to the scenario',
        'The scenario sets neither an end date nor a tick limit, so nothing says where it stops.'),
    ValidationCheckInfo(
        'date_logic', 'Invalid date range',
        "The scenario's end date is not after its start date."),
    ValidationCheckInfo(
        'balances_missing', 'No balances configured',
        'The scenario configures no starting balances.'),
    ValidationCheckInfo(
        'balance_currency_mismatch', "No balance in the symbol's currencies",
        "None of the configured balances is in the base or the quote currency of the symbol."),
    ValidationCheckInfo(
        'account_currency_invalid', 'Account currency not in the symbol',
        'The configured account currency is neither the base nor the quote currency of the '
        'symbol; settling in a third currency is not supported.'),
    ValidationCheckInfo(
        'account_currency_normalized', 'Account currency set to the quote',
        'At spot the account currency was changed to the quote currency, because spot settles '
        'in the quote currency and a base-currency profit would mix units.'),
    ValidationCheckInfo(
        'parameter_validation', 'Invalid parameter',
        'A decision logic or worker parameter is unknown, missing, or outside its declared range.'),
    ValidationCheckInfo(
        'requirements_aggregation', 'Data requirements could not be computed',
        'Working out which bars and how much warmup the workers need failed with an error.'),
    # --- The algorithm -----------------------------------------------------------------------
    ValidationCheckInfo(
        'worker_compat', 'Worker does not fit the market',
        "A worker needs an activity measure (such as traded volume) that the broker's market does "
        'not provide.'),
    ValidationCheckInfo(
        'worker_signal_subscription', 'Subscribed worker output does not exist',
        'The decision logic subscribes to a worker output that the worker does not produce.'),
    ValidationCheckInfo(
        'algo_state_snapshot', 'Algorithm state cannot be saved',
        'The decision logic keeps state across sessions, but its state cannot be written as JSON.'),
    ValidationCheckInfo(
        'algo_clock', 'Algorithm reads the system clock',
        "The decision logic or a worker reads the system clock instead of the run's own clock, "
        'which makes a backtest impossible to reproduce.'),
    ValidationCheckInfo(
        'market_fit', 'Algorithm not recommended for this market',
        'The decision logic names the markets or instruments it is meant for, and this one is '
        'not among them.'),
    # --- The broker --------------------------------------------------------------------------
    ValidationCheckInfo(
        'broker_config_unavailable', 'Broker configuration unavailable',
        "The broker's configuration could not be fetched or loaded."),
    ValidationCheckInfo(
        'symbol_unknown', 'Symbol not in the broker configuration',
        'The broker configuration does not list the symbol.'),
    ValidationCheckInfo(
        'swap_mode_unsupported', 'Swap mode not modelled',
        'The symbol charges overnight swap in a way the simulation does not model.'),
    # --- The data a scenario reads -----------------------------------------------------------
    ValidationCheckInfo(
        'coverage_report_missing', 'No data coverage report',
        "No coverage report exists for the broker and symbol, so the scenario's window cannot be "
        'checked against the data.'),
    ValidationCheckInfo(
        'data_availability', 'Data does not cover the window',
        "The scenario's window starts before the first tick or ends after the last one."),
    ValidationCheckInfo(
        'start_date_in_gap', 'Start inside a data gap',
        'The scenario starts inside a gap in the tick data.'),
    ValidationCheckInfo(
        'tick_stretch_gap', 'Gap in the tick data',
        "The tick data has a gap inside the scenario's window that is longer than allowed."),
    ValidationCheckInfo(
        'warmup_quality', 'Not enough warmup bars',
        "A timeframe has fewer bars before the scenario's start than its indicators need to "
        'settle.'),
    ValidationCheckInfo(
        'tick_data_missing', 'No tick data loaded',
        'No tick data could be loaded for the scenario.'),
    ValidationCheckInfo(
        'data_origin', 'Data origin not admitted',
        'The scenario read data files whose origin is not on the admitted list.'),
    ValidationCheckInfo(
        'signal_data_load', 'Signal data unavailable',
        'The signal data the scenario binds could not be loaded.'),
    ValidationCheckInfo(
        'signal_availability', 'Signal data thin in the window',
        "The scenario's signal source has little or patchy data in its window; the scenario "
        'still runs.'),
    ValidationCheckInfo(
        'signal_stretch_gap', 'Gap in the signal data',
        "The signal data has a gap inside the scenario's window."),
    ValidationCheckInfo(
        'stale_data_stress_source', 'Stress test names a source the scenario lacks',
        'A planned data outage names a data source the scenario does not read.'),
    ValidationCheckInfo(
        'scenario_execution', 'Refused before it ran',
        'The scenario was refused right before it would have started, for the reason stated.'),
    # --- Advisories on the run as a whole ----------------------------------------------------
    ValidationCheckInfo(
        'debug_mode', 'Debug mode',
        'The run executed serially under a debugger, so its timings are not representative.'),
    ValidationCheckInfo(
        'stress_test', 'Stress test active',
        'At least one unit ran with a stress test that injects errors and rejections on purpose.'),
    ValidationCheckInfo(
        'uncommitted_code', 'Real orders from uncommitted code',
        'A session trading real orders was started with --allow-dirty from code that is not '
        'exactly one commit.'),
    ValidationCheckInfo(
        'unversioned_code', 'Code not under version control',
        'A component was loaded from a directory that git does not track or cannot read, so the '
        'run cannot name the code it ran.'),
    ValidationCheckInfo(
        'data_version_unknown', 'Data format version unknown',
        'Some data files the run read carry no format version in the tick index.'),
    ValidationCheckInfo(
        'data_origin_unstamped', 'Data origin not stamped',
        'Some data files the run read carry no origin stamped by their producer, so they are '
        'usable but cannot enter a parity comparison.'),
    ValidationCheckInfo(
        'spreadless_tick_data', 'Ticks without a spread',
        'Some tick data the run read has no bid/ask spread, so trades crossed no spread.'),
    ValidationCheckInfo(
        'multi_currency', 'Several account currencies',
        'The run mixes account currencies; profit and loss is shown per currency and never added '
        'across them.'),
    ValidationCheckInfo(
        'time_divergence', 'Scenarios far apart in time',
        'The scenarios of one currency span many days, so their combined profit and loss is a '
        'statistic, not a portfolio.'),
    # --- Performance and tick processing -----------------------------------------------------
    ValidationCheckInfo(
        'budget', 'Processing slower than the ticks arrive',
        'In some scenarios a tick takes longer to process, on average, than the shortest gaps '
        'between ticks; a tick processing budget would simulate the ticks a live run would skip.'),
    ValidationCheckInfo(
        'budget_granularity', 'Budget below data resolution',
        'The tick processing budget is below one millisecond, which the data cannot resolve, so '
        'it has no effect.'),
    ValidationCheckInfo(
        'budget_too_high', 'Budget skips ticks needlessly',
        'The tick processing budget is more than twice the usual processing time, so ticks are '
        'skipped that a live run would have processed.'),
    ValidationCheckInfo(
        'clipping', 'Ticks arrived while the last was processing',
        'More ticks than the configured share arrived while the previous one was still being '
        'processed, so those decisions ran on data that was already old.'),
    ValidationCheckInfo(
        'coordination_overhead', 'High coordination overhead',
        'Coordinating the workers and the decision logic costs more than half of their own '
        'computation in some scenarios.'),
    ValidationCheckInfo(
        'bottleneck', 'Infrastructure dominates the cost',
        'An operation outside the trading logic is the largest cost in many scenarios.'),
    ValidationCheckInfo(
        'parallel_penalty', 'Parallel workers cost time',
        'Running the workers in parallel was slower than running them one after another.'),
    # --- Robustness --------------------------------------------------------------------------
    ValidationCheckInfo(
        'robustness_param_drift', 'Parameters differ across windows',
        'The strategy parameters are not the same in every window, so comparing in-sample with '
        'out-of-sample is not a fair test.'),
    ValidationCheckInfo(
        'robustness_low_windows', 'Too few windows',
        'The robustness figures rest on fewer windows than the configured minimum.'),
    ValidationCheckInfo(
        'robustness_low_trust', 'Block splitting distorts the windows',
        'Splitting the data into blocks distorted the windows beyond the trust limit, so no '
        'verdict is given.'),
    ValidationCheckInfo(
        'robustness_insufficient_buckets', 'Too few in- or out-of-sample windows',
        'The in-sample or the out-of-sample group has fewer windows than the minimum, so no '
        'verdict is given.'),
    ValidationCheckInfo(
        'robustness_overfit', 'Overfit',
        'Out-of-sample results fall sharply below in-sample results: the strategy is likely fitted '
        'to the in-sample windows.'),
    # --- Dated declarations the installation holds -------------------------------------------
    ValidationCheckInfo(
        'certificate_expired', 'Release certificate expired',
        'The newest certificate of a release gate is past its validity date, so the gate has to '
        'be run again before a release.'),
    ValidationCheckInfo(
        'fee_structure_frozen_long', 'Fee structure frozen long ago',
        "A broker's declared fee rates were frozen more than ninety days ago; the venue may "
        'charge this account differently by now.'),
)

# Each check by its id.
VALIDATION_CHECKS_BY_ID: Dict[str, ValidationCheckInfo] = {
    info.check: info for info in VALIDATION_CHECKS}
