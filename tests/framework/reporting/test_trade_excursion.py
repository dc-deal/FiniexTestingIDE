"""
Position Excursion (MAE/MFE) Capture Tests (#389).

The runtime half of the trade-analytics feature: `Position.update_current_price`
tracks the running max adverse / favorable excursion (price + gross P&L) over the
position's life — the input the report postprocessor derives R/MAE/MFE from. Tested
in isolation by driving a price path (LONG + SHORT); no engine run required.
"""

from datetime import datetime, timezone

import pytest

from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.order_types import OrderDirection


_T = datetime(2025, 10, 13, tzinfo=timezone.utc)


def _pos(direction: OrderDirection) -> Position:
    return Position(
        position_id='p1', symbol='EURUSD', direction=direction,
        lots=1.0, original_lots=1.0, entry_price=1.1000, entry_time=_T)


def test_seeds_at_entry():
    p = _pos(OrderDirection.LONG)
    assert p.mae_price == 1.1000 and p.mfe_price == 1.1000   # seeded to entry (0 excursion)
    assert p.mae_pnl == 0.0 and p.mfe_pnl == 0.0


def test_long_excursion():
    p = _pos(OrderDirection.LONG)                            # LONG closes at bid
    p.update_current_price(1.1010, 1.1011, 1.0, 5)           # favorable: +0.0010
    p.update_current_price(1.0990, 1.0991, 1.0, 5)           # adverse:   -0.0010
    p.update_current_price(1.1005, 1.1006, 1.0, 5)           # neither extreme
    assert p.mfe_price == 1.1010                             # highest bid
    assert p.mae_price == 1.0990                             # lowest bid
    assert p.mfe_pnl == pytest.approx(100.0)                 # 0.0010 * 1e5 * 1.0 * 1.0
    assert p.mae_pnl == pytest.approx(-100.0)


def test_short_excursion():
    p = _pos(OrderDirection.SHORT)                           # SHORT closes at ask
    p.update_current_price(1.0989, 1.0990, 1.0, 5)           # favorable: entry-ask = +0.0010
    p.update_current_price(1.1010, 1.1011, 1.0, 5)           # adverse:   entry-ask = -0.0011
    assert p.mfe_price == 1.0990
    assert p.mae_price == 1.1011
    assert p.mfe_pnl == pytest.approx(100.0)
    assert p.mae_pnl == pytest.approx(-110.0)
