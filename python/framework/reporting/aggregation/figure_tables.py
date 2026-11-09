"""
Every fold table (#557) — the one catalog of how each served aggregate combines.

Each kind of row the reports fold has its table: the ledger's here beside the ones of the report
families, each declaring per figure its reduction, what its empty value means, where it is read and
how it is added. This index lists them all, so a test can hold every table to the fold, and so the
catalog can be served whole (#657, through #582) instead of being rebuilt by every reader.
"""

from typing import Dict

from python.framework.reporting.aggregation.tables.block_splitting_figure_table import (
    BLOCK_SYMBOL_TABLE,
    BLOCK_TOTALS_TABLE,
)
from python.framework.reporting.aggregation.tables.booking_figure_table import (
    BOOKING_RUN_TOTALS_TABLE,
    BOOKING_UNIT_CLOSE_TABLE,
)
from python.framework.reporting.aggregation.tables.deployment_figure_table import (
    DEPLOYMENT_CAPTION_TABLE,
    DEPLOYMENT_SUMMARY_TABLE,
)
from python.framework.reporting.aggregation.tables.execution_figure_table import (
    AGGREGATED_EXECUTION_TABLE,
    EXECUTION_TOTALS_TABLE,
)
from python.framework.reporting.aggregation.tables.portfolio_figure_table import (
    AGGREGATED_PORTFOLIO_TABLE,
    PENDING_TOTALS_TABLE,
    PORTFOLIO_HEADLINE_TABLE,
    SPOT_BALANCES_TABLE,
)
from python.framework.reporting.aggregation.tables.feed_stability_figure_table import (
    FEED_COUNTER_TABLE,
    FEED_SOURCE_TABLE,
    FEED_TOTALS_TABLE,
)
from python.framework.reporting.aggregation.tables.ledger_figure_table import LEDGER_TABLE
from python.framework.reporting.aggregation.tables.profiling_figure_table import (
    PROFILING_OPERATION_TABLE,
    PROFILING_TABLE,
)
from python.framework.reporting.aggregation.tables.robustness_figure_table import (
    ROBUSTNESS_DISTRIBUTION_TABLE,
    ROBUSTNESS_ROLE_TABLE,
)
from python.framework.reporting.aggregation.tables.run_list_figure_table import RUN_LIST_TABLE
from python.framework.reporting.aggregation.tables.run_summary_figure_table import (
    RUN_SPAN_TABLE,
    SIGNAL_CHANNEL_TABLE,
)
from python.framework.reporting.aggregation.tables.order_history_figure_table import (
    FAILED_ORDER_REASON_TABLE,
    FAILED_ORDERS_OF_UNIT_TABLE,
)
from python.framework.reporting.aggregation.tables.run_meta_figure_table import (
    RUN_THROUGHPUT_TABLE,
)
from python.framework.reporting.aggregation.tables.safety_figure_table import SAFETY_DAYS_TABLE
from python.framework.reporting.aggregation.tables.signal_figure_table import (
    FEED_PROVENANCE_TABLE,
    SIGNAL_COVERAGE_TABLE,
    SIGNAL_RESOLUTION_TABLE,
)
from python.framework.reporting.aggregation.tables.slippage_figure_table import SLIPPAGE_TABLE
from python.framework.reporting.aggregation.tables.sweep_figure_table import SWEEP_SUMMARY_TABLE
from python.framework.reporting.aggregation.tables.trade_figure_table import (
    TRADE_ANALYTICS_TABLE,
    TRADE_SCENARIO_TOTALS_TABLE,
    TRADE_SCENARIO_TOTALS_WITHOUT_QUOTE_TABLE,
)
from python.framework.reporting.aggregation.tables.worker_figure_table import (
    DECISION_LOGIC_MEANS_TABLE,
    DECISION_TOTALS_TABLE,
    WORKER_DECISION_BOTTLENECKS_TABLE,
    WORKER_TOTALS_TABLE,
)
from python.framework.types.aggregation_types import FoldTable
from python.framework.types.api.report_types import (
    BookingPeriodsReport,
    FeedStabilityReport,
    RunMetaReport,
    RunSummary,
    SafetyReport,
    SignalSourceRow,
)
from python.framework.types.trading_env_types.trading_env_stats_types import EXECUTION_COUNT_FIELDS

FIGURE_TABLES: Dict[str, FoldTable] = {table.name: table for table in (
    LEDGER_TABLE,
    DEPLOYMENT_SUMMARY_TABLE,
    DEPLOYMENT_CAPTION_TABLE,
    SWEEP_SUMMARY_TABLE,
    RUN_LIST_TABLE,
    EXECUTION_TOTALS_TABLE,
    WORKER_TOTALS_TABLE,
    DECISION_TOTALS_TABLE,
    DECISION_LOGIC_MEANS_TABLE,
    WORKER_DECISION_BOTTLENECKS_TABLE,
    SLIPPAGE_TABLE,
    TRADE_SCENARIO_TOTALS_TABLE,
    TRADE_SCENARIO_TOTALS_WITHOUT_QUOTE_TABLE,
    TRADE_ANALYTICS_TABLE,
    FAILED_ORDERS_OF_UNIT_TABLE,
    FAILED_ORDER_REASON_TABLE,
    PORTFOLIO_HEADLINE_TABLE,
    AGGREGATED_PORTFOLIO_TABLE,
    AGGREGATED_EXECUTION_TABLE,
    PENDING_TOTALS_TABLE,
    SPOT_BALANCES_TABLE,
    RUN_SPAN_TABLE,
    SIGNAL_CHANNEL_TABLE,
    PROFILING_TABLE,
    PROFILING_OPERATION_TABLE,
    RUN_THROUGHPUT_TABLE,
    SIGNAL_RESOLUTION_TABLE,
    SIGNAL_COVERAGE_TABLE,
    FEED_PROVENANCE_TABLE,
    BOOKING_RUN_TOTALS_TABLE,
    BOOKING_UNIT_CLOSE_TABLE,
    FEED_SOURCE_TABLE,
    FEED_COUNTER_TABLE,
    FEED_TOTALS_TABLE,
    ROBUSTNESS_DISTRIBUTION_TABLE,
    ROBUSTNESS_ROLE_TABLE,
    BLOCK_SYMBOL_TABLE,
    BLOCK_TOTALS_TABLE,
    SAFETY_DAYS_TABLE,
)}

# The served models only part tables fold, each with the fields none of its parts folds and why — the
# whole those parts are held to. A test holds each model's fields to its parts' figures plus these,
# both ways, so a field added to the model has to be folded or named here.
SERVED_UNFOLDED: Dict[type, Dict[str, str]] = {
    RunSummary: {
        'run_id': 'the identity of the run',
        'keys': "the list's declared row identity",
        'currencies': "a nested table: each currency's headline joined to its trade analytics",
        **{name: 'a copy of the execution totals' for name in EXECUTION_COUNT_FIELDS},
        'unit_count': "a copy: the portfolio's unit count",
        'units_declared': 'a copy of the roster',
        'units_disabled': 'a copy of the roster',
        'units_absent': 'a copy of the roster',
        'disturbance_episode_count': "a copy of the feed stability's totals",
        'disturbance_stale_seconds': "a copy of the feed stability's totals",
        'disturbance_source_count': "a copy of the feed stability's totals",
        'disturbance_stress_injected': "a copy of the feed stability's totals",
    },
    BookingPeriodsReport: {
        'run_id': 'the identity of the run',
        'keys': "the list's declared row identity",
        'periods': 'a listing: the periods themselves',
        'unit_totals': 'a nested table: each unit folded by the ledger table',
        'currency': 'the group: the currency the family reports',
        'currencies': "a listing of the run's currencies",
        'run_net_pnl': 'a copy of the run summary',
        'run_total_trades': 'a copy of the run summary',
        'reconciles': "a check: the periods' total against the run's own figure",
    },
    FeedStabilityReport: {
        'run_id': 'the identity of the run',
        'units': 'a nested table: the source rows, folded by the source table',
    },
    RunMetaReport: {
        **{name: "a copy of the batch's own measurement or identity"
           for name in ('run_id', 'scenario_count', 'disabled_count', 'symbols', 'is_profile_run',
                        'debug_execution', 'execution_time_s', 'warmup_time_s', 'tickrun_time_s',
                        'pickle_time_s', 'pickle_sample_mb', 'worker_tracking_on',
                        'profiling_tracking_on')},
        'parallel_scenarios': "a copy of the batch's parallelism configuration",
        'max_parallel_scenarios': "a copy of the batch's parallelism configuration",
        'generator_profile_count': "a count of the Profile Run's generator profiles",
        'generator_profile_modes': "a listing of the Profile Run's generator modes",
        **{name: 'a copy of the machine reading taken at the report'
           for name in ('machine_cpu_count', 'machine_memory_limit_bytes',
                        'machine_memory_used_bytes')},
        'loop_ticks': "a choice: the profiling's clipping total, else the summed algo ticks",
        'has_clipping': 'a comparison of the loop ticks with the summed algo ticks',
        **{name: 'a formula over the ticks and a time the run measured'
           for name in ('ticks_per_second', 'speedup', 'ticks_per_hour', 'avg_hours_per_unit')},
    },
    SafetyReport: {
        'run_id': 'the identity of the run',
        'symbol': 'the instrument the session traded',
        **{name: "a copy of the session's record: what the tick loop measured against its limits"
           for name in ('enabled', 'baseline', 'baseline_value', 'final_value',
                        'worst_drawdown_abs', 'worst_drawdown_abs_at', 'worst_drawdown_pct',
                        'worst_drawdown_pct_at', 'block_count', 'blocked_at_end',
                        'reason_at_end')},
        'baseline_restored': "a check of where the session's baseline came from",
        'soft_limit_used_pct': 'a formula over the worst drawdown and two configured limits',
        'hard_limit_used_pct': 'a formula over the worst drawdown and two configured limits',
        'limits': "a copy of the profile's circuit-breaker configuration",
        'days': 'a listing: one row per trading day',
        **{name: "a copy of the session's record of the emergency flatten"
           for name in ('flatten_fired', 'flatten_reason', 'flatten_outcome',
                        'flatten_unconfirmed', 'flatten_kept')},
    },
    SignalSourceRow: {
        'source': 'the group: the family folds one source at a time',
        'series_kind': 'a constant: archive or feed',
        'prompt_version': 'from the feed path only, where a live series states it',
        'usages': "a listing: one row per scenario's use of the source",
    },
}
