"""
A tick's two stamps (#457, #461): the source stamp the venue gave it and the receipt stamp of its
arrival, read in one place for both pipelines.

The backtest orders its timeline by the receipt and the AutoTrader stamps its instants by it; the
source stamp stays on the tick as data. This suite holds where each comes from and how each falls
back when a file recorded less.
"""

from datetime import datetime, timezone

from python.framework.types.market_types.market_data_types import (
    TickData,
    tick_event_msc,
    tick_receipt_msc,
)


def _tick(time_msc: int, collected_msc: int = 0) -> TickData:
    return TickData(
        timestamp=datetime.fromtimestamp(time_msc / 1000, tz=timezone.utc),
        symbol='BTCUSD', bid=50000.0, ask=50001.0, time_msc=time_msc,
        collected_msc=collected_msc)


class TestATicksTwoStamps:

    def test_the_source_stamp_is_the_venue_stamp_not_the_timestamp(self):
        """A tick whose timestamp differs from its venue stamp: the venue stamp is the source stamp."""
        tick = TickData(
            timestamp=datetime.fromtimestamp(1784737896.0, tz=timezone.utc), symbol='BTCUSD',
            bid=1.0, ask=2.0, time_msc=1784737896774, collected_msc=1784737896900)

        assert tick_event_msc(tick) == 1784737896774

    def test_a_tick_without_a_venue_stamp_takes_its_timestamp_to_the_millisecond(self):
        tick = TickData(
            timestamp=datetime(2026, 4, 27, 7, 38, 8, 773_000, tzinfo=timezone.utc),
            symbol='BTCUSD', bid=1.0, ask=1.0)

        assert tick_event_msc(tick) == 1777275488773

    def test_the_receipt_stamp_is_the_collection_stamp(self):
        assert tick_receipt_msc(_tick(1_000, collected_msc=1_080)) == 1_080

    def test_a_tick_recorded_without_its_arrival_was_received_when_it_happened(self):
        assert tick_receipt_msc(_tick(1_000)) == 1_000

    def test_a_tick_with_neither_stamp_takes_its_timestamp_for_both(self):
        """No venue stamp and no arrival: the receipt falls back through the source stamp."""
        tick = TickData(
            timestamp=datetime(2026, 4, 27, 7, 38, 8, 773_000, tzinfo=timezone.utc),
            symbol='BTCUSD', bid=1.0, ask=1.0)

        assert tick_receipt_msc(tick) == 1777275488773
