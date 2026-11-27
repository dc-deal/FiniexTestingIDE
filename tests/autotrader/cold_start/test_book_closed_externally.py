"""
A Holding Sold Outside the Bot — How Its Note Ends (#627)

At spot a position is the bot's own note, not something the venue knows. When an operator sells the
holding by hand while the bot is down, nothing else can end that note: every start finds the book
claiming coins the account no longer holds, says so as an ERROR and restores the note. One start told
`--book-closed-externally <id>` ends it — only where the venue's balances confirm the coins are gone —
and books it at the session's first instant at its entry price, without moving a balance, because the
venue already shows the sale.
"""

from datetime import datetime, timezone

import pytest

from python.framework.autotrader.cold_start.cold_start_adopter import ColdStartAdopter
from python.framework.testing.mock_broker_adapter import MockExecutionMode
from python.framework.testing.mock_order_execution import MockOrderExecution
from python.framework.types.config_types.autotrader_defaults_config_types import ColdStartDefaults
from python.framework.types.persistence_types import PositionCarryOver
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.validation_types import Severity
from tests.autotrader.cold_start.conftest import PREVIOUS_SESSION_KEY

_FIRST_INSTANT = datetime(2026, 11, 24, 7, 5, tzinfo=timezone.utc)


def _note(position_id: str = 'pos_btcusd_7', lots: float = 0.004,
          direction: str = 'long') -> PositionCarryOver:
    """A remembered spot position, as an earlier session wrote it down."""
    return PositionCarryOver(
        position_id=position_id, symbol='BTCUSD', direction=direction, lots=lots,
        original_lots=lots, entry_price=61200.0, entry_time='2026-11-24T02:00:00+00:00',
        entry_type='market', contract_size=1)


def _adopter(executor, store, logger, closed=None, dry_run: bool = False) -> ColdStartAdopter:
    """The boot step under test, adopting without asking."""
    return ColdStartAdopter(
        executor=executor, store=store, config=ColdStartDefaults(adoption_mode='auto'),
        symbol='BTCUSD', logger=logger, dry_run=dry_run, interactive=False,
        book_closed_externally=closed)


def _first_instant(executor) -> None:
    """The session's first instant, as its loop runs it: the clock moves, then the resolutions."""
    stamp = int(_FIRST_INSTANT.timestamp() * 1000)
    executor.begin_instant(stamp)
    executor.resolve_due(stamp)


def _remember(store, *notes: PositionCarryOver) -> None:
    store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=7,
               open_positions=list(notes))


class TestTheDeclaredNoteEnds:

    def test_it_is_booked_at_its_entry_price_and_moves_no_balance(self, spot_executor, store, logger):
        _remember(store, _note())
        # The operator sold the 0.004 BTC by hand: the coins are gone, the proceeds arrived
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.0, 'ZUSD': 1245.0})
        balances_before = spot_executor.portfolio.get_balances()

        adopter = _adopter(spot_executor, store, logger, closed=['pos_btcusd_7'])
        assert adopter.run() is True

        assert spot_executor.get_open_positions() == [], 'the strategy never sees it open'
        assert not any('short' in e for e in logger.errors), 'no shortfall is left to report'
        assert any('booked as closed outside this bot' in w for w in logger.warnings)
        finding = adopter.get_closed_externally_finding()
        assert (finding.check, finding.severity) == ('book_closed_externally', Severity.WARNING)

        _first_instant(spot_executor)

        (record,) = spot_executor.portfolio.get_trade_history()
        assert (record.position_id, record.close_reason, record.exit_observed) == (
            'pos_btcusd_7', CloseReason.CLOSED_EXTERNALLY, False)
        assert (record.exit_price, record.gross_pnl) == (61200.0, 0.0)
        assert record.exit_time == _FIRST_INSTANT, 'stamped by the canonical clock'
        assert record.exit_trades == []
        assert spot_executor.portfolio.get_balances() == balances_before

    def test_it_is_booked_once(self, spot_executor, store, logger):
        _remember(store, _note())
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.0, 'ZUSD': 1245.0})
        _adopter(spot_executor, store, logger, closed=['pos_btcusd_7']).run()

        _first_instant(spot_executor)
        _first_instant(spot_executor)

        assert len(spot_executor.portfolio.get_trade_history()) == 1

    def test_its_id_is_never_minted_again(self, spot_executor, store, logger):
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=0,
                   open_positions=[_note()])
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.0, 'ZUSD': 1245.0})

        _adopter(spot_executor, store, logger, closed=['pos_btcusd_7']).run()

        assert spot_executor.portfolio.get_position_counter() >= 7

    def test_the_configuration_check_compares_without_it(self, spot_executor, store, logger):
        """#626 counts what is open from the carry-over itself, so it gets the reduced one."""
        class _Compared(Exception):
            pass

        compared = []

        def compare(payload):
            compared.append([note.position_id for note in payload.open_positions])
            raise _Compared()

        _remember(store, _note(), _note('pos_btcusd_8', lots=0.01))
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.01, 'ZUSD': 1245.0})
        adopter = ColdStartAdopter(
            executor=spot_executor, store=store, config=ColdStartDefaults(adoption_mode='auto'),
            symbol='BTCUSD', logger=logger, compare_configuration=compare,
            book_closed_externally=['pos_btcusd_7'])

        with pytest.raises(_Compared):
            adopter.run()

        assert compared == [['pos_btcusd_8']]

    def test_the_other_notes_are_still_held_against_the_balance(self, spot_executor, store, logger):
        _remember(store, _note(), _note('pos_btcusd_8', lots=0.01))
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.01, 'ZUSD': 1245.0})

        _adopter(spot_executor, store, logger, closed=['pos_btcusd_7']).run()

        assert [p.position_id for p in spot_executor.get_open_positions()] == ['pos_btcusd_8']
        assert any('covered' in i for i in logger.infos)


class TestTheVenueHasToConfirmIt:

    def test_a_note_whose_coins_are_still_there_stays(self, spot_executor, store, logger):
        _remember(store, _note())
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.004, 'ZUSD': 1000.0})

        adopter = _adopter(spot_executor, store, logger, closed=['pos_btcusd_7'])
        adopter.run()

        assert [p.position_id for p in spot_executor.get_open_positions()] == ['pos_btcusd_7']
        assert any('not honoured' in w and 'still there' in w for w in logger.warnings)
        assert adopter.get_closed_externally_finding() is None

    def test_a_short_note_cannot_be_confirmed_and_stays(self, spot_executor, store, logger):
        _remember(store, _note(direction='short'))
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.0, 'ZUSD': 1000.0})

        _adopter(spot_executor, store, logger, closed=['pos_btcusd_7']).run()

        assert [p.position_id for p in spot_executor.get_open_positions()] == ['pos_btcusd_7']
        assert any('SHORT note' in w for w in logger.warnings)

    def test_an_id_the_book_does_not_hold_closes_nothing(self, spot_executor, store, logger):
        """A flag left in a service unit names a note that ended long ago."""
        _remember(store, _note())
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.004, 'ZUSD': 1000.0})

        _adopter(spot_executor, store, logger, closed=['pos_btcusd_3']).run()

        assert [p.position_id for p in spot_executor.get_open_positions()] == ['pos_btcusd_7']
        assert any('pos_btcusd_3 closes nothing' in i for i in logger.infos)


class TestWhereItDoesNotApply:

    @pytest.mark.parametrize('spot_mode', [False, True])
    def test_a_margin_session_or_a_dry_run_books_nothing(self, store, logger, spot_mode):
        """Margin reads its positions from the venue; a dry run restores no note at all."""
        executor = MockOrderExecution(
            mode=MockExecutionMode.TIMEOUT, spot_mode=spot_mode,
            initial_balances={'USD': 1000.0, 'BTC': 0.0} if spot_mode else None).create_executor()
        _remember(store, _note())
        executor.broker.adapter.set_broker_balances({'XXBT': 0.0, 'ZUSD': 1000.0})

        _adopter(executor, store, logger, closed=['pos_btcusd_7'], dry_run=spot_mode).run()
        _first_instant(executor)

        assert executor.portfolio.get_trade_history() == []
        said = 'dry run restores no position book' if spot_mode else 'not in spot mode'
        assert any(said in i for i in logger.infos)


class TestTheWayOutIsNamed:

    def test_the_shortfall_error_names_the_flag_and_the_note(self, spot_executor, store, logger):
        _remember(store, _note())
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.0, 'ZUSD': 1245.0})

        _adopter(spot_executor, store, logger).run()

        assert any('--book-closed-externally' in e and 'pos_btcusd_7' in e for e in logger.errors)

    def test_it_names_no_note_the_shortfall_could_not_cover(self, spot_executor, store, logger):
        """0.001 short of a 0.004 note: the flag would be refused, so it is not offered."""
        _remember(store, _note())
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.003, 'ZUSD': 1000.0})

        _adopter(spot_executor, store, logger).run()

        (error,) = [e for e in logger.errors if 'short' in e]
        assert '--book-closed-externally' not in error


class TestThePortfolioBooksItWithoutABalance:

    @pytest.mark.parametrize('spot_mode', [False, True])
    def test_only_a_spot_note_is_booked(self, spot_mode):
        """A margin position is a venue object; its close comes from the venue (#209)."""
        executor = MockOrderExecution(
            mode=MockExecutionMode.TIMEOUT, spot_mode=spot_mode,
            initial_balances={'USD': 1245.0, 'BTC': 0.0} if spot_mode else None).create_executor()
        executor.set_current_time(_FIRST_INSTANT)
        portfolio = executor.portfolio

        if not spot_mode:
            with pytest.raises(ValueError, match='only a spot note'):
                portfolio.close_note_externally(_note(), exit_tick_index=0)
            return

        before = portfolio.get_balances()
        equity_before = portfolio.get_spot_equity(61000.0)
        record = portfolio.close_note_externally(_note(), exit_tick_index=0)

        assert portfolio.get_balances() == before
        assert portfolio.get_spot_equity(61000.0) == equity_before
        assert portfolio.open_positions == {}
        assert (record.close_reason, record.exit_observed, record.net_pnl) == (
            CloseReason.CLOSED_EXTERNALLY, False, 0.0)
        assert portfolio.get_trade_history() == [record]
