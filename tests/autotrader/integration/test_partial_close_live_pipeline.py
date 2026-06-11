"""
FiniexTestingIDE - Partial Close Lifecycle through the AutoTrader Pipeline (#330)

Runs the partial_close_lifecycle profile (scripted BacktestingMultiPosition decision
logic + mock adapter) end-to-end and verifies:
- The three derived TradeRecords from one partially-closed position all share
  the same entry_trades (multi-fill visibility paradigm)
- The event-stream CSV contains the expected sequence (ORDER_SUBMIT,
  CLOSE_SUBMIT, FILL, POSITION_OPEN, POSITION_CLOSE)
- shared(Nx) detection works on the live-pipeline side as well as in sim
"""

import csv
import shutil

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.reporting.event_stream_csv_writer import EVENT_FIELDS
from python.framework.types.trading_env_types.order_types import CloseType, OrderSide


MOCK_PROFILE = 'configs/autotrader_profiles/backtesting/partial_close_lifecycle.json'


@pytest.fixture(scope='module')
def session_result():
    """Run one full mock session for the entire module."""
    config = load_autotrader_config(MOCK_PROFILE)
    trader = AutotraderMain(config)
    result = trader.run()
    run_dir = trader._run_dir
    yield result, run_dir
    if run_dir and run_dir.exists():
        shutil.rmtree(run_dir)


class TestSessionCompletes:
    """Smoke: profile runs to normal shutdown and produces the expected trades."""

    def test_normal_shutdown(self, session_result):
        result, _ = session_result
        assert result.shutdown_mode == 'normal'

    def test_no_errors(self, session_result):
        result, _ = session_result
        assert len(result.error_messages) == 0, result.error_messages

    def test_expected_trade_count(self, session_result):
        """3 partials from pos_usdjpy_1 + 1 full from pos_usdjpy_2 = 4 records."""
        result, _ = session_result
        assert len(result.trade_history) == 4


class TestEntryTradesSharedAcrossPartials:
    """Multi-fill data model: all derived TradeRecords share the entry execution."""

    def test_three_partial_records_share_entry_trade(self, session_result):
        result, _ = session_result
        partials_pos1 = [
            tr for tr in result.trade_history
            if tr.position_id == 'pos_usdjpy_1'
        ]
        assert len(partials_pos1) == 3, partials_pos1

        # All three records carry the same single entry execution
        for tr in partials_pos1:
            assert len(tr.entry_trades) == 1
        entry_trade_ids = {tr.entry_trades[0].trade_id for tr in partials_pos1}
        assert len(entry_trade_ids) == 1, entry_trade_ids

    def test_entry_trade_volume_is_original_lots(self, session_result):
        """entry_trades[0].volume reflects the ORIGINAL open (0.03), not the
        per-record close share (0.01)."""
        result, _ = session_result
        partials_pos1 = [
            tr for tr in result.trade_history
            if tr.position_id == 'pos_usdjpy_1'
        ]
        for tr in partials_pos1:
            assert tr.entry_trades[0].volume == pytest.approx(0.03)
            assert tr.lots == pytest.approx(0.01)

    def test_exit_trades_distinct_per_record(self, session_result):
        result, _ = session_result
        partials_pos1 = [
            tr for tr in result.trade_history
            if tr.position_id == 'pos_usdjpy_1'
        ]
        exit_ids = [tr.exit_trades[0].trade_id for tr in partials_pos1]
        assert len(set(exit_ids)) == len(exit_ids), exit_ids

    def test_close_type_split(self, session_result):
        """2 PARTIAL records + 1 FULL (remainder) for pos_usdjpy_1."""
        result, _ = session_result
        partials_pos1 = [
            tr for tr in result.trade_history
            if tr.position_id == 'pos_usdjpy_1'
        ]
        partial_count = sum(1 for tr in partials_pos1 if tr.close_type == CloseType.PARTIAL)
        full_count = sum(1 for tr in partials_pos1 if tr.close_type == CloseType.FULL)
        assert partial_count == 2
        assert full_count == 1


class TestExecutionSides:
    """Trade-event side (BUY/SELL) is populated on TradeRecord per the BUY/SELL refactor."""

    def test_long_position_records_have_buy_entry_sell_exit(self, session_result):
        """pos_usdjpy_1 is LONG — open via BUY, close via SELL on every record."""
        result, _ = session_result
        for tr in (t for t in result.trade_history if t.position_id == 'pos_usdjpy_1'):
            assert tr.entry_side == OrderSide.BUY, f"entry_side {tr.entry_side}"
            assert tr.exit_side == OrderSide.SELL, f"exit_side {tr.exit_side}"

    def test_short_position_records_have_sell_entry_buy_exit(self, session_result):
        """pos_usdjpy_2 is SHORT — open via SELL, close via BUY."""
        result, _ = session_result
        shorts = [t for t in result.trade_history if t.position_id == 'pos_usdjpy_2']
        assert len(shorts) == 1
        assert shorts[0].entry_side == OrderSide.SELL
        assert shorts[0].exit_side == OrderSide.BUY

    def test_entry_trades_carry_buy_sell_not_long_short(self, session_result):
        """BrokerTrade.side on entry_trades is now OrderSide (BUY/SELL),
        independent of the position direction it derives from."""
        result, _ = session_result
        for tr in result.trade_history:
            for bt in tr.entry_trades:
                assert isinstance(bt.side, OrderSide), f"got {type(bt.side)}"
            for bt in tr.exit_trades:
                assert isinstance(bt.side, OrderSide), f"got {type(bt.side)}"


class TestSinglePositionIsolation:
    """Trade #2 (single full close) is not contaminated by the partial chain."""

    def test_pos_usdjpy_2_single_record(self, session_result):
        result, _ = session_result
        records = [tr for tr in result.trade_history if tr.position_id == 'pos_usdjpy_2']
        assert len(records) == 1
        assert records[0].close_type == CloseType.FULL

    def test_pos_usdjpy_2_entry_trade_not_shared(self, session_result):
        """pos_usdjpy_2 entry trade_id does NOT appear in pos_usdjpy_1 records."""
        result, _ = session_result
        pos2 = next(tr for tr in result.trade_history if tr.position_id == 'pos_usdjpy_2')
        pos2_entry_id = pos2.entry_trades[0].trade_id

        pos1_entry_ids = {
            bt.trade_id
            for tr in result.trade_history if tr.position_id == 'pos_usdjpy_1'
            for bt in tr.entry_trades
        }
        assert pos2_entry_id not in pos1_entry_ids


class TestEventStreamCsv:
    """The events.csv contains the expected sequence of events."""

    def test_csv_exists(self, session_result):
        _, run_dir = session_result
        assert (run_dir / 'events.csv').exists()

    def test_csv_header_matches_canonical(self, session_result):
        _, run_dir = session_result
        with open(run_dir / 'events.csv') as f:
            header = next(csv.reader(f))
        assert tuple(header) == EVENT_FIELDS

    def _event_types(self, run_dir):
        with open(run_dir / 'events.csv') as f:
            rows = list(csv.reader(f))
        return [r[1] for r in rows[1:]]

    def test_order_submit_emitted_for_each_position(self, session_result):
        _, run_dir = session_result
        events = self._event_types(run_dir)
        assert events.count('ORDER_SUBMIT') == 2  # one per opened position

    def test_close_submit_emitted_three_times(self, session_result):
        """2 partial closes + 1 full close (remainder of pos_usdjpy_1) + 1 close of pos_usdjpy_2 = 4."""
        _, run_dir = session_result
        events = self._event_types(run_dir)
        assert events.count('CLOSE_SUBMIT') == 4

    def test_position_open_close_pair(self, session_result):
        _, run_dir = session_result
        events = self._event_types(run_dir)
        assert events.count('POSITION_OPEN') == 2     # one per position
        assert events.count('POSITION_CLOSE') == 4    # 3 partials of pos_1 + 1 full of pos_2

    def test_fill_count_matches_trade_records(self, session_result):
        """One FILL per BrokerTrade. 2 entry fills (one per position) + 4 exit
        fills (3 for pos_usdjpy_1 partials + 1 for pos_usdjpy_2)."""
        result, run_dir = session_result
        events = self._event_types(run_dir)
        expected_fills = sum(
            len(tr.exit_trades) for tr in result.trade_history
        )
        # Plus 1 entry FILL per UNIQUE position
        unique_positions = {tr.position_id for tr in result.trade_history}
        expected_fills += len(unique_positions)
        assert events.count('FILL') == expected_fills
