"""
Test Data Deficiencies (#561).

What a tick file LACKS is measured from its rows when the tick index scans it — never inferred
from a format version — and every value of the closed vocabulary is declared beside it.
"""

import numpy as np
import pandas as pd

from python.data_management.importers.tick_data_importer import TickDataImporter
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.types.data_deficiency_types import (
    DEFICIENCY_DECLARATIONS,
    MISSING_TRADES_RUN_LIMIT_S,
    DataDeficiency,
)
from python.framework.utils.tick_deficiency_scan import measure_tick_deficiencies
from python.framework.validators.validation_check_catalog import VALIDATION_CHECKS
from tests.data.import_pipeline.conftest import build_minimal_tick_json, write_json_fixture

_START_MSC = 1_768_471_200_000  # 2026-01-15 10:00:00 UTC


def _frame(rows: int = 200, quoted: bool = True, lag_ms: int = 15) -> pd.DataFrame:
    """
    A recorded-looking tick frame: one tick every 400 ms, a real quote, a receive lag.

    Args:
        rows: Number of ticks
        quoted: Whether ask sits above bid
        lag_ms: Receive lag added to the event time

    Returns:
        The frame
    """
    event = _START_MSC + np.arange(rows, dtype=np.int64) * 400
    bid = np.full(rows, 100.0)
    return pd.DataFrame({
        'timestamp': pd.to_datetime(event, unit='ms'),
        'time_msc': event,
        'collected_msc': event + lag_ms + (np.arange(rows) % 7),
        'bid': bid,
        'ask': bid + (0.5 if quoted else 0.0),
    })


class TestTheVocabularyIsDeclared:

    def test_every_deficiency_is_declared(self):
        # A new value cannot arrive without saying how it is established and what it costs
        assert set(DEFICIENCY_DECLARATIONS) == set(DataDeficiency)

    def test_every_declared_check_is_in_the_catalog(self):
        catalog = {check.check for check in VALIDATION_CHECKS}
        for declaration in DEFICIENCY_DECLARATIONS.values():
            assert declaration.check in catalog


class TestWhatAFileLacksIsMeasured:

    def test_a_recorded_quoted_file_lacks_nothing(self):
        measurement = measure_tick_deficiencies(_frame())
        assert measurement.deficiencies == []
        assert measurement.trade_id_gaps is None   # no trade ids: not measurable, never zero

    def test_bid_equal_to_ask_throughout_is_no_quote(self):
        assert measure_tick_deficiencies(_frame(quoted=False)).deficiencies == [
            DataDeficiency.NO_QUOTE]

    def test_an_absent_or_zeroed_arrival_column_is_no_arrival_time(self):
        absent = _frame().drop(columns=['collected_msc'])
        zeroed = _frame().assign(collected_msc=0)
        for frame in (absent, zeroed):
            assert measure_tick_deficiencies(frame).deficiencies == [
                DataDeficiency.NO_ARRIVAL_TIME]

    def test_every_second_ending_on_999_is_a_reconstruction(self):
        # The MT5 reconstruction spread each second's ticks evenly over 0–999 ms
        frame = _frame(rows=60)
        seconds = (frame['time_msc'] // 1000).to_numpy()
        frame['collected_msc'] = [
            second * 1000 + 999 if index == len(seconds) - 1 or seconds[index + 1] != second
            else second * 1000 + 300
            for index, second in enumerate(seconds)]
        assert DataDeficiency.ARRIVAL_TIME_RECONSTRUCTED in measure_tick_deficiencies(
            frame).deficiencies

    def test_an_arrival_copied_from_the_event_time_is_a_reconstruction(self):
        # The Kraken reconstruction copied the event time for ticker ticks: no lag at all
        frame = _frame()
        frame.loc[frame.index % 5 != 0, 'collected_msc'] = frame['time_msc']
        assert DataDeficiency.ARRIVAL_TIME_RECONSTRUCTED in measure_tick_deficiencies(
            frame).deficiencies


class TestMissingTradesAreCountedAlways:

    def test_reconnect_noise_is_a_count_not_a_deficiency(self):
        frame = _frame(rows=1000)
        frame['trade_id'] = np.arange(1, 1001, dtype=np.int64)
        frame = frame[~frame['trade_id'].isin([100, 300, 500])]
        measurement = measure_tick_deficiencies(frame)

        assert measurement.trade_id_gaps.missing == 3
        assert measurement.trade_id_gaps.runs == 3
        assert DataDeficiency.MISSING_TRADES not in measurement.deficiencies

    def test_a_long_run_of_missing_ids_is_a_hole(self):
        # Twenty ids missing across more than five minutes: a real hole, not noise
        frame = _frame(rows=400)
        frame['trade_id'] = np.arange(1, 401, dtype=np.int64)
        frame.loc[frame.index >= 200, 'trade_id'] += 20
        later = (MISSING_TRADES_RUN_LIMIT_S + 60) * 1000
        frame.loc[frame.index >= 200, 'time_msc'] += int(later)
        measurement = measure_tick_deficiencies(frame)

        assert measurement.trade_id_gaps.longest_run == 20
        assert DataDeficiency.MISSING_TRADES in measurement.deficiencies


class TestTheIndexCarriesThem:

    def test_an_imported_file_without_quote_or_arrival_is_indexed_so(self, tmp_path):
        # Through the real import and a persisted index — a converted venue history looks
        # exactly like this: bid = ask = traded price, and no receive time at all
        source, target = tmp_path / 'source', tmp_path / 'target'
        data = build_minimal_tick_json(symbol='DOTUSD', broker_type='kraken_spot', tick_count=5,
                                       bid_start=5.0, ask_start=5.0)
        for tick in data['ticks']:
            del tick['collected_msc']
        write_json_fixture(source, 'DOTUSD_20260115_100000_ticks.json', data)
        TickDataImporter(source_dir=str(source), target_dir=str(target),
                         auto_render_bars=False).process_all_exports()

        reloaded = TickIndexManager(data_dir=str(target))
        reloaded.build_index()
        entry = reloaded.get_symbol_entries('kraken_spot', 'DOTUSD')[0]
        assert entry['deficiencies'] == [DataDeficiency.NO_QUOTE, DataDeficiency.NO_ARRIVAL_TIME]
        assert entry['trade_id_gaps'] is None
