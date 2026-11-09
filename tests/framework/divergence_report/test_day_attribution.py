"""
The day attribution of the divergence report (#512) — one day's change of account value, taken
apart, and two runs' days compared part by part.

A spot day worked by hand: the account starts with 1000 USD and no ETH at a mid of 3000, buys
0.1 ETH at 3001 for a fee of 0.60 — decided at a mid of 2999, submitted at 3000 — and ends at a
mid of 3010. Its value rises by 0.30: the end price 1.10, the delay −0.10, the execution −0.10 and
the fee −0.60. A margin day is not taken apart, and says why.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Dict, Optional

import pytest

from python.framework.reporting.builders.divergence.day_attribution_builder import (
    attribute_day,
    decompose_day,
)
from python.framework.types.api.divergence_report_types import OrderOutcome
from python.framework.types.api.report_types import OpenPositionRow, OrderEventRow
from python.framework.types.api.state_frame_types import StateFrame, StateFrameCause
from python.framework.types.trading_env_types.order_event_types import OrderEventType
from python.framework.types.trading_env_types.order_types import (
    OrderAction,
    OrderDirection,
    OrderSide,
)

_DAY = date(2026, 11, 25)
_OPENING = datetime(2026, 11, 25, tzinfo=timezone.utc)
_CLOSING = _OPENING + timedelta(days=1)
_FILLED = _OPENING + timedelta(hours=10)


def _spot_frame(at: datetime, usd: float, eth: float, mid: float) -> StateFrame:
    return StateFrame(
        frame_time=at, written_at=at, written_for=StateFrameCause.CADENCE, ticks_processed=1,
        symbol='ETHUSD', account_currency='USD', last_price=mid, balance=usd,
        balances={'USD': usd, 'ETH': eth}, equity=usd + eth * mid, open_positions=[])


def _buy(fee: Optional[float] = 0.6, at: datetime = _FILLED) -> OrderEventRow:
    return OrderEventRow(
        scenario_name='my_unit', seq=2, event_type=OrderEventType.FILLED, order_id='order_1',
        submitted_seq=1, action=OrderAction.OPEN, direction=OrderDirection.LONG,
        symbol='ETHUSD', lots=0.1, fill_price=3001.0, fee=fee, event_time=at.isoformat())


def _frames(end_mid: float = 3010.0):
    return [_spot_frame(_OPENING - timedelta(seconds=30), 1000.0, 0.0, 3000.0),
            _spot_frame(_CLOSING - timedelta(seconds=30), 1000.0 - 300.1 - 0.6, 0.1, end_mid)]


_DECIDED = OrderOutcome(run_id='s1', seq=1, order_side=OrderSide.BUY, submission_mid=3000.0,
                        decision_mid=2999.0)


def _paired(outcome: OrderOutcome = _DECIDED) -> Dict:
    return {(outcome.run_id, outcome.seq): outcome}


class TestOneSpotDay:

    def test_a_paired_fill_splits_at_its_decision_and_its_submission(self):
        parts, why = decompose_day(_OPENING, _CLOSING, _frames(), [('s1', _buy())], _paired(),
                                   'ETH')
        assert why == ''
        assert parts.equity_change == pytest.approx(0.3)
        assert (parts.end_price, parts.delay, parts.execution, parts.fees) == pytest.approx(
            (1.1, -0.1, -0.1, -0.6))
        assert parts.held == pytest.approx(0.0) and parts.one_sided == 0.0
        assert parts.residual == pytest.approx(0.0, abs=1e-9)

    def test_an_unpaired_fill_stays_whole(self):
        parts, _ = decompose_day(_OPENING, _CLOSING, _frames(), [('s1', _buy())], {}, 'ETH')
        assert parts.one_sided == pytest.approx(0.9)
        assert parts.end_price == parts.delay == parts.execution == 0.0
        assert parts.residual == pytest.approx(0.0, abs=1e-9)

    def test_what_was_held_at_the_start_is_valued_over_the_day(self):
        frames = [_spot_frame(_OPENING - timedelta(seconds=30), 700.0, 0.1, 3000.0),
                  _spot_frame(_CLOSING - timedelta(seconds=30), 700.0, 0.1, 3010.0)]
        parts, _ = decompose_day(_OPENING, _CLOSING, frames, [], {}, 'ETH')
        assert parts.held == pytest.approx(1.0) and parts.residual == pytest.approx(0.0, abs=1e-9)

    def test_a_missing_quantity_leaves_a_residual(self):
        parts, _ = decompose_day(_OPENING, _CLOSING, _frames(), [('s1', _buy(fee=None))], {},
                                 'ETH')
        assert parts.residual == pytest.approx(-0.6)

    def test_a_fill_outside_the_two_frames_belongs_to_another_day(self):
        parts, _ = decompose_day(_OPENING, _CLOSING, _frames(),
                                 [('s1', _buy(at=_OPENING - timedelta(minutes=5)))], {}, 'ETH')
        assert parts.fees == 0.0


class TestWhatIsNotTakenApart:

    def test_a_margin_day_says_why(self):
        margin = [frame.model_copy(update={'balances': None, 'open_positions': [OpenPositionRow(
            position_id='order_1', direction='long', lots=0.1, entry_price=3001.0,
            entry_time=_FILLED.isoformat())]}) for frame in _frames()]
        parts, why = decompose_day(_OPENING, _CLOSING, margin, [], {}, 'ETH')
        assert parts is None and 'margin account' in why

    def test_a_day_without_frames_at_both_ends(self):
        parts, why = decompose_day(_OPENING, _CLOSING, _frames()[1:], [], {}, 'ETH')
        assert parts is None and 'no valued frame' in why

    def test_a_spot_day_without_a_base_currency(self):
        parts, why = decompose_day(_OPENING, _CLOSING, _frames(), [], {}, None)
        assert parts is None and 'base currency' in why


class TestTwoRuns:

    def test_the_difference_is_taken_part_by_part(self):
        backtest_decided = _DECIDED.model_copy(update={'run_id': 'b1', 'submission_mid': 2999.5})
        attribution = attribute_day(
            _DAY, _OPENING, _CLOSING, _frames(), [('s1', _buy())], _paired(),
            _frames(), [('b1', _buy())], _paired(backtest_decided), 'ETH')
        difference = attribution.difference
        assert difference.equity_change == pytest.approx(0.0)
        assert difference.delay == pytest.approx(-0.05)
        assert difference.execution == pytest.approx(0.05)
        assert difference.residual == pytest.approx(0.0, abs=1e-9)

    def test_a_side_that_cannot_be_taken_apart_names_itself(self):
        attribution = attribute_day(_DAY, _OPENING, _CLOSING, _frames(), [], {}, [], [], {},
                                    'ETH')
        assert attribution.difference is None
        assert attribution.not_computed_because.startswith('backtest: no valued frame')
