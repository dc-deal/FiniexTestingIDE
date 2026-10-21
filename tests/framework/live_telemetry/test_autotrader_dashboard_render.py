"""
FiniexTestingIDE - AutoTrader Dashboard Render Tests (#586)

Every panel of the live dashboard, rendered from a fully populated frame into a recording
console: no live terminal, no tick source, no thread. The trade-history panel once read a field
its row never had and ended the display at the first ordinary exit — nothing rendered it,
because every mock profile switches the display off.
"""

import dataclasses
import io
from datetime import datetime, timedelta, timezone
from typing import List

import pytest
from rich.console import Console
from rich.panel import Panel

import python.system.ui.autotrader_live_display as autotrader_live_display
from python.framework.autotrader.autotrader_display_exporter import AutotraderDisplayExporter
from python.framework.types.autotrader_types.autotrader_display_types import (
    AutoTraderDisplayStats,
    TickSourceSnapshot,
    TradeHistoryEntry,
)
from python.framework.types.portfolio_types.portfolio_trade_record_types import (
    CloseReason,
    EntryType,
    TradeRecord,
)
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.order_types import (
    CloseType,
    OrderDirection,
    OrderSide,
)

# One width per layout, each the narrowest of its layout — where a panel has the least room
_SINGLE, _TWO, _THREE = 100, 120, 160
# Wide enough that no free text wraps inside its panel
_WIDE = 400

_PANELS = {
    _SINGLE: ['SESSION', 'PORTFOLIO', 'OPEN POSITIONS', 'ORDERS', 'REJECTIONS',
              'API PERFORMANCE'],
    _TWO: ['SESSION', 'PORTFOLIO', 'ALGO STATE', 'CONNECTION', 'API PERFORMANCE',
           'OPEN POSITIONS', 'ORDERS', 'REJECTIONS', 'TRADE HISTORY'],
    _THREE: ['SESSION', 'CONNECTION', 'WORKER PERFORMANCE', 'API PERFORMANCE', 'PORTFOLIO',
             'TICK PROCESSING', 'OPEN POSITIONS', 'ORDERS', 'REJECTIONS', 'TRADE HISTORY',
             'ALGO STATE'],
}


def _export(panel: Panel, width: int) -> str:
    """
    Print a panel into a recording console and return what it shows.

    Args:
        panel: The rendered dashboard
        width: Terminal width

    Returns:
        The screen as plain text
    """
    console = Console(file=io.StringIO(), width=width, height=400, record=True,
                      color_system=None, force_terminal=False)
    console.print(panel)
    return console.export_text()


def _line_with(text: str, needle: str) -> str:
    """
    The one line of a screen that holds a text.

    Args:
        text: The screen
        needle: What the line holds

    Returns:
        That line
    """
    lines = [line for line in text.splitlines() if needle in line]
    assert len(lines) == 1, (needle, lines)
    return lines[0]


class TestTheFrameIsFull:
    """The frame every render test uses carries every field — a new field joins the renders."""

    def test_every_field_of_the_frame_differs_from_its_default(self, dashboard_frame):
        for f in dataclasses.fields(AutoTraderDisplayStats):
            if f.default is not dataclasses.MISSING:
                default = f.default
            elif f.default_factory is not dataclasses.MISSING:
                default = f.default_factory()
            else:
                continue
            assert getattr(dashboard_frame, f.name) != default, f.name


class TestEveryPanelRenders:

    @pytest.mark.parametrize('width', [_SINGLE, _TWO, _THREE], ids=['1-col', '2-col', '3-col'])
    def test_each_panel_of_the_layout_is_on_screen(self, dashboard, dashboard_frame, connection,
                                                   frame_time, render_reports, width):
        text = _export(dashboard.render(dashboard_frame, connection, frame_time, width), width)

        for title in _PANELS[width]:
            assert title in text, title
        assert 'panel failed' not in text
        assert render_reports == []

    @pytest.mark.parametrize('width', [_SINGLE, _TWO, _THREE], ids=['1-col', '2-col', '3-col'])
    def test_a_margin_account(self, dashboard, dashboard_frame, connection, frame_time,
                              render_reports, width):
        frame = dataclasses.replace(dashboard_frame, trading_model='margin', spot_balances=None)

        text = _export(dashboard.render(frame, connection, frame_time, width), width)

        assert 'PORTFOLIO' in text
        assert render_reports == []

    def test_before_the_first_snapshot(self, dashboard, connection, frame_time, render_reports):
        text = _export(dashboard.render(None, connection, frame_time, _TWO), _TWO)

        assert 'FiniexAutoTrader' in text
        assert render_reports == []

    def test_the_ages_are_measured_against_the_frame(self, dashboard, dashboard_frame,
                                                     connection, frame_time):
        text = _export(dashboard.render(dashboard_frame, connection, frame_time, _TWO), _TWO)

        assert 'Uptime:  54h 12m 00s' in text
        # Measured against the last tick (two seconds before the frame), not the wall clock
        assert '2m ago' in _line_with(text, '50500.00')

    def test_the_audit_line_takes_the_width_it_is_given(self, dashboard, dashboard_frame,
                                                        connection, frame_time):
        narrow = _export(dashboard.render(dashboard_frame, connection, frame_time, _SINGLE),
                         _SINGLE)
        wide = _export(dashboard.render(dashboard_frame, connection, frame_time, _TWO), _TWO)

        assert '⚠1F' in _line_with(narrow, 'Audit:')
        assert '⚠1 fee' in _line_with(wide, 'Audit:')

    def test_an_order_partly_filled_shows_its_progress(self, dashboard, dashboard_frame,
                                                       connection, frame_time):
        text = _export(dashboard.render(dashboard_frame, connection, frame_time, _TWO),
                       _TWO)

        assert 'PARTIAL' in _line_with(text, '└─ 0.0400/0.1000')


class TestTheTablesSpanTheFullWidth:
    """
    OPEN POSITIONS, ORDERS and TRADE HISTORY stand below the columns, each across the full
    width (#586). In a column they had a third or a half of the terminal, and their tables cut
    the numbers a trader reads them for: `0.0…` lots, `49800…` prices, `WATCH…`.
    """

    _WHOLE = {
        'OPEN POSITIONS': ['pos_btcusd_9', '0.0500', '50100.00', '-3.2500',
                           '└─ 2 fills', '50100.00→50110.00'],
        'ORDERS': ['pos_btcusd_11', 'limit', '49800.00', 'WATCHING', '11m ago',
                   '└─ 0.0400/0.1000', 'PARTIAL', '■ 1 PENDING'],
        'TRADE HISTORY': ['50000.00', '50500.00', '+24.5000', 'remain', '2m ago',
                          '50000.00→50010.00', '50490.00→50510.00'],
    }

    @pytest.mark.parametrize('width', [_TWO, _THREE, _WIDE], ids=['2-col', '3-col', 'wide'])
    def test_each_table_has_a_row_of_its_own(self, dashboard, dashboard_frame, connection,
                                             frame_time, width):
        text = _export(dashboard.render(dashboard_frame, connection, frame_time, width), width)

        for title in self._WHOLE:
            # The table's top border is the only panel border on its line
            assert _line_with(text, f' {title} ').count('╭') == 1, title

    @pytest.mark.parametrize('width', [_SINGLE, _TWO, _THREE], ids=['1-col', '2-col', '3-col'])
    def test_no_number_is_cut_at_the_narrowest_width_of_a_layout(
            self, dashboard, dashboard_frame, connection, frame_time, width):
        text = _export(dashboard.render(dashboard_frame, connection, frame_time, width), width)

        for title, whole in self._WHOLE.items():
            if title not in _PANELS[width]:
                continue
            for value in whole:
                assert value in text, (title, value)


class TestTheTradeHistoryReason:
    """
    The Reason column says how a strategy's own close relates to its position (#330). It is
    the column that once read a field the row never had.
    """

    @pytest.mark.parametrize('close_reason', list(CloseReason), ids=lambda r: r.name)
    @pytest.mark.parametrize('close_type', list(CloseType), ids=lambda t: t.name)
    @pytest.mark.parametrize('entry_lots', [0.10, 0.05, None], ids=['larger', 'equal', 'none'])
    def test_every_way_a_trade_closes(self, dashboard, dashboard_frame, connection, frame_time,
                                      render_reports, make_trade_row, close_reason, close_type,
                                      entry_lots):
        frame = dataclasses.replace(dashboard_frame, recent_trades=[make_trade_row(
            close_reason=close_reason, close_type=close_type, entry_lots=entry_lots)])

        text = _export(dashboard.render(frame, connection, frame_time, _TWO), _TWO)

        row = _line_with(text, '50500.00')
        if close_reason is not CloseReason.MANUAL:
            expected = close_reason.value[:8]
        elif close_type is CloseType.PARTIAL:
            expected = 'partial'
        elif entry_lots is not None and entry_lots > 0.05:
            expected = 'remain'
        else:
            expected = None
        if expected is not None:
            assert expected in row
        else:
            assert 'remain' not in row and 'partial' not in row
        assert render_reports == []

    def test_each_side_of_a_trade_filled_in_parts_gets_its_line(self, dashboard,
                                                               dashboard_frame, connection,
                                                               frame_time):
        text = _export(dashboard.render(dashboard_frame, connection, frame_time, _TWO),
                       _TWO)

        assert '└─ in 2' in text
        assert '└─ out 2' in text


class TestTheVenuesAndTheStrategysWordsAreText:
    """
    A closing tag with no opener builds fine and fails only when it is PRINTED — a venue's
    reason or a strategy's event carrying one would end the display at print time.
    """

    @pytest.mark.parametrize('needle', [
        'EOrder:[/x] Insufficient funds',
        'trend filter [ATR] armed, waiting [/x]',
        'entry skipped [/x] — spread too wide',
        'epoch changed [/x] — reconnecting',
        'drawdown 5.2% [limit 5.0%]',
        '(main [prod])',
        'daily budget [/x] spent',
        'regime=range [low]',
        'why=cross [up]',
        'mode=strict [x]',
    ])
    def test_brackets_reach_the_screen_as_written(self, dashboard, dashboard_frame, connection,
                                                  frame_time, render_reports, needle):
        text = _export(dashboard.render(dashboard_frame, connection, frame_time, _WIDE), _WIDE)

        assert needle in text
        assert render_reports == []


class TestAPanelThatFails:
    """One panel's failure is shown in its place, reported once, and the rest renders."""

    def test_it_shows_failed_in_place_and_is_reported_once(self, dashboard, dashboard_frame,
                                                           connection, frame_time,
                                                           render_reports, make_trade_row):
        broken = dataclasses.replace(make_trade_row(), net_pnl=None)
        frame = dataclasses.replace(dashboard_frame, recent_trades=[broken])

        first = _export(dashboard.render(frame, connection, frame_time, _TWO), _TWO)
        dashboard.render(frame, connection, frame_time, _TWO)

        assert 'panel failed: TypeError' in first
        for title in _PANELS[_TWO]:
            assert title in first, title
        assert len(render_reports) == 1
        assert render_reports[0].startswith('❌ [DISPLAY] Panel TRADE HISTORY failed — TypeError')
        assert 'trading is unaffected' in render_reports[0]
        assert 'Traceback' in render_reports[0]


class TestTheLoop:
    """The display thread's own loop: printing happens inside its guards."""

    def test_a_frame_that_cannot_be_printed_does_not_end_the_loop(self, dashboard,
                                                                  render_reports, monkeypatch):
        screen = io.StringIO()
        terminal = Console(file=screen, width=130, height=60, force_terminal=True,
                           color_system=None)
        monkeypatch.setattr(dashboard, '_console', terminal)
        # A frame whose markup only fails when printed — what an unescaped text would cause
        monkeypatch.setattr(dashboard, 'render',
                            lambda *args: Panel('closed [/x] by venue'))
        cycles: List[int] = []

        def one_cycle(_seconds: float) -> None:
            cycles.append(1)
            if len(cycles) == 3:
                dashboard.stop()

        monkeypatch.setattr(autotrader_live_display.time, 'sleep', one_cycle)
        # The loop runs inline, in this thread — start() would put it on a thread of its own
        dashboard._running = True

        dashboard._update_loop()

        assert len(cycles) == 3
        assert len(render_reports) == 1
        assert 'The dashboard frame failed — MarkupError' in render_reports[0]
        assert 'dashboard render failed: MarkupError' in screen.getvalue()
        # The failure frame still names the deployment it belongs to
        assert 'DEPLOYMENT deploy_20261012_074850' in screen.getvalue()


class TestTheExporterCopiesTheRecord:
    """The row is copied from the closed trade's record, every field of it (#586)."""

    @staticmethod
    def _record(entry_lots) -> TradeRecord:
        """
        A closed trade's record, as the portfolio writes it at a partial close.

        Every field the row copies differs from the row's own default, so a copy the exporter
        forgets cannot pass unseen.

        Args:
            entry_lots: The position's size when it opened

        Returns:
            The record
        """
        opened = datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc)
        executions = [BrokerTrade(
            trade_id=f'T-{n}', parent_broker_ref='OREF-1', order_id='pos_btcusd_3',
            volume=0.025, price=50000.0 + n, fee=0.01, fee_currency='USD',
            timestamp=opened + timedelta(seconds=n), side=OrderSide.SELL, is_maker=False)
            for n in range(2)]
        return TradeRecord(
            position_id='pos_btcusd_3', symbol='BTCUSD', direction=OrderDirection.LONG,
            lots=0.05, close_type=CloseType.PARTIAL, entry_price=50000.0, entry_time=opened,
            entry_tick_value=1.0, entry_bid=49999.0, entry_ask=50001.0, exit_price=50500.0,
            exit_time=opened + timedelta(minutes=30), exit_tick_value=1.0, entry_tick_index=0,
            exit_tick_index=100, digits=2, contract_size=1, spread_cost=0.0,
            commission_cost=0.4, swap_cost=0.0, total_fees=0.4, gross_pnl=25.0, net_pnl=24.6,
            entry_lots=entry_lots, close_reason=CloseReason.SL_TRIGGERED,
            entry_type=EntryType.MARKET, entry_trades=list(executions),
            exit_trades=list(executions), exit_side=OrderSide.SELL)

    @pytest.mark.parametrize('entry_lots', [0.10, None], ids=['set', 'none'])
    def test_the_size_at_entry_travels(self, entry_lots):
        row = AutotraderDisplayExporter.trade_history_entry(self._record(entry_lots))

        assert row.entry_lots == entry_lots

    def test_every_field_of_the_row_comes_from_the_record(self):
        record = self._record(0.10)

        row = AutotraderDisplayExporter.trade_history_entry(record)

        for f in dataclasses.fields(TradeHistoryEntry):
            source = 'position_id' if f.name == 'trade_id' else f.name
            assert hasattr(record, source), f'{f.name}: the record has no {source}'
            assert getattr(row, f.name) == getattr(record, source), f.name
            if f.default is not dataclasses.MISSING:
                assert getattr(record, source) != f.default, f'{f.name} would pass uncopied'
            elif f.default_factory is not dataclasses.MISSING:
                assert getattr(record, source) != f.default_factory(), (
                    f'{f.name} would pass uncopied')


def test_the_connection_snapshot_defaults_render(dashboard, dashboard_frame, frame_time):
    """A source that has reported nothing yet — no message, no tick, no quote channel."""
    text = _export(dashboard.render(dashboard_frame, TickSourceSnapshot(), frame_time, _TWO), _TWO)

    assert '● waiting' in text
    assert '(no quote channel)' in text


@pytest.mark.parametrize('message_age_s, expected', [
    (1.0, '● connected'),
    (6.0, '● stale'),
    (11.0, '● dead'),
])
def test_the_stream_is_judged_by_the_sources_own_threshold(dashboard, dashboard_frame, frame_time,
                                                           message_age_s, expected):
    """Stale from half the threshold the source reconnects at, dead from all of it (#609)."""
    connection = TickSourceSnapshot(message_age_s=message_age_s, connection_dead_s=10.0)

    text = _export(dashboard.render(dashboard_frame, connection, frame_time, _TWO), _TWO)

    assert expected in text
