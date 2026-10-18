"""
Spread Estimate Measurement Tests (#602).

The measurement reads a symbol's tick files one at a time and files every figure under the year,
origin class and quote regime it belongs to. These tests write small tick files with a known
spread and hold it to what they contain: the pairs and their spread, a burst counted once, a
pair across the boundary of two files, and — where a file recorded a quote — the quoted and the
effective spread beside the estimate.
"""

from datetime import date, datetime, timezone
from typing import Dict, List

import pytest

from python.framework.discoveries.spread_estimate.spread_estimate_measurement import (
    SpreadEstimateMeasurement,
)
from python.framework.types.spread_estimate_types import QuoteRegime
from tests.shared.spread_tick_files import Trade, write_tick_file

# 2021-03-01 00:00:00 UTC
T0 = int(datetime(2021, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)

def _measure(*entries: Dict):
    return SpreadEstimateMeasurement().measure_entries('kraken_spot', 'ETHUSD', list(entries))


class TestTheEstimateFromTrades:

    def _trades(self) -> List[Trade]:
        return [
            (T0, 100.01, 'BUY'),
            (T0 + 100, 99.99, 'SELL'),       # pair: 2 bps
            (T0 + 300, 100.01, 'BUY'),       # pair: 2 bps
            (T0 + 300, 100.02, 'BUY'),       # same burst — collapsed
            (T0 + 5_300, 99.99, 'SELL'),     # 5 s later — no pair
        ]

    def test_pairs_and_their_spread_land_under_their_year_and_regime(self, tmp_path):
        row = _measure(write_tick_file(tmp_path, 'a.parquet', self._trades())).rows[0]
        assert (row.year, row.origin_class, row.regime) == (2021, 'external', QuoteRegime.NO_QUOTE)
        assert (row.trades, row.pairs) == (5, 2)
        assert row.estimate_mean_bps == pytest.approx(2.0, rel=1e-4)
        assert row.quoted_mean_bps is None

    def test_a_day_carries_its_own_estimate(self, tmp_path):
        days = _measure(write_tick_file(tmp_path, 'a.parquet', self._trades())).day_estimates
        assert [(d.day, d.pairs) for d in days] == [(date(2021, 3, 1), 2)]
        assert days[0].spread_mean_bps == pytest.approx(2.0, rel=1e-4)

    def test_a_pair_across_two_files_is_kept(self, tmp_path):
        first = write_tick_file(tmp_path, 'a.parquet', [(T0, 100.01, 'BUY')])
        second = write_tick_file(tmp_path, 'b.parquet', [(T0 + 200, 99.99, 'SELL')])
        measurement = _measure(second, first)           # out of order on purpose
        assert sum(row.pairs for row in measurement.rows) == 1


class TestTheQuotedTruthBesideIt:

    def test_a_quoted_file_reports_the_quoted_and_the_effective_spread(self, tmp_path):
        trades = [(T0, 100.01, 'BUY'), (T0 + 100, 99.99, 'SELL'), (T0 + 200, 100.03, 'BUY')]
        measurement = _measure(write_tick_file(tmp_path, 'q.parquet', trades, quote=(99.99, 100.01),
                                     origin_class='production'))
        row = measurement.rows[0]
        assert row.regime is QuoteRegime.QUOTED
        assert row.quoted_mean_bps == pytest.approx(2.0, rel=1e-4)
        # Effective: 2, 2 and 6 bps — the last fill went through the quote
        assert row.effective_median_bps == pytest.approx(2.0, rel=1e-4)
        assert measurement.validation_days[0].quoted_median_bps == pytest.approx(2.0, rel=1e-4)

    def test_an_unquoted_file_reports_no_truth(self, tmp_path):
        measurement = _measure(write_tick_file(tmp_path, 'a.parquet', [(T0, 100.0, 'BUY'),
                                                             (T0 + 100, 99.99, 'SELL')]))
        assert measurement.validation_days == []


def test_a_file_across_new_year_splits_its_figures_by_year(tmp_path):
    new_year = int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
    trades = [(new_year - 300, 100.01, 'BUY'), (new_year - 200, 99.99, 'SELL'),
              (new_year + 100, 100.01, 'BUY'), (new_year + 200, 99.99, 'SELL')]
    rows = _measure(write_tick_file(tmp_path, 'y.parquet', trades)).rows
    assert [(row.year, row.trades, row.pairs) for row in rows] == [(2021, 2, 1), (2022, 2, 2)]
