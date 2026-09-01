"""
Discovery-cache validity tests (#486 finding 57).

All three families wrote a `config_fingerprint` into every cache file and none of them read it
back. Their validity rested on the source bar file's mtime alone — and a config change moves no
bar file, so a cache built under different parameters read as valid indefinitely.

The concrete case, measured in the survey: `data_coverage.thresholds.short` decides every gap's
CATEGORY, and the category decides which scenarios `ScenarioDataValidator` excludes. Change the
threshold, and yesterday's categories keep being served.
"""

from pathlib import Path
from typing import Tuple

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from python.framework.discoveries.data_coverage.data_coverage_report_cache import (
    DataCoverageReportCache,
)
from python.framework.discoveries.discovery_cache import DiscoveryCache
from python.framework.discoveries.volatility_profile_analyzer.volatility_profile_analyzer_cache import (
    VolatilityProfileAnalyzerCache,
)
from python.framework.utils.config_fingerprint_utils import read_cache_metadata


def _plant(path: Path, fingerprint: str, source_mtime: float, extra=None) -> None:
    """Write a cache file carrying the metadata a validity check reads."""
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(pd.DataFrame([{'x': 1}]), preserve_index=False)
    metadata = {
        b'config_fingerprint': fingerprint.encode(),
        b'source_bar_mtime': str(source_mtime).encode(),
    }
    metadata.update(extra or {})
    pq.write_table(table.replace_schema_metadata(metadata), path)


class _Bars:
    """A stand-in bar index: one known symbol, one mtime we control."""

    def __init__(self, mtime_path: Path):
        self._path = mtime_path

    def get_bar_file(self, broker_type, symbol, granularity=None):
        return self._path


@pytest.fixture
def source(tmp_path) -> Tuple[Path, float]:
    """A source 'bar file' whose mtime the caches compare against."""
    path = tmp_path / 'source_BARS.parquet'
    path.write_bytes(b'x')
    return path, path.stat().st_mtime


def _wire(cache, tmp_path, source_path):
    """Point a cache at a temp directory and a controllable source."""
    cache.cache_dir = tmp_path / 'cache'
    cache.cache_dir.mkdir(parents=True, exist_ok=True)
    cache._bar_index = _Bars(source_path)
    return cache


class TestConfigFingerprintInvalidation:
    """A config change must invalidate, even though no source file moved."""

    @pytest.mark.parametrize('build,call', [
        (DiscoveryCache, lambda c: c.is_cache_valid('mt5', 'EURUSD', 'extreme_moves')),
        (DataCoverageReportCache, lambda c: c.is_cache_valid('mt5', 'EURUSD')),
        (VolatilityProfileAnalyzerCache, lambda c: c.is_cache_valid('mt5', 'EURUSD')),
    ])
    def test_a_matching_fingerprint_is_valid_and_a_changed_one_is_not(
            self, build, call, tmp_path, source):
        source_path, source_mtime = source
        cache = _wire(build(), tmp_path, source_path)

        extra = {b'granularity': cache._granularity.encode()} \
            if isinstance(cache, DataCoverageReportCache) else None
        target = cache._get_cache_path('mt5', 'EURUSD', 'extreme_moves') \
            if isinstance(cache, DiscoveryCache) else cache._get_cache_path('mt5', 'EURUSD')

        _plant(target, 'FINGERPRINT_A', source_mtime + 10, extra)

        cache._live_fingerprint = 'FINGERPRINT_A'
        assert call(cache) is True, 'a cache built under the current config must stay valid'

        cache._live_fingerprint = 'FINGERPRINT_B'
        assert call(cache) is False, (
            'a config change moves no bar file — mtime alone would keep serving this')

    def test_a_cache_without_a_fingerprint_is_not_valid(self, tmp_path, source):
        """Pre-fingerprint files cannot vouch for themselves and must be rebuilt."""
        source_path, source_mtime = source
        cache = _wire(DiscoveryCache(), tmp_path, source_path)
        target = cache._get_cache_path('mt5', 'EURUSD', 'extreme_moves')

        table = pa.Table.from_pandas(pd.DataFrame([{'x': 1}]), preserve_index=False)
        pq.write_table(
            table.replace_schema_metadata({b'source_bar_mtime': str(source_mtime + 10).encode()}),
            target)

        cache._live_fingerprint = 'FINGERPRINT_A'
        assert cache.is_cache_valid('mt5', 'EURUSD', 'extreme_moves') is False


class TestMetadataReader:
    """Both fields come out of ONE file open — the difference between free and doubled."""

    def test_it_returns_every_key_decoded(self, tmp_path):
        path = tmp_path / 'c.parquet'
        _plant(path, 'FP', 123.5, {b'granularity': b'M5'})
        metadata = read_cache_metadata(path)
        assert metadata['config_fingerprint'] == 'FP'
        assert metadata['source_bar_mtime'] == '123.5'
        assert metadata['granularity'] == 'M5'

    def test_a_missing_file_is_none_rather_than_an_error(self, tmp_path):
        assert read_cache_metadata(tmp_path / 'nope.parquet') is None
