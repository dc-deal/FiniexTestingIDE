"""
FiniexTestingIDE - Signal Import Test Fixtures (#429)

Imports the multi-symbol signal fixture JSONL into a temp parquet tree + index once per
module, so the import / index / reader / v0-parity tests share one prepared source.
"""

import shutil
from pathlib import Path

import pytest

from python.data_management.importers.signal_importer import SignalDataImporter
from python.data_management.index.signal_index_manager import SignalIndexManager

# tests/fixtures/signals/signal_import_sample.jsonl (pipeline_id = 'test_sentiment')
FIXTURE_JSONL = (
    Path(__file__).resolve().parents[2] / 'fixtures' / 'signals' / 'signal_import_sample.jsonl'
)
DATA_SENTIMENT_TYPE = 'test_sentiment'


@pytest.fixture(scope='module')
def imported_signals(tmp_path_factory):
    """Import the fixture JSONL → parquet + index in a temp tree."""
    root = tmp_path_factory.mktemp('signal_import')
    raw_dir = root / 'raw' / DATA_SENTIMENT_TYPE
    raw_dir.mkdir(parents=True)
    shutil.copy(FIXTURE_JSONL, raw_dir / FIXTURE_JSONL.name)

    processed = root / 'processed'
    importer = SignalDataImporter(
        source_dir=str(root / 'raw'), target_dir=str(processed), override=True)
    importer.process_all_signals()

    index = SignalIndexManager(data_dir=str(processed))
    index.build_index(force_rebuild=True)

    return {
        'jsonl': FIXTURE_JSONL,
        'processed': processed,
        'index': index,
        'importer': importer,
        'parquet': processed / DATA_SENTIMENT_TYPE / f'{FIXTURE_JSONL.stem}.parquet',
    }
