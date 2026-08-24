"""
Signal import pipeline tests (#429).

Covers the JSONL → parquet import (explode + envelope sentinels), the signal index (sources,
symbols, coverage, range resolution), the projected parquet reader, and — the key guarantee —
bit-identical parity with the v0 JSONL path on the consumed fields (including partial/error
envelopes resolving to a defensive HOLD).
"""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from python.data_management.importers.signal_data_importer import SignalDataImporter
from python.data_management.index.signal_index_manager import SignalIndexManager
from python.framework.data_preparation.shared_data_preparator import SharedDataPreparator
from python.framework.exceptions.signal_data_errors import (
    SignalDataUnavailableError,
    SignalSchemaError,
)
from python.framework.signal_data.signal_data_provider import SignalDataProvider
from python.framework.signal_data.signal_jsonl_loader import load_signal_series
from python.framework.signal_data.signal_parquet_reader import load_signal_series_from_parquet
from python.framework.types.process_data_types import RequirementsMap, SignalRequirement
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.signal_data_types import SIGNAL_ENVELOPE_SYMBOL, SignalParquetColumn

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


# ------------------------------------------------- index: the preceding bucket (#447)

BUCKET_SYMBOL = 'BTCUSD'


@pytest.fixture(scope='module')
def two_bucket_index(tmp_path_factory):
    """
    A two-day archive, each day a bucket on a 10-minute grid.

    Day 1: 2026-03-01 00:00 → 23:50    Day 2: 2026-03-02 00:00:30 → 23:50:30

    Day 2's first snapshot sits 30s AFTER midnight (the producer stamps after the
    bar close), so a window opening at 2026-03-02 00:00:00 has nothing of its own
    to resolve — exactly the real-archive shape.
    """
    root = tmp_path_factory.mktemp('two_bucket') / 'signals' / 'bucket_source'
    root.mkdir(parents=True)

    for day, offset_s in ((1, 0), (2, 30)):
        rows = []
        for i in range(144):
            moment = datetime(2026, 3, day, 0, 0, tzinfo=timezone.utc) + \
                timedelta(minutes=10 * i, seconds=offset_s)
            msc = int(moment.timestamp() * 1000)
            for symbol in (SIGNAL_ENVELOPE_SYMBOL, BUCKET_SYMBOL):
                rows.append({
                    SignalParquetColumn.COLLECTED_MSC.value: msc,
                    SignalParquetColumn.SYMBOL.value: symbol,
                    SignalParquetColumn.PIPELINE_ID.value: 'bucket_source',
                    # The reader projects the full runtime set — a fixture that
                    # only satisfies the index would fail on read.
                    SignalParquetColumn.SIGNAL.value: 'HOLD',
                    SignalParquetColumn.SENTIMENT_SCORE.value: 0.0,
                    SignalParquetColumn.CONFIDENCE.value: 0.5,
                    SignalParquetColumn.REASONING.value: '',
                    SignalParquetColumn.URGENCY.value: 0.0,
                    SignalParquetColumn.IS_BREAKING.value: False,
                    SignalParquetColumn.BASIS.value: 'llm',
                    SignalParquetColumn.STATUS.value: 'success',
                    SignalParquetColumn.SCHEMA_VERSION.value: '1.0',
                })
        pd.DataFrame(rows).to_parquet(root / f'2026-03-0{day}.parquet')

    index = SignalIndexManager(data_dir=str(root.parent))
    index.build_index(force_rebuild=True)
    return index


def _bucket_names(paths):
    """Bucket file stems, in the returned order."""
    return [p.stem for p in paths]


def test_window_at_day_boundary_pulls_the_preceding_bucket(two_bucket_index):
    # Day 2 opens at 00:00:30 — a window at 00:00:00 needs day 1 to resolve tick 1
    files = two_bucket_index.get_relevant_files(
        'bucket_source', BUCKET_SYMBOL,
        datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc))

    assert _bucket_names(files) == ['2026-03-01', '2026-03-02']


def test_window_inside_a_bucket_needs_no_predecessor(two_bucket_index):
    files = two_bucket_index.get_relevant_files(
        'bucket_source', BUCKET_SYMBOL,
        datetime(2026, 3, 2, 6, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc))

    assert _bucket_names(files) == ['2026-03-02']


def test_window_before_the_archive_has_no_predecessor(two_bucket_index):
    files = two_bucket_index.get_relevant_files(
        'bucket_source', BUCKET_SYMBOL,
        datetime(2026, 2, 28, 0, 0, tzinfo=timezone.utc),
        datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc))

    assert _bucket_names(files) == ['2026-03-01']


def test_first_tick_resolves_a_signal(two_bucket_index):
    """The guarantee the preceding bucket exists for: tick 1 is never blind."""
    window_start = datetime(2026, 3, 2, 0, 0, tzinfo=timezone.utc)
    files = two_bucket_index.get_relevant_files(
        'bucket_source', BUCKET_SYMBOL, window_start,
        datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc))

    provider = SignalDataProvider(load_signal_series_from_parquet(
        files, signal_kind='x', symbol=BUCKET_SYMBOL, start=window_start,
        end=datetime(2026, 3, 2, 12, 0, tzinfo=timezone.utc)))

    resolved = provider.nearest(window_start, BUCKET_SYMBOL)
    assert resolved is not None, 'first tick must resolve the last known signal'
    # The 23:50 snapshot of the previous day — 10 minutes old, not a gap
    assert resolved.collected_msc == datetime(
        2026, 3, 1, 23, 50, tzinfo=timezone.utc)


# ---------------------------------------------------------------- reader projection

def test_reader_projects_symbol(imported_signals):
    files = imported_signals['index'].get_relevant_files('test_sentiment', 'BTCUSD', START, END)
    series = load_signal_series_from_parquet(
        files, signal_kind='test', symbol='BTCUSD', start=START, end=END)
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
        imported_signals['jsonl'], signal_kind='x', start=START, end=END))
    files = imported_signals['index'].get_relevant_files('test_sentiment', symbol, START, END)
    parquet = SignalDataProvider(load_signal_series_from_parquet(
        files, signal_kind='x', symbol=symbol, start=START, end=END))

    t = START
    while t <= END:
        assert _consumed(v0.nearest(t, symbol)) == _consumed(parquet.nearest(t, symbol)), \
            f'parity mismatch for {symbol} at {t}'
        t += timedelta(minutes=3)


def test_defensive_hold_on_partial_and_error(imported_signals):
    # ETHUSD absent in envelope 2 (partial) + 3 (error) → defensive HOLD, confidence 0
    files = imported_signals['index'].get_relevant_files('test_sentiment', 'ETHUSD', START, END)
    parquet = SignalDataProvider(load_signal_series_from_parquet(
        files, signal_kind='x', symbol='ETHUSD', start=START, end=END))
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


# ---------------------------------------------------------------- finished archive

class TestFinishedArchive:
    """
    An imported JSONL moves to the finished archive, so the raw directory holds only
    what still needs importing. The raw JSONL is the audit source (sources / metadata /
    errors survive nowhere else), so this is a move, never a delete.
    """

    def _tree(self, tmp_path, name: str = 'day.jsonl', source: str = 'sentiment_a'):
        """A raw tree holding one valid one-envelope JSONL under a source folder."""
        raw = tmp_path / 'raw' / source
        raw.mkdir(parents=True)
        line = {'collected_msc': BASE_MSC, 'schema_version': '1.0',
                'pipeline_id': source, 'status': 'success', 'result': []}
        (raw / name).write_text(json.dumps(line))
        return tmp_path / 'raw', tmp_path / 'proc', tmp_path / 'finished'

    def _importer(self, raw, proc, finished, override: bool = False):
        return SignalDataImporter(
            source_dir=str(raw), target_dir=str(proc), override=override,
            finished_dir=str(finished) if finished else None)

    def test_imported_file_moves_and_keeps_its_structure(self, tmp_path):
        raw, proc, finished = self._tree(tmp_path)
        self._importer(raw, proc, finished).process_all_signals()

        assert not (raw / 'sentiment_a' / 'day.jsonl').exists()
        assert (finished / 'sentiment_a' / 'day.jsonl').exists()
        assert (proc / 'sentiment_a' / 'day.parquet').exists()

    def test_without_finished_dir_the_file_stays(self, tmp_path):
        raw, proc, _ = self._tree(tmp_path)
        self._importer(raw, proc, None).process_all_signals()

        assert (raw / 'sentiment_a' / 'day.jsonl').exists()

    def test_rerun_without_override_finds_nothing_and_reports_no_error(self, tmp_path):
        raw, proc, finished = self._tree(tmp_path)
        self._importer(raw, proc, finished).process_all_signals()

        # The whole point: a second run costs nothing instead of raising FileExistsError
        # on every already-imported file.
        again = self._importer(raw, proc, finished)
        again.process_all_signals()
        assert again.processed_files == 0
        assert again.errors == []

    def test_override_re_reads_the_finished_archive(self, tmp_path):
        raw, proc, finished = self._tree(tmp_path)
        self._importer(raw, proc, finished).process_all_signals()
        (proc / 'sentiment_a' / 'day.parquet').unlink()

        # 'override' means rebuilding what is already imported — which now lives in
        # the archive, so that is where it must be read from.
        rebuild = self._importer(raw, proc, finished, override=True)
        rebuild.process_all_signals()
        assert rebuild.processed_files == 1
        assert (proc / 'sentiment_a' / 'day.parquet').exists()
        assert (finished / 'sentiment_a' / 'day.jsonl').exists()

    def test_a_re_exported_day_supersedes_its_archived_copy(self, tmp_path):
        raw, proc, finished = self._tree(tmp_path)
        self._importer(raw, proc, finished).process_all_signals()

        # Same relative path back in raw — the newer export must win, and exactly one
        # import must run (not two, which would make the outcome order-dependent).
        (raw / 'sentiment_a').mkdir(parents=True, exist_ok=True)
        line = {'collected_msc': BASE_MSC + STEP_MSC, 'schema_version': '1.0',
                'pipeline_id': 'sentiment_a', 'status': 'success', 'result': []}
        (raw / 'sentiment_a' / 'day.jsonl').write_text(json.dumps(line))

        rerun = self._importer(raw, proc, finished, override=True)
        rerun.process_all_signals()
        assert rerun.processed_files == 1

        archived = json.loads((finished / 'sentiment_a' / 'day.jsonl').read_text())
        assert archived['collected_msc'] == BASE_MSC + STEP_MSC

    def test_a_failed_import_leaves_the_file_in_place(self, tmp_path):
        raw, proc, finished = self._tree(tmp_path)
        lines = [
            {'collected_msc': BASE_MSC, 'schema_version': '1.0',
             'pipeline_id': 'a', 'status': 'success', 'result': []},
            {'collected_msc': BASE_MSC + STEP_MSC, 'schema_version': '1.0',
             'pipeline_id': 'b', 'status': 'success', 'result': []},
        ]
        (raw / 'sentiment_a' / 'day.jsonl').write_text(
            '\n'.join(json.dumps(line) for line in lines))

        importer = self._importer(raw, proc, finished)
        importer.process_all_signals()

        assert importer.errors
        assert (raw / 'sentiment_a' / 'day.jsonl').exists()
        assert not (finished / 'sentiment_a' / 'day.jsonl').exists()

    def test_emptied_pipeline_folder_is_removed(self, tmp_path):
        """An inbox whose files all moved must look empty, not occupied."""
        raw, proc, finished = self._tree(tmp_path)
        importer = self._importer(raw, proc, finished)
        importer.process_all_signals()

        assert not (raw / 'sentiment_a').exists()
        assert raw.exists()
        assert importer.pruned_dirs == 1

    def test_folder_with_unimported_content_survives(self, tmp_path):
        """rmdir refuses a non-empty folder — anything left behind keeps its home."""
        raw, proc, finished = self._tree(tmp_path)
        (raw / 'sentiment_a' / 'notes.txt').write_text('not a jsonl')

        importer = self._importer(raw, proc, finished)
        importer.process_all_signals()

        assert (raw / 'sentiment_a' / 'notes.txt').exists()
        assert importer.pruned_dirs == 0

    def test_failed_import_keeps_its_folder(self, tmp_path):
        """The file stays on failure (previous test), so its folder must stay too."""
        raw, proc, finished = self._tree(tmp_path)
        lines = [
            {'collected_msc': BASE_MSC, 'schema_version': '1.0',
             'pipeline_id': 'a', 'status': 'success', 'result': []},
            {'collected_msc': BASE_MSC + STEP_MSC, 'schema_version': '1.0',
             'pipeline_id': 'b', 'status': 'success', 'result': []},
        ]
        (raw / 'sentiment_a' / 'day.jsonl').write_text(
            '\n'.join(json.dumps(line) for line in lines))

        importer = self._importer(raw, proc, finished)
        importer.process_all_signals()

        assert importer.errors
        assert (raw / 'sentiment_a').exists()

    def test_stale_empty_folder_is_removed_on_an_idle_run(self, tmp_path):
        """Nothing to import is not a reason to leave scaffolding behind."""
        raw, proc, finished = self._tree(tmp_path)
        self._importer(raw, proc, finished).process_all_signals()
        (raw / 'left_over').mkdir()

        importer = self._importer(raw, proc, finished)
        importer.process_all_signals()

        assert not (raw / 'left_over').exists()


# ---------------------------------------------------------------- §33 availability

def test_no_overlap_raises_unavailable(imported_signals):
    # A scenario range entirely outside the signal coverage → SignalDataUnavailableError
    # (a per-scenario exclusion at the batch level, NOT a batch crash).
    prep = SharedDataPreparator(MagicMock())
    prep.signal_index_manager = imported_signals['index']

    req_map = RequirementsMap()
    req_map.add_signal_requirement(SignalRequirement(
        scenario_name='out_of_range', broker_type='kraken_spot', symbol='BTCUSD',
        signal_kind='llm_sentiment', data_sentiment_type='test_sentiment',
        start_time=datetime(2020, 1, 1, tzinfo=timezone.utc),
        end_time=datetime(2020, 1, 2, tzinfo=timezone.utc)))
    scenario = SingleScenario(
        name='out_of_range', scenario_index=0, symbol='BTCUSD',
        data_broker_type='kraken_spot',
        start_date=datetime(2020, 1, 1, tzinfo=timezone.utc))

    with pytest.raises(SignalDataUnavailableError):
        prep._load_signals_for_scenario(scenario, req_map)
