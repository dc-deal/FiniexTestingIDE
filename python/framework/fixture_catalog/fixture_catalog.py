"""
The fixture catalog (#576) — every run a consumer pins, as a committed declaration.

Each entry says what produces it, what its runs must carry, and who relies on it. `produce` makes
an entry's runs and checks them; the newest production that carried every property is the entry's
CURRENT fixture, derived from the production record and never written into a run.

The properties are the ones the consumers assert. FiniexViewer replays its tests from the
report-coverage run and the demo deployment; #557 freezes its golden samples from the backtest,
the mock sessions, the deployment and the sweep — both pipelines, both account models.
"""

from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from python.framework.types.api.report_types import (
    CloseType,
    DeploymentDetailResponse,
    OrderStatus,
    OrdersTo,
    ParentKind,
)
from python.framework.types.fixture_catalog_types import (
    FixtureEntry,
    FixtureEvidence,
    FixtureProducerKind,
    FixtureProperty,
    FixtureSession,
)
from python.framework.types.run_outcome_types import RunOutcome

_FINIEX_VIEWER = 'FiniexViewer'
_GOLDEN_SAMPLES = '#557 golden samples'


# ============================================
# Checks over a production's runs
# ============================================

def _ends_failed(evidence: FixtureEvidence) -> bool:
    """
    A run of the production ended `failed`.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    return any(run.run_outcome == RunOutcome.FAILED for run in evidence.runs)


def _absent_scenario_with_error(evidence: FixtureEvidence) -> bool:
    """
    A scenario was refused, and the error pot names it.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    return any(summary.units_absent for summary in evidence.run_summary.values()) and any(
        report.errors for report in evidence.warnings_errors.values())


def _several_currencies(evidence: FixtureEvidence) -> bool:
    """
    A run's units are accounted in more than one currency.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    return any(len({row.currency for row in summary.currencies}) > 1
               for summary in evidence.run_summary.values())


def _every_order_status(evidence: FixtureEvidence) -> bool:
    """
    The order history holds denied, rejected, executed and pending orders.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    wanted = {OrderStatus.DENIED, OrderStatus.REJECTED, OrderStatus.EXECUTED, OrderStatus.PENDING}
    return any(wanted <= {row.status for row in report.orders}
               for report in evidence.order_history.values())


def _two_trades(evidence: FixtureEvidence) -> bool:
    """
    Two trades or more were closed.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    return sum(report.count for report in evidence.trade_history.values()) >= 2


def _a_trade(evidence: FixtureEvidence) -> bool:
    """
    At least one trade was closed.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    return sum(report.count for report in evidence.trade_history.values()) >= 1


def _partial_close(evidence: FixtureEvidence) -> bool:
    """
    A position was closed in parts.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    return any(trade.close_type == CloseType.PARTIAL
               for report in evidence.trade_history.values() for trade in report.trades)


def _left_open(evidence: FixtureEvidence) -> bool:
    """
    A position was still open when the run ended.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    return any(unit.open_positions
               for report in evidence.portfolio.values() for unit in report.units)


def _position_id_reused(evidence: FixtureEvidence) -> bool:
    """
    One position id appears in the order history of three scenarios or more, and only some of
    them have a trade under it — the case where a row's link to its trade is absent.

    "Traded" means a CLOSED trade in the trade history: a position opened and never closed has
    order rows and no trade. A refused or resting order carries no position id at all, so it
    cannot be what makes a scenario untraded (measured 2026-10-08 on this set).

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    for run_id, report in evidence.order_history.items():
        ordered = defaultdict(set)
        for row in report.orders:
            if row.position_id is not None:
                ordered[row.position_id].add(row.scenario_name)
        traded = defaultdict(set)
        trades = evidence.trade_history.get(run_id)
        for trade in trades.trades if trades is not None else []:
            traded[trade.position_id].add(trade.scenario_name)
        if any(len(names) >= 3 and 0 < len(traded[position] & names) < len(names)
               for position, names in ordered.items()):
            return True
    return False


def _both_account_models(evidence: FixtureEvidence) -> bool:
    """
    The run's units trade a spot account and a margin account.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    return any({unit.spot_mode for unit in report.units} == {True, False}
               for report in evidence.portfolio.values())


def _spot(evidence: FixtureEvidence) -> bool:
    """
    Every unit trades a spot account.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    units = [unit for report in evidence.portfolio.values() for unit in report.units]
    return bool(units) and all(unit.spot_mode for unit in units)


def _margin(evidence: FixtureEvidence) -> bool:
    """
    Every unit trades a margin account.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    units = [unit for report in evidence.portfolio.values() for unit in report.units]
    return bool(units) and not any(unit.spot_mode for unit in units)


def _reported(evidence: FixtureEvidence) -> bool:
    """
    Every run reached its report.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    return bool(evidence.runs) and all(run.artifacts for run in evidence.runs)


def _orders_simulated(evidence: FixtureEvidence) -> bool:
    """
    No order went to a venue.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    return bool(evidence.runs) and all(run.orders_to == OrdersTo.SIMULATED for run in evidence.runs)


# ============================================
# Checks over a production's deployments
# ============================================

def _main_history(evidence: FixtureEvidence) -> Optional[DeploymentDetailResponse]:
    """
    The production's longest deployment history — the one its changes are declared on.

    Args:
        evidence: The production's evidence

    Returns:
        That history, or None when the production made none
    """
    if not evidence.deployments:
        return None
    return max(evidence.deployments.values(), key=lambda detail: detail.count)


def _sessions(evidence: FixtureEvidence) -> bool:
    """
    The main history holds two sessions or more.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    history = _main_history(evidence)
    return history is not None and history.count >= 2


def _advisory(evidence: FixtureEvidence) -> bool:
    """
    The main history carries a comparability advisory.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    history = _main_history(evidence)
    return history is not None and history.advisory is not None


def _strategy_changed(evidence: FixtureEvidence) -> bool:
    """
    A session of the main history changed the strategy.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    history = _main_history(evidence)
    return history is not None and any(session.strategy_changed for session in history.sessions)


def _operation_changed(evidence: FixtureEvidence) -> bool:
    """
    A session of the main history changed only the operation.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    history = _main_history(evidence)
    return history is not None and any(session.operation_changed for session in history.sessions)


def _several_booking_periods(evidence: FixtureEvidence) -> bool:
    """
    The main history booked more than one period, opened at more than one moment.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    history = _main_history(evidence)
    if history is None or history.deployment_id not in evidence.deployment_periods:
        return False
    periods = evidence.deployment_periods[history.deployment_id].periods
    return len(periods) > 1 and len({period.opened_at for period in periods}) > 1


def _unfinished_session(evidence: FixtureEvidence) -> bool:
    """
    A session of the main history never reached its close.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    history = _main_history(evidence)
    return history is not None and history.unfinished >= 1


def _two_histories_one_bot(evidence: FixtureEvidence) -> bool:
    """
    The same bot has two histories.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    bots = [row.bot_id for row in evidence.deployment_rows]
    return any(bots.count(bot) >= 2 for bot in set(bots))


def _sessions_keyed_by_run(evidence: FixtureEvidence) -> bool:
    """
    A history's session list is keyed by run id.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    history = _main_history(evidence)
    return history is not None and 'run_id' in history.key


# ============================================
# Checks over a production's sweeps
# ============================================

def _several_combinations(evidence: FixtureEvidence) -> bool:
    """
    The sweep ran two combinations or more.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    return any(detail.count >= 2 for detail in evidence.sweeps.values())


def _combinations_are_its_runs(evidence: FixtureEvidence) -> bool:
    """
    Every run of the production is a combination of its sweep.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    return bool(evidence.runs) and all(run.parent_kind == ParentKind.SWEEP for run in evidence.runs)


def _ranked_by_its_objective(evidence: FixtureEvidence) -> bool:
    """
    The sweep names the objective its combinations are ranked by.

    Args:
        evidence: What the production made, as the API serves it

    Returns:
        Whether the production carries this property
    """
    return bool(evidence.sweeps) and all(detail.objective for detail in evidence.sweeps.values())


# ============================================
# The entries
# ============================================

# The demo deployment's sessions, one replay day each. Sessions 1 and 2 share one
# configuration, 3 changes the strategy, 4 only the operation, 5 is killed before its close, and
# 6 starts a second history for the same bot.
_RSI = 'strategy_config.decision_logic_config.rsi_oversold'
_DRAWDOWN = 'safety.max_drawdown_pct'
_SAFETY = 'safety.enabled'
_DEMO_SESSIONS: Tuple[FixtureSession, ...] = (
    FixtureSession('s1 · a fresh history', '2026-02-01T18:00:00+00:00',
                   {_RSI: 40, _SAFETY: True, _DRAWDOWN: 25.0}, new_deployment=True),
    FixtureSession('s2 · the same configuration', '2026-02-02T18:00:00+00:00',
                   {_RSI: 40, _SAFETY: True, _DRAWDOWN: 25.0}),
    FixtureSession('s3 · the STRATEGY changed', '2026-02-03T18:00:00+00:00',
                   {_RSI: 35, _SAFETY: True, _DRAWDOWN: 25.0}),
    FixtureSession('s4 · the OPERATION changed', '2026-02-04T18:00:00+00:00',
                   {_RSI: 35, _SAFETY: True, _DRAWDOWN: 12.0}),
    # Killed at its first order rather than after a fixed time: a timer cannot tell where a
    # session is. Measured 2026-10-10 on the 25 s timer it replaced: the header comes at the start,
    # the tick loop 17.7 s later and the first order 1.2 s after that — the kill landed seconds
    # after it, and on a loaded machine it would have landed before the header (at 8 s it did).
    FixtureSession('s5 · killed before its close', '2026-02-05T18:00:00+00:00',
                   {_RSI: 35, _SAFETY: True, _DRAWDOWN: 12.0}, killed_before_close=True),
    FixtureSession('s6 · a second history for the same bot', '2026-02-06T18:00:00+00:00',
                   {_RSI: 35, _SAFETY: True, _DRAWDOWN: 12.0}, new_deployment=True),
)

FIXTURE_CATALOG: Tuple[FixtureEntry, ...] = (
    FixtureEntry(
        entry_id='report_coverage',
        title='One backtest carrying every state the reports can show — refusals, a failed '
              'scenario, two currencies, both account models, a partial close, an open position',
        producer=FixtureProducerKind.SCENARIO_SET,
        source='configs/scenario_sets/backtesting/report_coverage_reference.json',
        consumers=(_FINIEX_VIEWER, _GOLDEN_SAMPLES),
        properties=(
            FixtureProperty('ends_failed', 'The run ends failed — one scenario is refused by '
                            'design', _ends_failed),
            FixtureProperty('absent_scenario', 'A scenario is absent, and the error pot names '
                            'it', _absent_scenario_with_error),
            FixtureProperty('several_currencies', 'Its units are accounted in more than one '
                            'currency', _several_currencies),
            FixtureProperty('both_account_models', 'Its units trade a spot and a margin '
                            'account', _both_account_models),
            FixtureProperty('order_statuses', 'Its order history holds denied, rejected, '
                            'executed and pending orders', _every_order_status),
            FixtureProperty('two_trades', 'It closes two trades or more', _two_trades),
            FixtureProperty('partial_close', 'A position is closed in parts', _partial_close),
            FixtureProperty('left_open', 'A position is still open at the end', _left_open),
            FixtureProperty('position_id_reused', 'One position id appears in three scenarios '
                            'or more, traded in only some', _position_id_reused),
        ),
    ),
    FixtureEntry(
        entry_id='demo_deployment',
        title='One bot across several restarts — a strategy change, an operation change, a '
              'session killed before its close, and a second history',
        producer=FixtureProducerKind.SESSION_SEQUENCE,
        source='configs/autotrader_profiles/mock/trade_lifecycle_test.json',
        consumers=(_FINIEX_VIEWER, _GOLDEN_SAMPLES),
        sessions=_DEMO_SESSIONS,
        session_profile_name='demo_btcusd_bot',
        # A declared identity is at most 10 characters of a-z, 0-9 and '-'.
        session_bot_id='demo-btc',
        # Long enough to cross the replay's midnight, so a session books more than its closing
        # period: the data of this era carries ~3,600 ticks an hour (measured 2026-09-23).
        session_max_ticks=90_000,
        properties=(
            FixtureProperty('sessions', 'Its main history holds two sessions or more',
                            _sessions),
            FixtureProperty('advisory', 'The main history carries a comparability advisory',
                            _advisory),
            FixtureProperty('strategy_changed', 'A session changes the strategy',
                            _strategy_changed),
            FixtureProperty('operation_changed', 'A session changes only the operation',
                            _operation_changed),
            FixtureProperty('booking_periods', 'More than one booking period, opened at more '
                            'than one moment', _several_booking_periods),
            FixtureProperty('unfinished', 'A session never reaches its close',
                            _unfinished_session),
            FixtureProperty('two_histories', 'The same bot has two histories',
                            _two_histories_one_bot),
            FixtureProperty('sessions_key', 'A history lists its sessions keyed by run id',
                            _sessions_keyed_by_run),
        ),
    ),
    FixtureEntry(
        entry_id='mock_session_spot',
        title='One AutoTrader mock session on a spot account (Kraken BTCUSD), its position '
              'closed by its take-profit',
        producer=FixtureProducerKind.PROFILE,
        # A probe opens the position, so the session trades whatever the market does — a
        # strategy-driven profile over its default window closed no trade (measured 2026-10-08).
        source='configs/autotrader_profiles/mock/tp_triggered_test.json',
        consumers=(_GOLDEN_SAMPLES,),
        properties=(
            FixtureProperty('reported', 'The session reaches its report', _reported),
            FixtureProperty('a_trade', 'It closes a trade', _a_trade),
            FixtureProperty('spot', 'It trades a spot account', _spot),
            FixtureProperty('simulated', 'No order goes to a venue', _orders_simulated),
        ),
    ),
    FixtureEntry(
        entry_id='mock_session_margin',
        title='One AutoTrader mock session on a margin account (MT5 USDJPY), closing a position '
              'in parts',
        producer=FixtureProducerKind.PROFILE,
        source='configs/autotrader_profiles/mock/partial_close_lifecycle.json',
        consumers=(_GOLDEN_SAMPLES,),
        properties=(
            FixtureProperty('reported', 'The session reaches its report', _reported),
            FixtureProperty('a_trade', 'It closes a trade', _a_trade),
            FixtureProperty('partial_close', 'A position is closed in parts', _partial_close),
            FixtureProperty('margin', 'It trades a margin account', _margin),
            FixtureProperty('simulated', 'No order goes to a venue', _orders_simulated),
        ),
    ),
    FixtureEntry(
        entry_id='sweep',
        title='A small parameter sweep — every combination an ordinary backtest of its parent',
        producer=FixtureProducerKind.SWEEP,
        source='configs/sweeps/trend_channel_reference_grid.json',
        consumers=(_GOLDEN_SAMPLES,),
        properties=(
            FixtureProperty('combinations', 'The sweep runs two combinations or more',
                            _several_combinations),
            FixtureProperty('parented', 'Every run is a combination of the sweep',
                            _combinations_are_its_runs),
            FixtureProperty('objective', 'The sweep names the objective it ranks by',
                            _ranked_by_its_objective),
        ),
    ),
)

FIXTURE_CATALOG_BY_ID: Dict[str, FixtureEntry] = {entry.entry_id: entry
                                                  for entry in FIXTURE_CATALOG}


def entry_ids() -> List[str]:
    """
    Every entry's identity, in catalog order.

    Returns:
        The ids
    """
    return [entry.entry_id for entry in FIXTURE_CATALOG]


# Checked on every entry rather than declared on each. A production finds its runs by name, so a
# run of the same name that somebody starts elsewhere while it runs would be counted as its own;
# the count is what notices. A sweep is exempt — its runs are found by the sweep they belong to.
DECLARED_RUN_COUNT = 'declared_run_count'
DECLARED_RUN_COUNT_SENTENCE = ('The production made exactly the runs its entry declares — no run '
                               'of the same name, started elsewhere while it ran, counted as its '
                               'own')


def declared_run_count(entry: FixtureEntry) -> Optional[int]:
    """
    How many runs one production of an entry makes.

    Args:
        entry: The catalog entry

    Returns:
        One for a scenario set or a profile, one per session for a sequence — a killed session
        leaves its run too; None for a sweep, whose runs are not found by name
    """
    if entry.producer == FixtureProducerKind.SWEEP:
        return None
    if entry.producer == FixtureProducerKind.SESSION_SEQUENCE:
        return len(entry.sessions)
    return 1
