"""
Trade-History Report Builder Tests (#391).

The postprocessor is a pure function (RunUnits → TradeHistoryReport), so it is tested
in isolation with hand-built fixture trade records wrapped in a single RunUnit — no
simulation or live run required. Covers mapping, the filter path (symbol / close
reason / time range), distinct symbols, and the empty case.
"""

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from python.framework.reporting.builders.run_unit import RunUnit, run_units_from_batch
from python.framework.reporting.builders.trade_history_report_builder import (
    build_trade_history_report,
)
from python.framework.reporting.io.report_filters import filter_trade_history_report
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.data_deficiency_types import DataDeficiency
from python.framework.types.fill_slippage_types import SlippageReference
from python.framework.types.portfolio_types.portfolio_trade_record_types import (
    CloseReason,
    CloseType,
    EntryType,
    TradeRecord,
)
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderSide
from python.framework.types.trading_env_types.submission_metadata_types import SubmissionMetadata

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'

_T0 = datetime(2025, 10, 13, 8, 0, 0, tzinfo=timezone.utc)


def _units(trades):
    """Wrap a flat trade list in a single RunUnit (the builder consumes units)."""
    return [RunUnit(name='', symbol='', trade_history=trades)]


def _trade(
    position_id: str = 'p1',
    symbol: str = 'EURUSD',
    direction: OrderDirection = OrderDirection.LONG,
    net_pnl: float = 10.0,
    close_reason: CloseReason = CloseReason.TP_TRIGGERED,
    entry_offset_min: int = 0,
    duration_min: int = 30,
    initial_risk: float | None = None,
    mae_price: float = 0.0,
    mfe_price: float = 0.0,
    mae_pnl: float = 0.0,
    mfe_pnl: float = 0.0,
    pip_size: float = 0.0001,
    price_unit: str = 'pip',
) -> TradeRecord:
    """Build a fixture closed trade with sensible defaults for the audit fields."""
    entry_time = _T0 + timedelta(minutes=entry_offset_min)
    exit_time = entry_time + timedelta(minutes=duration_min)
    return TradeRecord(
        position_id=position_id, symbol=symbol, direction=direction, lots=0.1,
        close_type=CloseType.FULL,
        entry_price=1.1000, entry_time=entry_time,
        entry_tick_value=1.0, entry_bid=1.0999, entry_ask=1.1001,
        exit_price=1.1020, exit_time=exit_time, exit_tick_value=1.0,
        entry_tick_index=0, exit_tick_index=100,
        digits=5, contract_size=100000,
        spread_cost=0.5, commission_cost=0.3, swap_cost=0.0, total_fees=0.8,
        gross_pnl=net_pnl + 0.8, net_pnl=net_pnl,
        initial_risk=initial_risk,
        mae_price=mae_price, mfe_price=mfe_price, mae_pnl=mae_pnl, mfe_pnl=mfe_pnl,
        close_reason=close_reason, entry_type=EntryType.MARKET,
        pip_size=pip_size, price_unit=price_unit,
    )


class TestMapping:
    """TradeRecord → renderable row."""

    def test_builds_rows(self):
        report = build_trade_history_report(_RUN_ID, _units([_trade(), _trade(position_id='p2')]))
        assert report.count == 2
        assert len(report.trades) == 2

    def test_row_fields_mapped(self):
        report = build_trade_history_report(_RUN_ID, _units([_trade(net_pnl=12.5, duration_min=15)]))
        row = report.trades[0]
        assert row.direction == 'long'                 # enum → value
        assert row.close_reason == 'tp_triggered'
        assert row.net_pnl == 12.5
        assert row.duration_s == 15 * 60               # exit - entry
        assert row.entry_time.endswith('+00:00')       # ISO-8601 UTC

    def test_short_direction(self):
        report = build_trade_history_report(_RUN_ID, _units([_trade(direction=OrderDirection.SHORT)]))
        assert report.trades[0].direction == 'short'


class TestFilters:
    """One shared filter path for console / CSV / API."""

    def _mixed(self):
        return [
            _trade(position_id='p1', symbol='EURUSD', close_reason=CloseReason.TP_TRIGGERED, entry_offset_min=0),
            _trade(position_id='p2', symbol='GBPUSD', close_reason=CloseReason.SL_TRIGGERED, entry_offset_min=60),
            _trade(position_id='p3', symbol='EURUSD', close_reason=CloseReason.SL_TRIGGERED, entry_offset_min=120),
        ]

    def test_filter_by_symbol(self):
        report = build_trade_history_report(_RUN_ID, _units(self._mixed()), symbol='GBPUSD')
        assert report.count == 1
        assert report.trades[0].position_id == 'p2'

    def test_filter_by_close_reason(self):
        report = build_trade_history_report(_RUN_ID, _units(self._mixed()), close_reason='sl_triggered')
        assert report.count == 2
        assert {r.position_id for r in report.trades} == {'p2', 'p3'}

    def test_filter_by_time_range(self):
        report = build_trade_history_report(_RUN_ID, 
            _units(self._mixed()), start=_T0 + timedelta(minutes=30), end=_T0 + timedelta(minutes=90))
        assert report.count == 1
        assert report.trades[0].position_id == 'p2'


class TestMetadata:
    """Distinct symbols + empty case."""

    def test_distinct_symbols_sorted(self):
        report = build_trade_history_report(_RUN_ID, _units([
            _trade(symbol='GBPUSD'), _trade(symbol='EURUSD'), _trade(symbol='EURUSD')]))
        assert report.symbols == ['EURUSD', 'GBPUSD']

    def test_empty(self):
        report = build_trade_history_report(_RUN_ID, _units([]))
        assert report.count == 0
        assert report.trades == []
        assert report.symbols == []

    def test_scenario_totals(self):
        # per-scenario footer totals (the table footer, model-served)
        report = build_trade_history_report(_RUN_ID, 
            _units([_trade(net_pnl=20.0), _trade(position_id='p2', net_pnl=-10.0)]))
        assert len(report.scenario_totals) == 1
        st = report.scenario_totals[0]
        assert st.trade_count == 2
        assert round(st.net_pnl, 2) == 10.0        # 20 - 10
        assert round(st.total_fees, 2) == 1.6      # 0.8 × 2


class TestAnalytics:

    def test_no_trade_with_an_r_leaves_expectancy_empty(self):
        """A logic keeping its own stop: the position carried none, so nothing was measured."""
        a = build_trade_history_report(_RUN_ID, _units([_trade(initial_risk=None)])).analytics[0]

        assert a.expectancy is None and a.r_trade_count == 0 and a.trade_count == 1
    """#389 — R-multiple + pips per row, and the aggregate analytics block."""

    def test_r_multiple(self):
        # net_pnl 20 / initial_risk 10 → R = 2.0
        report = build_trade_history_report(_RUN_ID, _units([_trade(net_pnl=20.0, initial_risk=10.0)]))
        assert report.trades[0].r_multiple == 2.0

    def test_r_multiple_none_without_sl(self):
        # no initial_risk (trade had no stop loss) → R undefined
        assert build_trade_history_report(_RUN_ID, _units([_trade()])).trades[0].r_multiple is None

    def test_distance_forex_pip_unit(self):
        # Forex: authoritative pip_size 1e-4 stamped at source → distance in pips (#167)
        row = build_trade_history_report(_RUN_ID, 
            _units([_trade(mae_price=1.0990, mfe_price=1.1030)])).trades[0]
        assert round(row.mae_distance, 1) == 10.0   # |1.1000 - 1.0990| / 1e-4
        assert round(row.mfe_distance, 1) == 30.0   # |1.1030 - 1.1000| / 1e-4
        assert row.price_unit == 'pip'

    def test_distance_crypto_tick_unit(self):
        # Crypto: pip_size = tick (0.1 for BTCUSD) → distance in ticks, labeled 'tick' (#167)
        row = build_trade_history_report(_RUN_ID, _units([_trade(
            symbol='BTCUSD', mae_price=1.0980, mfe_price=1.1050,
            pip_size=0.1, price_unit='tick')])).trades[0]
        assert round(row.mae_distance, 2) == 0.02   # |1.1000 - 1.0980| / 0.1
        assert round(row.mfe_distance, 2) == 0.05   # |1.1050 - 1.1000| / 0.1
        assert row.price_unit == 'tick'

    def test_aggregate(self):
        trades = [
            _trade(position_id='w1', net_pnl=20.0, initial_risk=10.0, mae_pnl=-3.0),  # R=2
            _trade(position_id='w2', net_pnl=20.0, initial_risk=10.0, mae_pnl=-5.0),  # R=2
            _trade(position_id='l1', net_pnl=-10.0, initial_risk=10.0, mae_pnl=-12.0, mfe_pnl=4.0),  # R=-1
            _trade(position_id='l2', net_pnl=-10.0, initial_risk=10.0, mae_pnl=-8.0, mfe_pnl=6.0),   # R=-1
        ]
        a = build_trade_history_report(_RUN_ID, _units(trades)).analytics[0]   # single currency ('')
        assert a.r_trade_count == 4
        assert a.trade_count == 4
        assert a.expectancy == 0.5                 # (2+2-1-1)/4
        assert a.avg_win_r == 2.0
        assert a.avg_loss_r == -1.0
        assert a.avg_mae_winners == -4.0           # (-3 + -5)/2
        assert a.avg_mae_losers == -10.0           # (-12 + -8)/2
        assert a.avg_mfe_losers == 5.0             # (4 + 6)/2
        # per-currency P&L totals (#393 — the trade-table TOTAL, model-served)
        assert round(a.net_pnl, 2) == 20.0         # 20 + 20 - 10 - 10
        assert round(a.total_fees, 2) == 3.2       # 0.8 × 4
        assert round(a.gross_pnl, 2) == 23.2       # net + fees

    def test_r_subsets_carry_their_own_counts(self):
        """r_trade_count cannot gate the averages — a run can have R-trades and no winner."""
        trades = [
            _trade(position_id='w1', net_pnl=20.0, initial_risk=10.0),   # R=2
            _trade(position_id='l1', net_pnl=-10.0, initial_risk=10.0),  # R=-1
            _trade(position_id='l2', net_pnl=-10.0, initial_risk=10.0),  # R=-1
        ]
        a = build_trade_history_report(_RUN_ID, _units(trades)).analytics[0]
        assert (a.r_trade_count, a.r_win_count, a.r_loss_count) == (3, 1, 2)

    def test_no_r_winner_leaves_avg_win_r_unmeasured(self):
        """The archive case: one R-trade, and it lost. 0.0 would read as a measured zero."""
        trades = [_trade(position_id='l1', net_pnl=-10.0, initial_risk=10.0)]   # R=-1
        a = build_trade_history_report(_RUN_ID, _units(trades)).analytics[0]
        assert a.r_trade_count == 1 and a.r_win_count == 0
        assert a.avg_win_r is None
        assert a.avg_loss_r == -1.0

    def test_no_r_loser_leaves_avg_loss_r_unmeasured(self):
        trades = [_trade(position_id='w1', net_pnl=20.0, initial_risk=10.0)]    # R=2
        a = build_trade_history_report(_RUN_ID, _units(trades)).analytics[0]
        assert a.avg_loss_r is None and a.r_loss_count == 0
        assert a.avg_win_r == 2.0

    def test_empty_analytics(self):
        assert build_trade_history_report(_RUN_ID, _units([])).analytics == []   # no rows → no currency groups


def _fill(trade_id: str) -> BrokerTrade:
    """One execution with a fixed id — the only field the count reads."""
    return BrokerTrade(
        trade_id=trade_id, parent_broker_ref='ref', order_id='pos_eurusd_1', volume=0.3,
        price=1.1, fee=0.0, fee_currency='USD', timestamp=_T0, side=OrderSide.BUY,
        is_maker=False)


class TestASharedFillIsCounted:
    """
    A partial close copies the position's entry fills onto every record it produces, so several
    rows show one fill. The count is stamped on the execution by the builder — over the whole
    unit, and per unit, because two scenarios on one symbol mint the same synthetic ids.
    """

    @staticmethod
    def _with_entry(trade: TradeRecord, trade_id: str) -> TradeRecord:
        exit_id = f'x-{trade.position_id}-{trade.exit_time.minute}'
        return replace(trade, entry_trades=[_fill(trade_id)], exit_trades=[_fill(exit_id)])

    def test_the_rows_of_one_fill_say_how_many_share_it(self):
        first = self._with_entry(_trade('p1', duration_min=10), 'SYNTH-pos_eurusd_1-000001')
        second = self._with_entry(_trade('p1', duration_min=20), 'SYNTH-pos_eurusd_1-000001')

        report = build_trade_history_report(_RUN_ID, _units([first, second]))

        assert [row.entry_executions[0].shared_by for row in report.trades] == [2, 2]
        assert [row.exit_executions[0].shared_by for row in report.trades] == [1, 1]

    def test_two_units_minting_the_same_id_do_not_share_it(self):
        same_id = 'SYNTH-pos_eurusd_1-000001'
        units = [RunUnit(name='EURUSD_feb', symbol='EURUSD',
                         trade_history=[self._with_entry(_trade('p1'), same_id)]),
                 RunUnit(name='EURUSD_oct', symbol='EURUSD',
                         trade_history=[self._with_entry(_trade('p1'), same_id)])]

        report = build_trade_history_report(_RUN_ID, units)

        assert [row.entry_executions[0].shared_by for row in report.trades] == [1, 1]

    def test_a_filtered_list_keeps_the_count_of_the_whole_unit(self):
        first = self._with_entry(_trade('p1', duration_min=10), 'SYNTH-pos_eurusd_1-000001')
        second = self._with_entry(
            _trade('p1', duration_min=20, close_reason=CloseReason.SL_TRIGGERED),
            'SYNTH-pos_eurusd_1-000001')

        report = build_trade_history_report(
            _RUN_ID, _units([first, second]), close_reason=CloseReason.SL_TRIGGERED.value)

        assert len(report.trades) == 1
        assert report.trades[0].entry_executions[0].shared_by == 2


class TestAStreakBelongsToOneAccount:
    """
    A backtest's scenarios are separate accounts. Interleaving their trades by exit time would
    string A's win, B's win and A's win into a run of three that no account had.
    """

    def test_the_longest_streak_is_one_unit_s(self):
        a = [_trade('a1', net_pnl=5.0, entry_offset_min=0), _trade('a2', net_pnl=5.0, entry_offset_min=20),
             _trade('a3', net_pnl=-1.0, entry_offset_min=40)]
        b = [_trade('b1', net_pnl=5.0, entry_offset_min=10), _trade('b2', net_pnl=-1.0, entry_offset_min=30)]
        units = [RunUnit(name='A', symbol='EURUSD', trade_history=a),
                 RunUnit(name='B', symbol='EURUSD', trade_history=b)]

        analytics = build_trade_history_report(_RUN_ID, units).analytics[0]

        # time order across both would read win, win, win (a1, b1, a2): 3
        assert analytics.max_consecutive_wins == 2


class TestWhatBelongsToTheTrade:
    """What a record states about its position, beside the slice it closed."""

    def _chain(self):
        """A position opened at 0.1 and closed in two parts, and a position closed whole."""
        first = replace(_trade(position_id='p1', close_reason=CloseReason.MANUAL),
                        lots=0.04, close_type=CloseType.PARTIAL, entry_lots=0.1)
        rest = replace(_trade(position_id='p1', entry_offset_min=0, duration_min=45),
                       lots=0.06, close_type=CloseType.FULL, entry_lots=0.1)
        whole = replace(_trade(position_id='p2', entry_offset_min=60), entry_lots=0.1)
        return [first, rest, whole]

    def test_each_record_states_its_close_type_and_the_size_at_entry(self):
        rows = build_trade_history_report(_RUN_ID, _units(self._chain())).trades
        assert [row.close_type for row in rows] == [
            CloseType.PARTIAL, CloseType.FULL, CloseType.FULL]
        assert all(row.entry_lots == 0.1 for row in rows)

    def test_the_closes_of_a_position_are_counted_per_position(self):
        rows = build_trade_history_report(_RUN_ID, _units(self._chain())).trades
        assert [(row.position_id, row.position_closes) for row in rows] == [
            ('p1', 2), ('p1', 2), ('p2', 1)]

    def test_a_filter_does_not_change_the_count(self):
        # Counted before the filter: a reader who filtered the partial away still learns
        # that the record in front of them is one of two.
        rows = build_trade_history_report(
            _RUN_ID, _units(self._chain()), close_reason=CloseReason.TP_TRIGGERED.value).trades
        assert [(row.position_id, row.position_closes) for row in rows] == [('p1', 2), ('p2', 1)]



class TestTheSpreadTotal:
    """
    A unit's spread total, and why it is null where the unit's data carried no quote.

    On a quote-driven market the spread is what trading costs while the fees read zero, so a
    footer without it says "free" (measured 2026-10-14 on a forex fixture run: two trades,
    spread 1.48 + 1.48 EUR, fees 0.00). Where a tick file recorded no quote, bid and ask are the
    traded price and every fill's spread reads zero — a sum of those would look like a
    measurement.
    """

    def _units(self, eurgbp_without_quote: bool = False):
        """Two units with two trades each, the second possibly on data without a quote."""
        quoted = [replace(_trade(position_id='p1'), spread_cost=1.4776),
                  replace(_trade(position_id='p2', entry_offset_min=60), spread_cost=1.4779)]
        other = [replace(_trade(position_id='p1', symbol='EURGBP'), spread_cost=0.0),
                 replace(_trade(position_id='p2', symbol='EURGBP', entry_offset_min=60),
                         spread_cost=0.0)]
        return [RunUnit(name='EURUSD_01', symbol='EURUSD', trade_history=quoted),
                RunUnit(name='EURGBP_01', symbol='EURGBP', trade_history=other,
                        input_without_quote=eurgbp_without_quote)]

    def _totals(self, report):
        return {totals.scenario_name: totals.total_spread_cost
                for totals in report.scenario_totals}

    def test_the_trades_spread_costs_are_summed_per_unit(self):
        report = build_trade_history_report(_RUN_ID, self._units())
        assert self._totals(report)['EURUSD_01'] == pytest.approx(2.9555)

    def test_a_measured_zero_stays_a_zero(self):
        assert self._totals(build_trade_history_report(_RUN_ID, self._units()))['EURGBP_01'] == 0.0

    def test_a_unit_that_read_data_without_a_quote_has_no_total(self):
        totals = self._totals(build_trade_history_report(
            _RUN_ID, self._units(eurgbp_without_quote=True)))
        assert totals['EURGBP_01'] is None
        assert totals['EURUSD_01'] == pytest.approx(2.9555)

    def test_a_filtered_view_keeps_the_missing_quote(self):
        # The API filters the persisted report, where the units are no longer at hand
        report = build_trade_history_report(_RUN_ID, self._units(eurgbp_without_quote=True))
        filtered = filter_trade_history_report(report, start=_T0 + timedelta(minutes=30))
        assert self._totals(filtered) == {'EURUSD_01': pytest.approx(1.4779), 'EURGBP_01': None}

    def test_a_report_written_before_the_total_existed_serves_none(self):
        report = build_trade_history_report(_RUN_ID, self._units())
        dumped = report.model_dump()
        for totals in dumped['scenario_totals']:
            del totals['total_spread_cost']
        old = type(report).model_validate(dumped)
        assert set(self._totals(filter_trade_history_report(old)).values()) == {None}


class TestTheUnitKnowsItsDataLackedAQuote:
    """The fact is taken from what the tick index measured per file the scenario read."""

    def _units(self, deficiencies):
        scenario = SingleScenario(
            name='s1', scenario_index=0, symbol='EURGBP', data_broker_type='mt5',
            start_date=_T0, data_deficiencies=deficiencies)
        result = ProcessResult(success=True, scenario_name='s1', scenario_index=0,
                               tick_loop_results=ProcessTickLoopResult())
        return run_units_from_batch(BatchExecutionSummary(
            batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
            process_result_list=[result], single_scenario_list=[scenario]))

    def test_one_file_without_a_quote_is_enough(self):
        units = self._units([[], [DataDeficiency.NO_QUOTE, DataDeficiency.NO_ARRIVAL_TIME]])
        assert units[0].input_without_quote is True

    def test_the_index_hands_the_deficiency_over_as_its_value(self):
        assert self._units([['no_quote']])[0].input_without_quote is True

    def test_other_deficiencies_do_not_count(self):
        units = self._units([[DataDeficiency.NO_ARRIVAL_TIME], [DataDeficiency.SYNTHETIC]])
        assert units[0].input_without_quote is False


class TestAProtectiveExitIsMeasuredAgainstItsLevel:
    """
    #631: a stop-loss or take-profit exit is measured against the level that fired. The simulation
    fills it AT the level, so against the mid of the tick that crossed the level a stop-loss read
    as a gain and a take-profit as a cost — the tick's overshoot, not slippage.
    """

    def _exit(self, close_reason, exit_price, mid, stop_loss=None, take_profit=None,
              exit_level=None):
        """The executor stamps the level that fired on the record — the position's own here,
        unless an own exit's level is given (#625)."""
        if exit_level is None:
            exit_level = close_reason.fired_level(stop_loss, take_profit)
        trade = replace(_trade(close_reason=close_reason), stop_loss=stop_loss,
                        take_profit=take_profit, exit_price=exit_price, exit_level=exit_level,
                        entry_side=OrderSide.BUY, exit_side=OrderSide.SELL,
                        exit_submission=SubmissionMetadata(tick_mid_price=mid))
        return build_trade_history_report(_RUN_ID, _units([trade])).trades[0]

    def test_a_stop_loss_filled_at_its_level_reads_zero(self):
        row = self._exit(CloseReason.SL_TRIGGERED, exit_price=1.0980, mid=1.0978,
                         stop_loss=1.0980)

        assert row.exit_slippage_measured_against is SlippageReference.PROTECTIVE_LEVEL
        assert (row.exit_slippage, row.exit_slippage_pct) == (0.0, 0.0), 'the percent too'

    def test_a_session_reads_the_real_distance_below_its_stop(self):
        row = self._exit(CloseReason.SL_TRIGGERED, exit_price=1.0975, mid=1.0978,
                         stop_loss=1.0980)

        assert row.exit_slippage == pytest.approx(0.0005)

    def test_a_take_profit_is_measured_against_its_target(self):
        row = self._exit(CloseReason.TP_TRIGGERED, exit_price=1.1050, mid=1.1052,
                         stop_loss=1.0980, take_profit=1.1050)

        assert row.exit_slippage_measured_against is SlippageReference.PROTECTIVE_LEVEL
        assert row.exit_slippage == 0.0

    def test_an_own_stop_is_measured_against_the_logic_s_level(self):
        """#625: the position carries no stop — the level travels on the record."""
        row = self._exit(CloseReason.SL_TRIGGERED, exit_price=1.0975, mid=1.0978,
                         exit_level=1.0980)

        assert row.stop_loss is None
        assert row.exit_slippage_measured_against is SlippageReference.PROTECTIVE_LEVEL
        assert row.exit_slippage == pytest.approx(0.0005)

    def test_an_exit_the_logic_chose_keeps_the_mid_when_it_was_sent(self):
        row = self._exit(CloseReason.MANUAL, exit_price=1.1020, mid=1.1022, stop_loss=1.0980)

        assert row.exit_slippage_measured_against is SlippageReference.SUBMISSION_MID
        assert row.exit_slippage == pytest.approx(0.0002), 'sold below the mid'
