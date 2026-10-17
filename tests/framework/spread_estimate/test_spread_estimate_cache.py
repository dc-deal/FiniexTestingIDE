"""
Spread Estimate Cache Tests (#602).

The day estimates a backtest prices its fills from are cached per symbol and measured only from
the files that recorded no quote. These tests hold the cache to its validity rule — the set of
those files and the estimator's version, nothing else — and to its place in the discovery index,
which every cache write now keeps current.
"""

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List

import pytest

from python.framework.discoveries.discovery_cache_index import DiscoveryCacheIndex
from python.framework.discoveries.spread_estimate import spread_estimate_cache
from python.framework.discoveries.spread_estimate.spread_estimate_cache import (
    SpreadEstimateCache,
)
from python.framework.discoveries.spread_estimate.spread_estimate_measurement import (
    SpreadEstimateMeasurement,
)
from tests.shared.spread_tick_files import Trade, write_tick_file

# 2021-03-01 00:00:00 UTC
T0 = int(datetime(2021, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)
DAY_MS = 86_400_000


class _TickIndex:
    """The three tick index reads the cache makes, answered from a dict."""

    def __init__(self, entries: Dict[str, Dict[str, List[Dict]]]):
        self._entries = entries

    def get_symbol_entries(self, broker_type: str, symbol: str) -> List[Dict]:
        return self._entries.get(broker_type, {}).get(symbol, [])

    def list_broker_types(self) -> List[str]:
        return sorted(self._entries)

    def list_symbols(self, broker_type: str) -> List[str]:
        return sorted(self._entries.get(broker_type, {}))


def _trades(day: int, spread: float) -> List[Trade]:
    """Two pairs straddling a known spread on one UTC day."""
    t = T0 + day * DAY_MS
    mid = 100.0
    return [(t, mid + spread / 2, 'BUY'), (t + 100, mid - spread / 2, 'SELL'),
            (t + 300, mid + spread / 2, 'BUY')]


@pytest.fixture
def setup(tmp_path):
    """A cache directory inside a discovery-cache root, and a symbol with two days."""
    ticks = tmp_path / 'ticks'
    ticks.mkdir()
    cache_dir = tmp_path / 'discovery_caches' / 'spread_estimate_cache'
    entries = {'kraken_spot': {'ETHUSD': [
        write_tick_file(ticks, 'd0.parquet', _trades(0, 0.02)),
        write_tick_file(ticks, 'd1.parquet', _trades(1, 0.04)),
    ]}}
    return ticks, cache_dir, entries


def _cache(cache_dir: Path, entries: Dict) -> SpreadEstimateCache:
    return SpreadEstimateCache(tick_index=_TickIndex(entries), cache_dir=cache_dir)


class TestTheCacheHoldsTheDayEstimates:

    def test_a_built_cache_reads_back_what_was_measured(self, setup):
        _, cache_dir, entries = setup
        built = _cache(cache_dir, entries).get_day_estimates('kraken_spot', 'ETHUSD')
        read = _cache(cache_dir, entries).get_day_estimates('kraken_spot', 'ETHUSD')

        assert [e.day for e in read] == [date(2021, 3, 1), date(2021, 3, 2)]
        assert [e.pairs for e in read] == [2, 2]
        assert read[0].spread_mean_bps == pytest.approx(2.0, rel=1e-4)
        assert read[1].spread_mean_bps == pytest.approx(4.0, rel=1e-4)
        assert [e.spread_mean_bps for e in read] == pytest.approx(
            [e.spread_mean_bps for e in built])

    def test_a_valid_cache_is_read_without_measuring(self, setup, monkeypatch):
        _, cache_dir, entries = setup
        _cache(cache_dir, entries).get_day_estimates('kraken_spot', 'ETHUSD')

        def _no_measuring(*args, **kwargs):
            raise AssertionError('a valid cache measured the history again')

        monkeypatch.setattr(SpreadEstimateMeasurement, 'measure_entries', _no_measuring)
        assert len(_cache(cache_dir, entries).get_day_estimates('kraken_spot', 'ETHUSD')) == 2

    def test_a_symbol_whose_every_file_has_a_quote_caches_nothing(self, setup):
        ticks, cache_dir, entries = setup
        entries['kraken_spot']['BTCUSD'] = [
            write_tick_file(ticks, 'q.parquet', _trades(0, 0.02), quote=(99.99, 100.01))]
        cache = _cache(cache_dir, entries)

        assert cache.get_day_estimates('kraken_spot', 'BTCUSD') == []
        assert not (cache_dir / 'kraken_spot_BTCUSD.parquet').exists()


class TestItsValidityIsTheSourceAndTheMethod:

    def test_a_new_file_without_a_quote_makes_it_invalid(self, setup):
        ticks, cache_dir, entries = setup
        _cache(cache_dir, entries).get_day_estimates('kraken_spot', 'ETHUSD')
        entries['kraken_spot']['ETHUSD'].append(
            write_tick_file(ticks, 'd2.parquet', _trades(2, 0.06)))

        cache = _cache(cache_dir, entries)
        assert not cache.is_cache_valid('kraken_spot', 'ETHUSD')
        assert len(cache.get_day_estimates('kraken_spot', 'ETHUSD')) == 3

    def test_a_new_quoted_file_leaves_it_valid(self, setup):
        ticks, cache_dir, entries = setup
        _cache(cache_dir, entries).get_day_estimates('kraken_spot', 'ETHUSD')
        entries['kraken_spot']['ETHUSD'].append(
            write_tick_file(ticks, 'q.parquet', _trades(5, 0.02), quote=(99.99, 100.01)))

        assert _cache(cache_dir, entries).is_cache_valid('kraken_spot', 'ETHUSD'), (
            'an import of quoted data must not re-measure the history')

    def test_a_changed_file_makes_it_invalid(self, setup):
        ticks, cache_dir, entries = setup
        _cache(cache_dir, entries).get_day_estimates('kraken_spot', 'ETHUSD')
        entries['kraken_spot']['ETHUSD'][1] = write_tick_file(
            ticks, 'd1.parquet', _trades(1, 0.04)[:2])

        assert not _cache(cache_dir, entries).is_cache_valid('kraken_spot', 'ETHUSD')

    def test_a_file_re_imported_with_other_values_makes_it_invalid(self, setup):
        """A re-import that changes only values keeps name, count and range; the importer's
        stamp is what tells the file is new."""
        ticks, cache_dir, entries = setup
        _cache(cache_dir, entries).get_day_estimates('kraken_spot', 'ETHUSD')
        entries['kraken_spot']['ETHUSD'][1] = {
            **write_tick_file(ticks, 'd1.parquet', _trades(1, 0.05)),
            'processed_at': '2026-10-17T12:00:00+00:00'}

        assert not _cache(cache_dir, entries).is_cache_valid('kraken_spot', 'ETHUSD')

    def test_a_new_estimator_version_makes_it_invalid(self, setup, monkeypatch):
        _, cache_dir, entries = setup
        _cache(cache_dir, entries).get_day_estimates('kraken_spot', 'ETHUSD')
        monkeypatch.setattr(spread_estimate_cache, 'ESTIMATOR_VERSION',
                            spread_estimate_cache.ESTIMATOR_VERSION + 1)

        assert not _cache(cache_dir, entries).is_cache_valid('kraken_spot', 'ETHUSD')


class TestBuildingEverySymbol:

    def test_only_symbols_with_a_file_without_a_quote_are_built(self, setup):
        ticks, cache_dir, entries = setup
        entries['kraken_spot']['BTCUSD'] = [
            write_tick_file(ticks, 'q.parquet', _trades(0, 0.02), quote=(99.99, 100.01))]

        assert _cache(cache_dir, entries).build_all() == {
            'generated': 1, 'skipped': 0, 'failed': 0}
        assert _cache(cache_dir, entries).build_all() == {
            'generated': 0, 'skipped': 1, 'failed': 0}


class TestTheDiscoveryIndexKeepsUp:

    def test_a_cache_written_on_demand_is_entered_in_the_index(self, setup):
        _, cache_dir, entries = setup
        index = DiscoveryCacheIndex(cache_dir.parent)
        index.rebuild()
        _cache(cache_dir, entries).get_day_estimates('kraken_spot', 'ETHUSD')

        rows = index.read()
        assert list(rows['file']) == ['kraken_spot_ETHUSD.parquet']
        assert list(rows['family']) == ['spread_estimate_cache']
        assert index.staleness_reason() is None, 'the write left the index behind'

    def test_a_rewritten_file_keeps_one_row(self, setup):
        _, cache_dir, entries = setup
        DiscoveryCacheIndex(cache_dir.parent).rebuild()
        _cache(cache_dir, entries).get_day_estimates('kraken_spot', 'ETHUSD')
        _cache(cache_dir, entries).get_day_estimates('kraken_spot', 'ETHUSD', force_rebuild=True)

        assert len(DiscoveryCacheIndex(cache_dir.parent).read()) == 1

    def test_an_index_already_behind_stays_reported_as_behind(self, setup):
        """One row recorded does not vouch for the files the index never saw."""
        _, cache_dir, entries = setup
        (cache_dir.parent / 'other_family').mkdir(parents=True)
        (cache_dir.parent / 'other_family' / 'x.parquet').write_bytes(b'')
        _cache(cache_dir, entries).get_day_estimates('kraken_spot', 'ETHUSD')

        assert DiscoveryCacheIndex(cache_dir.parent).staleness_reason() is not None
