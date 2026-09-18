"""
FiniexTestingIDE - Trade ID Import Tests

The venue's per-pair sequential trade id (collector format 1.7.0+) survives the import.

It is kept for ONE question a timestamp cannot answer. A stretch with no ticks can mean the
market was quiet or that we did not receive what happened, and in time alone those are the same
bytes — the failure this project spent a day removing from its producers. With a sequential id
they separate, and the jump size is exactly how many trades are missing. Measured by the
collector across 311,436 consecutive pairs in the rehearsal archive: the id never failed to
increase, and every jump coincided with a logged reconnect or restart — none anywhere else.

Two properties carry the weight. It is NULLABLE, because a quote-driven venue has no central
place where trades happen and therefore no id to give (§31c) — absence is correct rather than
missing. And null is never zero: a zero is an id downstream, the same trap `last` and
`quote_age_ms` already carry.

Before this, the importer dropped every column it did not know, without a word. The file
imported cleanly and the completeness information was gone.
"""

from pathlib import Path

import pandas as pd
import pytest

from python.data_management.importers.tick_data_importer import TickDataImporter
from tests.data.import_pipeline.conftest import (
    build_minimal_tick_json,
    write_json_fixture,
)


def _import(tmp_path: Path, payload: dict, name: str) -> pd.DataFrame:
    """
    Run one file through the importer and read the written parquet back.

    Args:
        tmp_path: pytest temp directory
        payload: The tick JSON to import
        name: File name for the fixture

    Returns:
        The parquet as a DataFrame
    """
    source, target = tmp_path / 'raw', tmp_path / 'processed'
    write_json_fixture(source, name, payload)
    TickDataImporter(source_dir=str(source), target_dir=str(target), override=True,
                     move_processed_files=False, auto_render_bars=False).process_all_exports()
    written = [p for p in target.rglob('*.parquet') if 'index' not in p.name]
    assert written, 'the importer wrote nothing'
    return pd.read_parquet(written[0])


def _with_trade_ids(first: int, count: int = 5, **kw) -> dict:
    """
    A 1.7.0 payload whose ticks carry consecutive trade ids.

    Args:
        first: The first trade id
        count: How many ticks
        kw: Passed through to the builder

    Returns:
        The payload
    """
    payload = build_minimal_tick_json(
        tick_count=count, data_format_version='1.7.0', **kw)
    for offset, tick in enumerate(payload['ticks']):
        tick['trade_id'] = first + offset
    return payload


class TestTheTradeIdSurvivesTheImport:
    """It used to be dropped silently — the file imported and the information was gone."""

    def test_the_column_reaches_the_parquet(self, tmp_path):
        df = _import(tmp_path, _with_trade_ids(9_000_001), 'TESTUSD_20260115_100000_ticks.json')

        assert 'trade_id' in df.columns
        assert list(df['trade_id']) == [9_000_001 + i for i in range(5)]

    def test_a_value_int32_could_not_hold_survives_exactly(self, tmp_path):
        """
        A counter that never decreases outgrows int32, and the failure would be silent.

        int32 tops out at 2,147,483,647, so a wrap would read as a gap of two billion trades
        rather than as an overflow. Asserted on the VALUE rather than on the dtype: parquet
        normalises pandas' nullable Int64 back to plain int64 when a column holds no nulls, so
        the dtype is an implementation detail while the magnitude is the property.
        """
        beyond_int32 = 2_400_000_000

        df = _import(tmp_path, _with_trade_ids(beyond_int32),
                     'TESTUSD_20260115_100000_ticks.json')

        assert df['trade_id'].iloc[0] == beyond_int32
        assert list(df['trade_id']) == [beyond_int32 + i for i in range(5)]

    def test_a_jump_is_preserved_rather_than_smoothed(self, tmp_path):
        """
        The whole point: the JUMP SIZE is the count of trades we did not receive.

        An importer that renumbered, reindexed or filled would destroy exactly the quantity
        the field exists to carry.
        """
        payload = build_minimal_tick_json(tick_count=4, data_format_version='1.7.0')
        for tick, tid in zip(payload['ticks'], [500, 501, 617, 618]):
            tick['trade_id'] = tid

        df = _import(tmp_path, payload, 'TESTUSD_20260115_100000_ticks.json')

        assert list(df['trade_id']) == [500, 501, 617, 618]
        assert int(df['trade_id'].diff().max()) == 116


class TestAVenueWithoutTradeIdsIsNotBroken:
    """A quote-driven venue has no central place where trades happen (§31c)."""

    def test_a_file_without_the_field_imports_and_reports_null(self, tmp_path):
        """
        MT5 forex is the case. Absence is the correct answer, not a gap to fill.

        Asserted as NULL rather than zero, because a zero is an id downstream — the same trap
        `last` and `quote_age_ms` already carry on this wire.
        """
        payload = build_minimal_tick_json(tick_count=3, data_format_version='1.5.0')

        df = _import(tmp_path, payload, 'TESTUSD_20260115_100000_ticks.json')

        if 'trade_id' in df.columns:
            assert df['trade_id'].isna().all()
            assert not (df['trade_id'] == 0).any()

    @pytest.mark.parametrize('version', ['1.0.5', '1.3.0', '1.5.0'])
    def test_every_legacy_format_still_imports(self, tmp_path, version):
        payload = build_minimal_tick_json(tick_count=3, data_format_version=version)

        df = _import(tmp_path, payload, 'TESTUSD_20260115_100000_ticks.json')

        assert len(df) == 3
