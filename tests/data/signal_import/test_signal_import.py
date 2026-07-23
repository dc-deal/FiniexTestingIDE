"""
Signal import pipeline tests (#429).

Covers the JSONL → parquet import (explode + envelope sentinels), the signal index (sources,
symbols, coverage, range resolution), the projected parquet reader, and — the key guarantee —
bit-identical parity with the v0 JSONL path on the consumed fields (including partial/error
envelopes resolving to a defensive HOLD).
"""

import json
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pandas as pd
import pytest

from python.data_management.importers.signal_importer import SignalDataImporter
from python.framework.data_preparation.shared_data_preparator import SharedDataPreparator
from python.framework.exceptions.signal_data_errors import (
    SignalDataUnavailableError, SignalSchemaError)
from python.framework.signal_data.signal_data_provider import SignalDataProvider
from python.framework.signal_data.signal_jsonl_loader import load_signal_series
from python.framework.signal_data.signal_parquet_reader import load_signal_series_from_parquet
from python.framework.types.process_data_types import RequirementsMap, SignalRequirement
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.signal_data_types import (
    SIGNAL_ENVELOPE_SYMBOL, SignalParquetColumn)

BASE_MSC = 1768464000000   # 2026-01-15T08:00:00Z
STEP_MSC = 600000          # 10 min
START = datetime(2026, 1, 15, 7, 0, tzinfo=timezone.utc)
END = datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc)


def _consumed(resolved):
    """The worker-consumed fields of a resolved signal (None on a gap)."""
    if resolved is None:
        return None
    r = resolved.result
    return (
        int(resolved.collected_msc.timestamp() * 1000), r.signal,
        round(r.sentiment_score, 9), round(r.confidence, 9),
        r.reasoning, round(r.urgency, 9), r.is_breaking, r.basis,
    )


# ---------------------------------------------------------------- import / explode

def test_import_row_counts(imported_signals):
    df = pd.read_parquet(imported_signals['parquet'])
    # 6 envelopes → 6 sentinels + 5 BTCUSD (present 0,1,2,4,5) + 4 ETHUSD (present 0,1,4,5)
    assert len(df) == 15
    assert (df['symbol'] == SIGNAL_ENVELOPE_SYMBOL).sum() == 6
    assert (df['symbol'] == 'BTCUSD').sum() == 5
    assert (df['symbol'] == 'ETHUSD').sum() == 4


def test_parquet_schema_and_dtypes(imported_signals):
    df = pd.read_parquet(imported_signals['parquet'])
    assert list(df.columns) == [c.value for c in SignalParquetColumn]
    assert df['collected_msc'].dtype == 'int64'
    assert df['is_breaking'].dtype == 'bool'


def test_lean_projection_drops_heavy_provenance(imported_signals):
    df = pd.read_parquet(imported_signals['parquet'])
    cols = set(df.columns)
    # heavy provenance is NOT persisted — it lives in the raw JSONL archive (audit source)
    assert {'sources', 'metadata', 'errors', 'timestamp', 'outcome_type'}.isdisjoint(cols)
    # new fields ARE persisted: per-symbol basis + prompt provenance (traceability)
    assert {'basis', 'prompt_id', 'prompt_hash'} <= cols
    btc0 = df[(df['symbol'] == 'BTCUSD') & (df['collected_msc'] == BASE_MSC)].iloc[0]
    assert btc0['basis'] == 'llm'
    assert btc0['prompt_id'] == 'test-prompt'


# ---------------------------------------------------------------- index

def test_index_sources_and_symbols(imported_signals):
    idx = imported_signals['index']
    assert idx.list_sentiment_types() == ['test_sentiment']
    assert idx.list_symbols('test_sentiment') == ['BTCUSD', 'ETHUSD']


def test_index_coverage_spans_full_range(imported_signals):
    # ETHUSD is absent in 2 envelopes but the sentinels give it whole-file coverage
    cov = imported_signals['index'].get_symbol_file_coverage('test_sentiment', 'ETHUSD')
    assert cov['num_files'] == 1
    assert cov['start_time'][:10] == '2026-01-15'


def test_get_relevant_files_range(imported_signals):
    idx = imported_signals['index']
    assert len(idx.get_relevant_files('test_sentiment', 'BTCUSD', START, END)) == 1
    assert idx.get_relevant_files(
        'test_sentiment', 'BTCUSD',
        datetime(2020, 1, 1, tzinfo=timezone.utc),
        datetime(2020, 1, 2, tzinfo=timezone.utc)) == []


def test_unknown_symbol_returns_empty(imported_signals):
    assert imported_signals['index'].get_relevant_files(
        'test_sentiment', 'XRPUSD', START, END) == []


# ---------------------------------------------------------------- reader projection

def test_reader_projects_symbol(imported_signals):
    files = imported_signals['index'].get_relevant_files('test_sentiment', 'BTCUSD', START, END)
    series = load_signal_series_from_parquet(
        files, source='test', symbol='BTCUSD', start=START, end=END)
    # one snapshot per envelope; BTCUSD present in 5, empty (error) in 1
    assert len(series.snapshots) == 6
    present = [s for s in series.snapshots if s.result]
    assert len(present) == 5
    # projection: the audit-only sources are NOT loaded into the runtime series
    assert all(not s.result[0].sources for s in present)


# ---------------------------------------------------------------- v0 parity

@pytest.mark.parametrize('symbol', ['BTCUSD', 'ETHUSD'])
def test_v0_parity(imported_signals, symbol):
    v0 = SignalDataProvider(load_signal_series(
        imported_signals['jsonl'], source='x', start=START, end=END))
    files = imported_signals['index'].get_relevant_files('test_sentiment', symbol, START, END)
    parquet = SignalDataProvider(load_signal_series_from_parquet(
        files, source='x', symbol=symbol, start=START, end=END))

    t = START
    while t <= END:
        assert _consumed(v0.nearest(t, symbol)) == _consumed(parquet.nearest(t, symbol)), \
            f'parity mismatch for {symbol} at {t}'
        t += timedelta(minutes=3)


def test_defensive_hold_on_partial_and_error(imported_signals):
    # ETHUSD absent in envelope 2 (partial) + 3 (error) → defensive HOLD, confidence 0
    files = imported_signals['index'].get_relevant_files('test_sentiment', 'ETHUSD', START, END)
    parquet = SignalDataProvider(load_signal_series_from_parquet(
        files, source='x', symbol='ETHUSD', start=START, end=END))
    for i in (2, 3):
        t = datetime.fromtimestamp((BASE_MSC + i * STEP_MSC) / 1000, tz=timezone.utc)
        resolved = parquet.nearest(t, 'ETHUSD')
        assert resolved is not None
        assert resolved.result.signal == 'HOLD'
        assert resolved.result.confidence == 0.0


# ---------------------------------------------------------------- import guards

def test_mixed_pipeline_id_rejected(tmp_path):
    raw = tmp_path / 'raw' / 'mixed'
    raw.mkdir(parents=True)
    lines = [
        {'collected_msc': BASE_MSC, 'schema_version': '1.0',
         'pipeline_id': 'a', 'status': 'success', 'result': []},
        {'collected_msc': BASE_MSC + STEP_MSC, 'schema_version': '1.0',
         'pipeline_id': 'b', 'status': 'success', 'result': []},
    ]
    jsonl = raw / 'mixed.jsonl'
    jsonl.write_text('\n'.join(json.dumps(line) for line in lines))

    importer = SignalDataImporter(
        source_dir=str(tmp_path / 'raw'), target_dir=str(tmp_path / 'proc'), override=True)
    with pytest.raises(SignalSchemaError):
        importer.convert_jsonl_to_parquet(jsonl)


# ---------------------------------------------------------------- §33 availability

def test_no_overlap_raises_unavailable(imported_signals):
    # A scenario range entirely outside the signal coverage → SignalDataUnavailableError
    # (a per-scenario exclusion at the batch level, NOT a batch crash).
    prep = SharedDataPreparator(MagicMock())
    prep.signal_index_manager = imported_signals['index']

    req_map = RequirementsMap()
    req_map.add_signal_requirement(SignalRequirement(
        scenario_name='out_of_range', broker_type='kraken_spot', symbol='BTCUSD',
        source='llm_sentiment', data_sentiment_type='test_sentiment',
        start_time=datetime(2020, 1, 1, tzinfo=timezone.utc),
        end_time=datetime(2020, 1, 2, tzinfo=timezone.utc)))
    scenario = SingleScenario(
        name='out_of_range', scenario_index=0, symbol='BTCUSD',
        data_broker_type='kraken_spot',
        start_date=datetime(2020, 1, 1, tzinfo=timezone.utc))

    with pytest.raises(SignalDataUnavailableError):
        prep._load_signals_for_scenario(scenario, req_map)
