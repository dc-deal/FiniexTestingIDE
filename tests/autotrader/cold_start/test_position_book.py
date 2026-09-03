"""
The Position Book — What a Spot Bot Has to Remember (#355)

A spot position is not an object the venue holds. Kraken knows balances and orders; a holding
is a number without an entry price and without an owner. Everything that turns 0.014 BTC into
a position — direction, entry, fee, "still open" — is OUR record, derived from our own fills.
So it survives a restart only because we wrote it down, and a bot that does not write it down
reads its own holding as flat and opens a second position beside it.

Which is why this suite is mostly about SILENT loss rather than about crashes. A sparsely
written note does not fail — it produces a closing report that looks complete and is not:
excursion extrema back at zero, a partial close returned as untouched, the entry executions
gone. Each of those gets its own case here.

The cross-check against the venue is deliberately one-sided, and that is asserted too: more
coins than the book claims is normal (the account is shared), fewer is not.
"""

from dataclasses import fields

from python.framework.autotrader.cold_start_adopter import ColdStartAdopter
from python.framework.persistence.position_book_projection import (
    carry_over_to_position,
    position_to_carry_over,
)
from python.framework.persistence.position_book_watcher import PositionBookWatcher
from python.framework.reporting.builders.cold_start_report_builder import (
    build_cold_start_report_from_session,
)
from python.framework.trading_env.trading_fees import MakerTakerFee
from python.framework.types.config_types.autotrader_defaults_config_types import ColdStartDefaults
from python.framework.types.persistence_types import PositionCarryOver
from python.framework.types.portfolio_types.portfolio_types import Position, PositionStatus
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderSide
from python.framework.utils.run_id_utils import build_client_order_id
from tests.autotrader.cold_start.conftest import PREVIOUS_SESSION_KEY, make_broker_order

_ENTRY_TIME = '2026-09-01T12:00:00+00:00'


def _note(position_id: str = 'pos_btcusd_47', lots: float = 0.01) -> PositionCarryOver:
    """One remembered spot position, as an earlier session wrote it down."""
    return PositionCarryOver(
        position_id=position_id,
        symbol='BTCUSD',
        direction='long',
        lots=lots,
        original_lots=lots,
        entry_price=61200.0,
        entry_time=_ENTRY_TIME,
        entry_type='market',
        contract_size=1,
    )


def _live_position() -> Position:
    """A position as it stands mid-life: partially closed, with a fee and an execution."""
    position = Position(
        position_id='pos_btcusd_47',
        symbol='BTCUSD',
        direction=OrderDirection.LONG,
        lots=0.01,
        original_lots=0.02,
        entry_price=61200.0,
        entry_time=carry_over_to_position(_note()).entry_time,
        broker_ref='OQ4T2K',
        digits=1,
        contract_size=1,
        pip_size=0.1,
        price_unit='tick',
        entry_tick_value=1.0,
        entry_bid=61199.0,
        entry_ask=61201.0,
        entry_tick_index=1234,
    )
    position.status = PositionStatus.PARTIALLY_CLOSED
    position.mae_pnl = -12.5
    position.mfe_pnl = 33.0
    position.mae_price = 60000.0
    position.mfe_price = 62000.0
    position.fees.append(
        MakerTakerFee(is_maker=True, maker_rate=0.16, taker_rate=0.26, order_value=612.0))
    position.entry_trades.append(BrokerTrade(
        trade_id='T1',
        parent_broker_ref='OQ4T2K',
        order_id='pos_btcusd_47',
        volume=0.02,
        price=61200.0,
        fee=0.98,
        fee_currency='USD',
        timestamp=position.entry_time,
        side=OrderSide.BUY,
        is_maker=True,
    ))
    return position


def _adopter(executor, store, logger, dry_run: bool = False):
    """The boot step under test, adopting without asking."""
    return ColdStartAdopter(
        executor=executor,
        store=store,
        config=ColdStartDefaults(adoption_mode='auto'),
        symbol='BTCUSD',
        logger=logger,
        dry_run=dry_run,
        interactive=False,
    )


class TestTheNoteLosesNothing:
    """Every field of an open position survives the round trip, or a later report lies."""

    def test_the_scalars_come_back_unchanged(self):
        original = _live_position()

        restored = carry_over_to_position(position_to_carry_over(original))

        for field in (
            'position_id', 'symbol', 'direction', 'lots', 'original_lots', 'entry_price',
            'entry_time', 'entry_type', 'entry_tick_value', 'entry_bid', 'entry_ask',
            'broker_ref', 'digits', 'contract_size', 'pip_size', 'price_unit',
            'entry_tick_index', 'swap_accrued_until',
        ):
            assert getattr(restored, field) == getattr(original, field), field

    def test_a_partial_close_does_not_return_as_untouched(self):
        original = _live_position()

        restored = carry_over_to_position(position_to_carry_over(original))

        assert restored.status == PositionStatus.PARTIALLY_CLOSED
        assert restored.lots == 0.01
        assert restored.original_lots == 0.02

    def test_the_excursion_extrema_survive_the_constructor(self):
        # MAE/MFE cannot be recomputed after the fact — they are a running maximum over a
        # life that already happened. Position.__post_init__ therefore seeds mae_price /
        # mfe_price from entry_price only where the field is still unset; it used to do so
        # unconditionally and discarded a restored value without a word.
        original = _live_position()

        restored = carry_over_to_position(position_to_carry_over(original))

        assert restored.mae_pnl == -12.5
        assert restored.mfe_pnl == 33.0
        assert restored.mae_price == 60000.0
        assert restored.mfe_price == 62000.0

    def test_the_incurred_fee_still_counts_toward_the_position(self):
        original = _live_position()

        restored = carry_over_to_position(position_to_carry_over(original))

        assert restored.get_total_fees() == original.get_total_fees()
        # The fee TYPE is kept, so the per-type queries a report runs still answer.
        assert restored.fees[0].fee_type == original.fees[0].fee_type

    def test_the_entry_executions_are_not_dropped(self):
        original = _live_position()

        restored = carry_over_to_position(position_to_carry_over(original))

        assert len(restored.entry_trades) == 1
        assert restored.entry_trades[0].trade_id == 'T1'
        assert restored.entry_trades[0].side == OrderSide.BUY
        assert restored.entry_trades[0].is_maker is True


class TestTheWatcherWritesOnChange:
    """
    Two classes of change, because they cost differently and are worth differently.

    A structural change cannot be recovered, so it is written at once. Drift — the exit
    levels and the excursion extrema — moves on nearly every tick of a trend (a trailing stop
    moves with every new high) and one write costs 11 ms on this project's tree, so it waits
    for a cadence. Without the split the seam is a 37 ms stall per tick.
    """

    def test_an_unchanged_book_is_not_written_again(self):
        position = _live_position()
        watcher = PositionBookWatcher([position])

        assert watcher.has_changed([position]) is False
        assert watcher.has_changed([position], drift_due=True) is False

    def test_nothing_written_yet_counts_as_a_change(self):
        watcher = PositionBookWatcher()

        assert watcher.has_changed([]) is True

    def test_a_partial_close_is_structural_and_fires_at_once(self):
        position = _live_position()
        watcher = PositionBookWatcher([position])

        position.lots = 0.005

        assert watcher.has_changed([position]) is True

    def test_a_moved_stop_waits_for_the_cadence(self):
        # A stale exit level is corrected by the algo on its next pass — it re-derives its
        # trailing stop — so this one is allowed to wait. Writing it immediately is what made
        # the seam cost 37 ms per tick in a trend.
        position = _live_position()
        watcher = PositionBookWatcher([position])

        position.stop_loss = 60000.0

        assert watcher.has_changed([position]) is False
        assert watcher.has_changed([position], drift_due=True) is True

    def test_a_new_excursion_extreme_waits_for_the_cadence(self):
        # MAE/MFE cannot be recomputed, but losing the last interval of a running maximum
        # understates a figure rather than inventing one.
        position = _live_position()
        watcher = PositionBookWatcher([position])

        position.mfe_pnl = 99.0

        assert watcher.has_changed([position]) is False
        assert watcher.has_changed([position], drift_due=True) is True

    def test_a_new_position_is_structural_even_while_drift_waits(self):
        first = _live_position()
        watcher = PositionBookWatcher([first])
        second = _live_position()
        second.position_id = 'pos_btcusd_48'

        assert watcher.has_changed([first, second]) is True

    def test_the_watcher_does_not_advance_on_the_question(self):
        # The caller advances it with accept() after a write that went through. A watcher
        # that advanced on the query would drop the trigger for good whenever a write failed.
        position = _live_position()
        watcher = PositionBookWatcher([position])
        position.lots = 0.005

        assert watcher.has_changed([position]) is True
        assert watcher.has_changed([position]) is True

        watcher.accept([position])

        assert watcher.has_changed([position]) is False


class TestTheBookComesBackAtBoot:
    """No tick is needed: every value was known when the position was opened."""

    def test_a_remembered_position_is_restored_before_the_first_tick(
        self, spot_executor, store, logger
    ):
        store.save(
            session_key=PREVIOUS_SESSION_KEY,
            highest_position_counter=47,
            open_positions=[_note()],
        )
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.01, 'ZUSD': 1000.0})

        assert _adopter(spot_executor, store, logger).run() is True

        positions = spot_executor.get_open_positions()
        assert [p.position_id for p in positions] == ['pos_btcusd_47']
        assert positions[0].entry_price == 61200.0

    def test_a_margin_session_leaves_the_note_alone_and_says_so(self, executor, store, logger):
        # Margin positions are real objects at the venue carrying our tag (#209). A note
        # over them would be the older of two answers.
        store.save(
            session_key=PREVIOUS_SESSION_KEY,
            highest_position_counter=47,
            open_positions=[_note()],
        )

        assert _adopter(executor, store, logger).run() is True

        assert executor.get_open_positions() == []
        assert any('not in spot mode' in message for message in logger.errors)

    def test_a_dry_run_restores_nothing(self, spot_executor, store, logger):
        store.save(
            session_key=PREVIOUS_SESSION_KEY,
            highest_position_counter=47,
            open_positions=[_note()],
        )

        assert _adopter(spot_executor, store, logger, dry_run=True).run() is True

        assert spot_executor.get_open_positions() == []


class TestTheCrossCheckOnlyReports:
    """The venue's balance is a check on the note, never a source for it."""

    def test_more_coins_than_the_book_claims_is_not_a_divergence(
        self, spot_executor, store, logger
    ):
        # The account is shared: the surplus may be the operator's or another bot's. What a
        # bot may USE is declared capital (#489), which is a different mechanism.
        store.save(
            session_key=PREVIOUS_SESSION_KEY,
            highest_position_counter=47,
            open_positions=[_note()],
        )
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 5.0, 'ZUSD': 1000.0})

        _adopter(spot_executor, store, logger).run()

        assert not any('short' in message for message in logger.errors)

    def test_fewer_coins_than_the_book_claims_is_reported_and_not_corrected(
        self, spot_executor, store, logger
    ):
        store.save(
            session_key=PREVIOUS_SESSION_KEY,
            highest_position_counter=47,
            open_positions=[_note(lots=0.01)],
        )
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.004, 'ZUSD': 1000.0})

        _adopter(spot_executor, store, logger).run()

        assert any('short' in message for message in logger.errors)
        # Reported, NOT adjusted: shrinking the note to fit would invent a number, and the
        # operator would never learn that something sold outside this bot.
        assert spot_executor.get_open_positions()[0].lots == 0.01


class TestTheIdCounterCannotCollide:
    """
    An id is a name, so lifting the floor too high costs nothing — reading it too low does.

    The dangerous case is the one where the carry-over is GONE: its high-water mark is then 0,
    nothing is adopted (the order cannot be attributed), and the next minted id would restart
    at 1 — while a run record from the earlier session already carries that name.
    """

    def test_an_order_of_our_shape_from_a_lost_session_still_lifts_the_floor(
        self, executor, store, logger
    ):
        # No carry-over at all: the venue is the only source left.
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ4T2K-UNK', build_client_order_id('9c4e', 'pos_btcusd_93')),
        ])

        _adopter(executor, store, logger).run()

        assert executor.portfolio.get_position_counter() == 93

    def test_a_restored_position_lifts_the_floor_too(self, spot_executor, store, logger):
        # Belt and braces: if the stored high-water mark and the stored book ever disagree,
        # the book still occupies its own id.
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=0,
                   open_positions=[_note(position_id='pos_btcusd_58')])
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.01})

        _adopter(spot_executor, store, logger).run()

        assert spot_executor.portfolio.get_position_counter() == 58

    def test_a_foreign_symbol_does_not_inflate_our_counter(self, executor, store, logger):
        # pos_<symbol>_<n> is numbered per bot, so another symbol's counter says nothing
        # about ours — reading it would just skip ids for no reason.
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=3)
        order = make_broker_order('OQ1M5C-ETH',
                                  build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_ethusd_500'))
        order.symbol = 'ETHUSD'
        executor.broker.adapter.set_broker_orders([order])

        _adopter(executor, store, logger).run()

        assert executor.portfolio.get_position_counter() == 3


class TestTheNoteCoversPosition:
    """
    The consistency guard for two type lists that describe the same thing.

    `Position` and `PositionCarryOver` overlap by design — the note is the serialisable
    projection of the runtime object — and nothing in the language makes them agree. The
    failure mode is not a crash but a SILENT omission: a field added to Position simply stops
    surviving restarts, and the first symptom is a report that looks complete and is not.
    That is exactly how the excursion extrema were being written stale.

    So every field of Position is either carried, or listed here with a reason. Adding one
    without deciding which turns this test red.
    """

    # Deliberately NOT carried — each is either recomputed from the next tick or belongs to a
    # position that is no longer open.
    NOT_CARRIED = {
        'current_price': 'a mark, recomputed from the next tick',
        'unrealized_pnl': 'a mark, recomputed from the next tick',
        'gross_pnl': 'a mark, recomputed from the next tick',
        'close_time': 'only set once the position is closed, and then it is not carried over',
        'close_price': 'only set once the position is closed',
        'exit_tick_value': 'only set once the position is closed',
        'exit_tick_index': 'only set once the position is closed',
    }

    def test_every_position_field_is_carried_or_declared_not_to_be(self):
        position_fields = {field.name for field in fields(Position)}
        carried = set(PositionCarryOver.model_fields)

        unaccounted = position_fields - carried - set(self.NOT_CARRIED)

        assert not unaccounted, (
            f'Position gained {sorted(unaccounted)} — decide whether the carry-over must '
            f'hold it (add it to PositionCarryOver and the projection) or must not (add it '
            f'to NOT_CARRIED with the reason). Silently omitting it means the field stops '
            f'surviving a restart and no test says so.'
        )

    def test_the_note_invents_no_field_of_its_own(self):
        # The other direction: a note field with no counterpart on Position would be restored
        # into nothing, so the restore would drop it without a word.
        position_fields = {field.name for field in fields(Position)}

        invented = set(PositionCarryOver.model_fields) - position_fields

        assert not invented, f'PositionCarryOver carries {sorted(invented)}, which Position has not'


class TestARefusedBootClaimsNothing:
    """
    A record that lists what a boot found must say whether the boot ACTED on it.

    The refusal path files its situation on purpose — it is the outcome that matters most to
    whoever reads the run afterwards. But the lists then describe what WOULD have happened,
    and a reader who is not told that sees a session that never traded as one that inherited
    a book and a position.
    """

    def test_a_refused_boot_marks_the_situation_as_not_applied(
        self, executor, store, logger
    ):
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=0)
        key = build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47')
        executor.broker.adapter.set_broker_orders([make_broker_order('OQ7X2A-OLD', key)])
        adopter = ColdStartAdopter(
            executor=executor, store=store,
            config=ColdStartDefaults(adoption_mode='operator_confirm'),
            symbol='BTCUSD', logger=logger, dry_run=False, interactive=False)

        assert adopter.run() is False

        situation = adopter.get_situation()
        assert situation is not None, 'the refusal must still file its situation'
        assert situation.applied is False
        # And nothing reached the executor.
        assert executor.get_active_orders() == []

    def test_a_boot_that_proceeds_marks_it_applied(self, executor, store, logger):
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=0)
        key = build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47')
        executor.broker.adapter.set_broker_orders([make_broker_order('OQ7X2A-OLD', key)])

        adopter = _adopter(executor, store, logger)
        assert adopter.run() is True

        assert adopter.get_situation().applied is True

    def test_the_report_carries_it(self, executor, store, logger):
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=0)
        executor.broker.adapter.set_broker_orders([make_broker_order('OQ9Z1B-EXT', None)])
        adopter = _adopter(executor, store, logger)
        adopter.run()

        report = build_cold_start_report_from_session(
            '20260903_100000_abcdef12', adopter.get_situation(), adopter.get_verdict(), '')

        assert report.applied is True
        # The distinct reasons are derived once, in the builder — a renderer formats, it
        # does not aggregate.
        assert report.skipped_reasons == ['foreign_key']


class TestTheBookSurvivesADamagedNote:
    """An unreadable carry-over is reported and treated as absent — it never ends a boot."""

    def test_a_corrupt_enum_value_degrades_instead_of_crashing(
        self, spot_executor, store, logger
    ):
        # The payload parses as JSON and as a Pydantic model and is still unusable: the
        # note holds enum values as STRINGS.
        broken = _note()
        broken.direction = 'sideways'
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=0,
                   open_positions=[broken])
        spot_executor.broker.adapter.set_broker_balances({'XXBT': 0.01})

        assert _adopter(spot_executor, store, logger).run() is True

        assert spot_executor.get_open_positions() == []
        assert any('could not be rebuilt' in message for message in logger.errors)
