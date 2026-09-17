"""
Test the Traded Price Across Its Serialization Boundaries.

`TickData.price` resolves the venue's basis from the data itself — the traded price where
there is one, the midpoint where there is not. That works only while an absent traded price
arrives as `None` and never as `0.0`, because a zero IS a price to everything downstream: it
would render an entire quote-driven archive at zero, silently, since `dropna` drops NaN and
not zeros.

Two boundaries carry a tick, and the first draft of this work saw only one of them. The pickle
transport between processes is the obvious one. `TickData.to_dict()` is the other, and it
feeds the coordinator tick log and two executor forensics records.
"""

from datetime import datetime, timezone

import pandas as pd
import pytest

from python.framework.types.market_types.market_data_types import (
    TickData,
    TickTransportColumn,
)
from python.framework.data_preparation.shared_data_preparator import SharedDataPreparator
from python.framework.exceptions.data_quality_errors import TradedPriceMissingException
from python.framework.utils.process_serialization_utils import (
    _traded_price_or_none,
    process_deserialize_ticks_batch,
    serialize_ticks_for_transport,
)

_TS = datetime(2026, 1, 15, 10, 0, 0, tzinfo=timezone.utc)


def _transport_dict(last):
    """Build one transport dict as the pack side would emit it.

    Args:
        last: The traded price to carry, or None to omit the column entirely

    Returns:
        A dict keyed the way TickTransportColumn declares
    """
    row = {
        TickTransportColumn.TIME_MSC: int(_TS.timestamp() * 1000),
        TickTransportColumn.BID: 100.0,
        TickTransportColumn.ASK: 100.2,
        TickTransportColumn.VOLUME: 1.0,
    }
    if last is not None:
        row[TickTransportColumn.LAST] = last
    return row


class TestAnAbsentTradedPriceIsNeverZero:
    """
    The single invariant `TickData.price` rests on.
    """

    def test_zero_reads_as_absent(self):
        """A quote-driven venue writes 0.0; that is an absence, not a price."""
        assert _traded_price_or_none(0.0) is None
        assert _traded_price_or_none(0) is None

    def test_a_missing_column_reads_as_absent(self):
        """An archive file written before `last` existed carries no column at all."""
        assert _traded_price_or_none(None) is None

    def test_a_negative_price_reads_as_absent(self):
        """Not reachable from a sane producer, and still not a price if it arrives."""
        assert _traded_price_or_none(-1.0) is None

    def test_a_real_traded_price_survives(self):
        """The only input that is a price stays one."""
        assert _traded_price_or_none(88000.3) == 88000.3


class TestThePickleBoundary:
    """
    `TickTransportColumn` is the contract between the main process and a scenario
    subprocess. A field the contract does not carry becomes None on the other side.
    """

    def test_the_transport_declares_the_traded_price(self):
        """Declared on the enum, not only on the dataclass."""
        assert TickTransportColumn.LAST.value == 'last'

    def test_a_traded_price_survives_the_round_trip(self):
        """An order-driven tick keeps its traded price, and `price` returns it."""
        ticks = process_deserialize_ticks_batch(
            'BTCUSD', {'BTCUSD': (_transport_dict(100.2),)})

        assert ticks[0].last == 100.2
        assert ticks[0].price == 100.2

    def test_a_zero_comes_back_as_none_not_as_zero(self):
        """
        The trap this whole invariant exists for.

        Every optional column beside this one defaults to 0.0 on unpack, and copying that
        idiom here would give MT5 `last = 0.0` — a price of zero on every forex bar.
        """
        ticks = process_deserialize_ticks_batch(
            'EURUSD', {'EURUSD': (_transport_dict(0.0),)})

        assert ticks[0].last is None, 'a zero must not survive as a price'
        assert ticks[0].price == ticks[0].mid

    def test_an_absent_column_comes_back_as_none(self):
        """A pre-`last` archive file resolves to the midpoint, as it always did."""
        ticks = process_deserialize_ticks_batch(
            'EURUSD', {'EURUSD': (_transport_dict(None),)})

        assert ticks[0].last is None
        assert ticks[0].price == ticks[0].mid


class TestAnEmptyTradedPriceColumnIsNotShipped:
    """
    A quote-driven venue's `last` column is all zeros, and every one of them is discarded on
    the other side. Carrying it costs a scenario subprocess 3.1 MB and 27 ms per 280,000
    ticks — measured — for a column that conveys nothing.

    Dropping it is EXACT, not an approximation: an absent column and an all-zero column both
    resolve to `None`, so the tick that comes out is the same object either way.
    """

    def _frame(self, last_values):
        """Build a transport-shaped frame with the given traded prices.

        Args:
            last_values: Values for the `last` column

        Returns:
            A DataFrame carrying the transport columns
        """
        n = len(last_values)
        return pd.DataFrame({
            TickTransportColumn.TIME_MSC.value: [1_700_000_000_000 + i * 400 for i in range(n)],
            TickTransportColumn.BID.value: [1.1000] * n,
            TickTransportColumn.ASK.value: [1.1002] * n,
            TickTransportColumn.VOLUME.value: [0.0] * n,
            TickTransportColumn.LAST.value: last_values,
        })

    def test_an_all_zero_column_is_dropped(self):
        """The quote-driven case — the column carries no information and does not travel."""
        records = serialize_ticks_for_transport(self._frame([0.0, 0.0, 0.0]))

        assert TickTransportColumn.LAST.value not in records[0]

    def test_a_populated_column_travels(self):
        """The order-driven case — every value is a real event and must reach the other side."""
        records = serialize_ticks_for_transport(self._frame([1.1002, 1.1000, 1.1002]))

        assert records[0][TickTransportColumn.LAST.value] == 1.1002

    def test_a_partially_populated_column_travels_whole(self):
        """
        One real trade is enough to keep the column.

        The test is the drop rule's boundary: it fires only when NOTHING is positive, so a
        venue that prints trades never loses one because some rows happened to be empty.
        """
        records = serialize_ticks_for_transport(self._frame([0.0, 1.1002, 0.0]))

        assert [r[TickTransportColumn.LAST.value] for r in records] == [0.0, 1.1002, 0.0]

    def test_dropping_it_changes_nothing_about_the_tick(self):
        """
        The property that makes the optimization safe rather than merely cheap.

        Same ticks out, whether the empty column was shipped or dropped.
        """
        dropped = process_deserialize_ticks_batch(
            'EURUSD',
            {'EURUSD': tuple(serialize_ticks_for_transport(self._frame([0.0, 0.0])))})
        # What the previous behaviour produced: the zeros transported and then discarded.
        shipped = process_deserialize_ticks_batch(
            'EURUSD',
            {'EURUSD': tuple(self._frame([0.0, 0.0]).to_dict('records'))})

        assert [t.last for t in dropped] == [t.last for t in shipped] == [None, None]
        assert [t.price for t in dropped] == [t.price for t in shipped]


class TestTheDictBoundary:
    """
    `to_dict()` is the second boundary — the coordinator tick log and the executor's
    forensics records read it, and it emitted only `mid` before.
    """

    def test_it_carries_both_prices(self):
        """
        Both, deliberately: `mid` is what a valuation used and `price` what a strategy saw.

        A forensics record carrying only one cannot tell the two apart afterwards, which is
        exactly the question such a record exists to answer.
        """
        tick = TickData(timestamp=_TS, symbol='BTCUSD',
                        bid=88000.1, ask=88000.3, last=88000.3)
        d = tick.to_dict()

        assert d['mid'] == pytest.approx(88000.2)
        assert d['last'] == 88000.3
        assert d['price'] == 88000.3

    def test_an_absent_traded_price_is_serialized_as_none(self):
        """Not as zero — the same invariant, on the other boundary."""
        d = TickData(timestamp=_TS, symbol='EURUSD', bid=1.1000, ask=1.1002).to_dict()

        assert d['last'] is None
        assert d['price'] == d['mid']


class TestTheMountRefusesATickSetThatLostItsTradedPrice:
    """
    The guard one layer below the import refusal.

    The import refuses a FILE without a traded price, so reaching a mount without one means
    the pipeline dropped the column — a column projection added for memory (#442) is the
    change that would do it. Without the guard the failure is silent: `price` falls back to
    the midpoint and every bar of the run is built on a basis the archive does not share.

    It cannot fire today. It is written because the change that would make it fire is
    scheduled by name, and because the failure is invisible on data below collector format
    1.6.0 and total from the first file with a real spread.
    """

    def _prep(self):
        """A preparator instance with nothing initialised — only the guard is exercised.

        Returns:
            A SharedDataPreparator whose __init__ is bypassed
        """
        return SharedDataPreparator.__new__(SharedDataPreparator)

    def _frame(self, last_values=None):
        """Build a mounted tick frame, optionally without the traded price column.

        Args:
            last_values: Values for `last`, or None to omit the column entirely

        Returns:
            A DataFrame in the shape the mount hands to transport
        """
        n = 3
        data = {'bid': [1.1000] * n, 'ask': [1.1002] * n}
        if last_values is not None:
            data['last'] = last_values
        return pd.DataFrame(data)

    def test_an_order_driven_mount_without_the_column_is_refused(self):
        """The projection case: the column never arrived."""
        with pytest.raises(TradedPriceMissingException, match='order_driven'):
            self._prep()._assert_traded_price_present(
                'kraken_spot', 'BTCUSD', self._frame())

    def test_an_order_driven_mount_of_only_zeros_is_refused(self):
        """The column arrived carrying nothing, which is the same loss by another route."""
        with pytest.raises(TradedPriceMissingException, match='BTCUSD'):
            self._prep()._assert_traded_price_present(
                'kraken_spot', 'BTCUSD', self._frame([0.0, 0.0, 0.0]))

    def test_an_order_driven_mount_with_traded_prices_passes(self):
        """The real case — every Kraken mount today."""
        self._prep()._assert_traded_price_present(
            'kraken_spot', 'BTCUSD', self._frame([1.1002, 1.1000, 1.1002]))

    def test_a_quote_driven_mount_is_not_checked(self):
        """
        MT5 has no traded price by construction and writes zeros.

        Checking it would refuse every forex scenario — the guard must key on the venue's
        declared structure, never on whether the column happens to hold anything.
        """
        self._prep()._assert_traded_price_present(
            'mt5', 'EURUSD', self._frame([0.0, 0.0, 0.0]))

    def test_a_quote_driven_mount_without_the_column_is_not_checked(self):
        """A pre-`last` archive file is legitimate on that side."""
        self._prep()._assert_traded_price_present('mt5', 'EURUSD', self._frame())
