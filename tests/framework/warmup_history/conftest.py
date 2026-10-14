"""
Fixtures for the warmup history suite: one synthetic tick stream, its archive, and the batch's
own preparator reading that archive.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from python.framework.data_preparation.shared_data_preparator import SharedDataPreparator
from tests.framework.warmup_history.warmup_history_builders import (
    ArchiveIndexStub,
    render_archive,
    tick_frame,
)

UTC = timezone.utc

# Two days of trades, one every 7 seconds — enough history for every requirement below, and a
# midnight to cross.
STREAM_START = datetime(2026, 8, 4, 0, 0, tzinfo=UTC)
STREAM_END = datetime(2026, 8, 6, 3, 0, tzinfo=UTC)
TIMEFRAMES = ('M1', 'M5', 'H1', 'D1')


@pytest.fixture(scope='module')
def stream():
    """The tick stream every path reads."""
    return tick_frame(STREAM_START, STREAM_END, step_s=7)


@pytest.fixture(scope='module')
def preparator(stream, tmp_path_factory):
    """The batch's own preparator, its bar index pointed at the stream's archive."""
    files = render_archive(stream, tmp_path_factory.mktemp('archive'), TIMEFRAMES)
    instance = SharedDataPreparator(MagicMock())
    instance.bar_index_manager = ArchiveIndexStub(files)
    return instance
