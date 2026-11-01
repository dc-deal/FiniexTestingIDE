"""
FiniexTestingIDE - AutoTrader Dashboard Render Tests (#586)

Every panel of the live dashboard, rendered from a fully populated frame into a recording
console: no live terminal, no tick source, no thread. The trade-history panel once read a field
its row never had and ended the display at the first ordinary exit — nothing rendered it,
because every mock profile switches the display off.
"""

import dataclasses
import io
import time
from datetime import datetime, timedelta, timezone
from typing import List

import pytest
from rich.console import Console
from rich.live import Live
from rich.panel import Panel

import python.system.ui.autotrader_live_display as autotrader_live_display
from python.framework.autotrader.autotrader_display_exporter import AutotraderDisplayExporter
from python.framework.types.autotrader_types.autotrader_display_types import (
    AutoTraderDisplayStats,
    TickSourceSnapshot,
    TradeHistoryEntry,
)
from python.framework.types.autotrader_types.shutdown_status_types import ShutdownStep
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


class TestAStopInProgress:
    """
    A session that stops says what it waits for and what is still to do (#630). Stated once in a
    panel on top, because a dashboard that keeps saying RUNNING and calls a stopped source dead
    reads like a fault while the session does exactly what it should.
    """

    _DRAWN = [
        '🛑 SHUTTING DOWN · SIGTERM 14:22:42 UTC · decisions stopped',
        '   Waiting for   market BUY 0.12 ETHUSD · on its way 4.2 s · venue has not answered',
        '   Still to do   carry-over · 0 orders to cancel · venue read · reports · alerts',
        '   Order wait    4.2 s of 30.0 s, then: booked unaccounted, reported, session ends '
        'itself',
        '   Kill          in 75.8 s (supervisor)',
    ]

    @pytest.mark.parametrize('width', [_SINGLE, _TWO, _THREE], ids=['1-col', '2-col', '3-col'])
    def test_it_stands_on_top_as_it_was_approved_in_each_layout(
            self, dashboard, dashboard_frame, connection, frame_time, render_reports, stopping,
            width):
        text = _export(dashboard.render(dashboard_frame, connection, frame_time, width, stopping),
                       width)

        lines = text.splitlines()
        for row, drawn in enumerate(self._DRAWN):
            # Below the frame's top border and the panel's own
            assert lines[2 + row].startswith(f'│ │ {drawn}'), (drawn, lines[2 + row])
        assert 'panel failed' not in text
        assert render_reports == []

    @pytest.mark.parametrize('width', [_SINGLE, _TWO, _THREE], ids=['1-col', '2-col', '3-col'])
    def test_the_rest_of_the_dashboard_stays(self, dashboard, dashboard_frame, connection,
                                             frame_time, stopping, width):
        text = _export(dashboard.render(dashboard_frame, connection, frame_time, width, stopping),
                       width)

        for title in _PANELS[width]:
            assert title in text, title

    def test_no_stop_no_panel(self, dashboard, dashboard_frame, connection, frame_time):
        text = _export(dashboard.render(dashboard_frame, connection, frame_time, _TWO), _TWO)

        assert 'SHUTTING DOWN' not in text
        assert 'Still to do' not in text

    @pytest.mark.parametrize('width', [_SINGLE, _TWO, _THREE], ids=['1-col', '2-col', '3-col'])
    def test_nothing_awaited_still_says_what_is_to_do(self, dashboard, dashboard_frame,
                                                        connection, frame_time, stopping, width):
        status = dataclasses.replace(stopping, awaited_orders=(),
                                     order_wait_began_monotonic_s=None, order_wait_bound_s=None,
                                     step=ShutdownStep.ORDER_CLEANUP, orders_to_cancel=2)

        text = _export(dashboard.render(dashboard_frame, connection, frame_time, width, status),
                       width)

        assert 'Waiting for' not in text
        assert 'Order wait' not in text
        assert 'Now           2 orders to cancel' in text
        assert 'Still to do   venue read · reports · alerts' in text

    def test_no_kill_declared_no_kill_line(self, dashboard, dashboard_frame, connection,
                                           frame_time, stopping):
        status = dataclasses.replace(stopping, kill_at_monotonic_s=None)

        text = _export(dashboard.render(dashboard_frame, connection, frame_time, _TWO, status),
                       _TWO)

        assert 'Kill' not in text
        assert 'supervisor' not in text

    def test_a_cropped_frame_cannot_hide_it(self, dashboard, dashboard_frame, connection,
                                            frame_time, stopping):
        screen = io.StringIO()
        # Far too short for the frame: the live display crops it from the bottom
        terminal = Console(file=screen, width=_TWO, height=12, force_terminal=True,
                           color_system=None)
        frame = dashboard.render(dashboard_frame, connection, frame_time, _TWO, stopping)

        with Live(console=terminal, auto_refresh=False) as live:
            live.update(frame, refresh=True)

        for drawn in self._DRAWN:
            assert drawn in screen.getvalue(), drawn

    def test_many_orders_are_counted_rather_than_taking_the_frame(
            self, dashboard, dashboard_frame, connection, frame_time, stopping):
        row = stopping.awaited_orders[0]
        status = dataclasses.replace(stopping, awaited_orders=(row,) * 11)

        text = _export(dashboard.render(dashboard_frame, connection, frame_time, _TWO, status),
                       _TWO)

        assert text.count('market BUY 0.12 ETHUSD') == dashboard._MAX_ACTIVE_ORDERS
        assert '+ 3 more' in text
        assert text.count('Waiting for') == 1

    def test_a_venues_and_a_strategys_words_are_text(self, dashboard, dashboard_frame, connection,
                                                     frame_time, render_reports, stopping):
        row = dataclasses.replace(stopping.awaited_orders[0], waits_for='venue said [/x] wait')
        status = dataclasses.replace(stopping, asked_by='strategy [/x] asked',
                                     awaited_orders=(row,))

        text = _export(dashboard.render(dashboard_frame, connection, frame_time, _WIDE, status),
                       _WIDE)

        assert 'strategy [/x] asked' in text
        assert 'venue said [/x] wait' in text
        assert render_reports == []

    def test_before_the_first_snapshot(self, dashboard, connection, frame_time, render_reports,
                                       stopping):
        text = _export(dashboard.render(None, connection, frame_time, _TWO, stopping), _TWO)

        assert 'FiniexAutoTrader' in text
        assert '🛑 SHUTTING DOWN · SIGTERM 14:22:42 UTC' in text
        assert 'Still to do' in text
        assert render_reports == []

    @pytest.mark.parametrize('width', [_SINGLE, _TWO, _THREE], ids=['1-col', '2-col', '3-col'])
    def test_the_status_line_stops_claiming_running(self, dashboard, dashboard_frame, connection,
                                                    frame_time, stopping, width):
        running = _export(dashboard.render(dashboard_frame, connection, frame_time, width), width)
        stopped = _export(dashboard.render(dashboard_frame, connection, frame_time, width,
                                           stopping), width)

        assert '● RUNNING' in _line_with(running, 'Status:')
        assert '🛑 SHUTTING DOWN' in _line_with(stopped, 'Status:')
        assert 'RUNNING' not in stopped
        # The frame is a pulse: its time since the last tick is no longer news
        assert 'since last tick' not in stopped

    @pytest.mark.parametrize('width', [_TWO, _THREE], ids=['2-col', '3-col'])
    def test_a_source_stopped_on_purpose_is_no_dead_stream(self, dashboard, dashboard_frame,
                                                           frame_time, stopping, width):
        silent = TickSourceSnapshot(message_age_s=11.0, connection_dead_s=10.0)
        status = dataclasses.replace(stopping, tick_source_stopped=True)

        text = _export(dashboard.render(dashboard_frame, silent, frame_time, width, status), width)

        assert '● stopped on purpose' in text
        assert '● dead' not in text

    def test_a_source_still_running_during_the_wait_reads_as_it_is(
            self, dashboard, dashboard_frame, frame_time, stopping):
        silent = TickSourceSnapshot(message_age_s=11.0, connection_dead_s=10.0)

        text = _export(dashboard.render(dashboard_frame, silent, frame_time, _TWO, stopping), _TWO)

        assert '● dead' in text
        assert 'stopped on purpose' not in text

    @pytest.mark.parametrize('width', [_SINGLE, _TWO, _THREE], ids=['1-col', '2-col', '3-col'])
    def test_a_panel_that_fails_shows_failed_in_place_and_is_reported_once(
            self, dashboard, dashboard_frame, connection, frame_time, render_reports, stopping,
            width):
        broken = dataclasses.replace(stopping, asked_at=None)

        first = _export(dashboard.render(dashboard_frame, connection, frame_time, width, broken),
                        width)
        dashboard.render(dashboard_frame, connection, frame_time, width, broken)

        assert 'panel failed: TypeError' in first
        assert first.index('SHUTTING DOWN') < first.index('panel failed: TypeError')
        for title in _PANELS[width]:
            assert title in first, title
        assert len(render_reports) == 1
        assert render_reports[0].startswith('❌ [DISPLAY] Panel SHUTTING DOWN failed — TypeError')

    def test_the_frame_polls_what_the_stop_is_doing(self, dashboard, dashboard_frame,
                                                    shutdown_holder, render_reports, monkeypatch):
        terminal = Console(file=io.StringIO(), width=_TWO, height=60, color_system=None,
                           force_terminal=False)
        monkeypatch.setattr(dashboard, '_console', terminal)
        dashboard._stats = dashboard_frame
        assert 'SHUTTING DOWN' not in _export(dashboard._frame(), _TWO)

        shutdown_holder.note_asked(
            'SIGTERM', datetime(2026, 10, 12, 14, 22, 42, tzinfo=timezone.utc), time.monotonic())

        assert '🛑 SHUTTING DOWN · SIGTERM 14:22:42 UTC' in _export(dashboard._frame(), _TWO)
        assert render_reports == []

    def test_a_failure_frame_still_says_the_session_is_stopping(
            self, dashboard, shutdown_holder, render_reports, monkeypatch):
        def fails(*args):
            raise RuntimeError('cannot draw')

        monkeypatch.setattr(dashboard, 'render', fails)
        assert 'SHUTTING DOWN' not in _export(dashboard._frame(), _TWO)

        shutdown_holder.note_asked(
            'SIGTERM', datetime(2026, 10, 12, 14, 22, 42, tzinfo=timezone.utc), time.monotonic())

        text = _export(dashboard._frame(), _TWO)
        assert '🛑 SHUTTING DOWN' in text
        assert 'dashboard render failed: RuntimeError' in text


@pytest.mark.parametrize('on_a_terminal', [True, False], ids=['terminal', 'no_terminal'])
def test_it_knows_whether_it_has_a_terminal_to_draw_on(dashboard, monkeypatch, on_a_terminal):
    """Without one it shows only its last frame, so a stop's lines must reach the console (#630)."""
    monkeypatch.setattr(dashboard, '_console', Console(
        file=io.StringIO(), force_terminal=on_a_terminal, color_system=None))

    assert dashboard.holds_a_terminal() is on_a_terminal


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
