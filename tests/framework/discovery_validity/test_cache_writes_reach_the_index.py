"""
Discovery-cache writes reach the index (#602).

A family that writes its cache on demand used to leave the discovery index describing the
generation before it, and the store catalog reported a stale index nobody had asked to rebuild.
Every family now writes through `DiscoveryCacheIndex.write_cache_file`, which enters the file in
the same step. These tests hold that method to its promise, and hold every module under
`python/framework/discoveries/` to writing its parquet files through it — a family that wrote one
past it would bring the stale index back with the suite green.
"""

import ast
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest

from python.framework.discoveries.discovery_cache_index import DiscoveryCacheIndex

_DISCOVERIES = Path('python/framework/discoveries')
_INDEX_UNIT = _DISCOVERIES / 'discovery_cache_index.py'
# The calls that put a parquet file on disk
_PARQUET_WRITES = {'write_table', 'to_parquet'}


def _table(value: int) -> pa.Table:
    return pa.Table.from_pandas(pd.DataFrame([{'x': value}]), preserve_index=False)


@pytest.fixture
def root(tmp_path) -> Path:
    """An empty discovery-cache root with one family's folder and a built, empty index."""
    (tmp_path / 'some_family').mkdir()
    DiscoveryCacheIndex(tmp_path).rebuild()
    return tmp_path


class TestAWriteIsEnteredInTheIndex:

    def test_the_file_is_written_and_the_index_stays_current(self, root):
        cache_path = root / 'some_family' / 'kraken_spot_ETHUSD.parquet'
        DiscoveryCacheIndex(root).write_cache_file(_table(1), cache_path)

        rows = DiscoveryCacheIndex(root).read()
        assert cache_path.exists()
        assert list(rows['family']) == ['some_family']
        assert list(rows['file']) == ['kraken_spot_ETHUSD.parquet']
        assert DiscoveryCacheIndex(root).staleness_reason() is None

    def test_a_rewritten_file_keeps_one_row_and_leaves_no_temporary_file(self, root):
        cache_path = root / 'some_family' / 'kraken_spot_ETHUSD.parquet'
        DiscoveryCacheIndex(root).write_cache_file(_table(1), cache_path)
        DiscoveryCacheIndex(root).write_cache_file(_table(2), cache_path)

        assert len(DiscoveryCacheIndex(root).read()) == 1
        assert list((root / 'some_family').iterdir()) == [cache_path]


class TestEveryFamilyWritesThroughTheIndex:

    def test_no_module_writes_a_parquet_file_past_it(self):
        units = sorted(p for p in _DISCOVERIES.rglob('*.py') if p != _INDEX_UNIT)
        assert units, f'no module found under {_DISCOVERIES}'
        past_the_index = [
            f'{unit}:{node.lineno} {node.func.attr}'
            for unit in units
            for node in ast.walk(ast.parse(unit.read_text()))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr in _PARQUET_WRITES
        ]
        assert not past_the_index, (
            'a discovery module writes a parquet file itself — write it through '
            f'DiscoveryCacheIndex.write_cache_file so the index keeps up: {past_the_index}')
