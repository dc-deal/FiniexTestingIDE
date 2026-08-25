"""
FiniexTestingIDE - Signal Stream Identity Tests
Ordering, resolution gate and deduplication once a snapshot carries stream identity (#141 Part 2a).

The pre-stream archive carries no seq / stream_epoch / available_msc, so every rule here has a
documented fallback and the legacy behaviour must stay bit-identical — that is what the first
test group pins.
"""

from conftest import SYMBOL, make_provider, snapshot, utc

from python.framework.signal_data.signal_data_provider import SignalDataProvider
from python.framework.types.signal_data_types import SentimentResult, SignalSeries, SignalSnapshot


def stream_snapshot(
    collected_msc,
    seq: int,
    stream_epoch: int = 1,
    available_msc=None,
    signal: str = 'HOLD',
) -> SignalSnapshot:
    """Build a snapshot carrying stream identity."""
    return SignalSnapshot(
        collected_msc=collected_msc,
        available_msc=available_msc,
        seq=seq,
        stream_epoch=stream_epoch,
        schema_version='1.0',
        result=[SentimentResult(symbol=SYMBOL, signal=signal)],
    )


class TestPreStreamFallback:
    """Lines without stream identity resolve exactly as before."""

    def test_gate_falls_back_to_collected_msc(self):
        snap = snapshot(utc(2026, 7, 22, 10, 0), 0.5, 0.9)
        assert snap.get_resolution_key() == snap.collected_msc

    def test_order_key_sorts_by_time_ahead_of_any_epoch(self):
        legacy = snapshot(utc(2026, 7, 22, 10, 0), 0.5, 0.9)
        streamed = stream_snapshot(utc(2026, 7, 22, 9, 0), seq=1)
        assert legacy.get_order_key() < streamed.get_order_key()

    def test_lookup_unchanged(self):
        provider = make_provider(
            snapshot(utc(2026, 7, 22, 10, 0), 0.1, 0.5),
            snapshot(utc(2026, 7, 22, 10, 10), 0.2, 0.5),
        )
        resolved = provider.nearest(utc(2026, 7, 22, 10, 15), SYMBOL)
        assert resolved.collected_msc == utc(2026, 7, 22, 10, 10)


class TestResolutionGate:
    """available_msc gates visibility once the producer stamps it."""

    def test_available_msc_wins_over_collected_msc(self):
        snap = stream_snapshot(
            utc(2026, 8, 20, 12, 5), seq=1, available_msc=utc(2026, 8, 20, 12, 0))
        assert snap.get_resolution_key() == utc(2026, 8, 20, 12, 0)

    def test_snapshot_invisible_before_its_availability(self):
        provider = make_provider(stream_snapshot(
            utc(2026, 8, 20, 12, 5), seq=1, available_msc=utc(2026, 8, 20, 12, 0)))
        assert provider.nearest(utc(2026, 8, 20, 11, 59), SYMBOL) is None
        assert provider.nearest(utc(2026, 8, 20, 12, 1), SYMBOL) is not None

    def test_backwards_availability_stamp_is_clamped(self):
        """A producer-side clock correction must never make a snapshot visible earlier."""
        provider = make_provider(
            stream_snapshot(utc(2026, 8, 20, 12, 0), seq=4,
                            available_msc=utc(2026, 8, 20, 12, 0, 5)),
            stream_snapshot(utc(2026, 8, 20, 12, 0), seq=5,
                            available_msc=utc(2026, 8, 20, 12, 0, 3)),
        )
        assert provider.nearest(utc(2026, 8, 20, 12, 0, 4), SYMBOL) is None


class TestOrdering:
    """(stream_epoch, seq) is the series order; no clock takes part in it."""

    def test_seq_orders_within_an_epoch(self):
        early = stream_snapshot(utc(2026, 8, 20, 12, 0), seq=1)
        late = stream_snapshot(utc(2026, 8, 20, 12, 0), seq=2)
        assert early.get_order_key() < late.get_order_key()

    def test_epoch_outranks_seq(self):
        """A reset restarts seq, so the epoch has to decide first."""
        old_series = stream_snapshot(utc(2026, 8, 20, 12, 0), seq=1200, stream_epoch=1)
        new_series = stream_snapshot(utc(2026, 8, 20, 12, 0), seq=1, stream_epoch=2)
        assert old_series.get_order_key() < new_series.get_order_key()


class TestDeduplication:
    """The producer is at-least-once; a redelivered envelope must be a no-op."""

    def test_extend_adds_only_new_envelopes(self):
        provider = make_provider(stream_snapshot(utc(2026, 8, 20, 12, 0), seq=1))
        added = provider.extend([
            stream_snapshot(utc(2026, 8, 20, 12, 0), seq=1),
            stream_snapshot(utc(2026, 8, 20, 12, 10), seq=2),
        ])
        assert added == 1
        assert provider.get_snapshot_count() == 2

    def test_same_seq_in_a_different_epoch_is_a_different_envelope(self):
        """seq is unique within an epoch only — the pair is the identity."""
        provider = make_provider(stream_snapshot(utc(2026, 8, 20, 12, 0), seq=1051))
        added = provider.extend([
            stream_snapshot(utc(2026, 8, 20, 13, 0), seq=1051, stream_epoch=2)])
        assert added == 1
        assert provider.get_snapshot_count() == 2

    def test_extend_keeps_the_series_resolvable(self):
        provider = make_provider(stream_snapshot(
            utc(2026, 8, 20, 12, 0), seq=1, available_msc=utc(2026, 8, 20, 12, 0)))
        provider.extend([stream_snapshot(
            utc(2026, 8, 20, 12, 10), seq=2,
            available_msc=utc(2026, 8, 20, 12, 10), signal='SELL')])
        resolved = provider.nearest(utc(2026, 8, 20, 12, 15), SYMBOL)
        assert resolved.result.signal == 'SELL'

    def test_empty_extend_is_a_no_op(self):
        provider = make_provider(stream_snapshot(utc(2026, 8, 20, 12, 0), seq=1))
        assert provider.extend([]) == 0
        assert provider.get_snapshot_count() == 1


class TestSeriesMetadata:
    """The provider reports what it serves."""

    def test_signal_kind_is_exposed(self):
        provider = make_provider(stream_snapshot(utc(2026, 8, 20, 12, 0), seq=1))
        assert provider.get_signal_kind() == 'llm_sentiment'

    def test_empty_series_resolves_to_nothing(self):
        provider = SignalDataProvider(SignalSeries(signal_kind='k', snapshots=[]))
        assert provider.nearest(utc(2026, 8, 20, 12, 0), SYMBOL) is None
