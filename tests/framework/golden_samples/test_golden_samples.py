"""
The golden samples (#557) — every frozen aggregate, rebuilt through today's code, field by field.

A sample freezes a verified production of a fixture catalog entry: the report sections and ledger
rows each aggregate is computed from, and what the runs recorded. These tests rebuild every block
from the frozen inputs and compose the read-time views from the frozen rows, and compare each
with what was recorded — a float to the last bit, a missing field a difference like any other.
The aggregation refactor moves every aggregate onto one component and must move no number; these
are what says so.

A failure names the sample, the aggregate, the run and the field. When the change was MEANT to
move that number, the sample is frozen again from a new production and the review reads the diff:
python python/cli/fixture_catalog_cli.py produce <entry> · ... freeze <entry>
"""

from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable, Dict, List, Tuple
from unittest.mock import MagicMock

import pytest

from python.framework.constants.golden_sample_constants import GOLDEN_SAMPLES_DIR
from python.framework.exceptions.golden_sample_errors import (
    GoldenSampleDirtyProductionError,
    GoldenSampleNoProductionError,
    GoldenSampleNotReproducedError,
)
from python.framework.fixture_catalog.fixture_catalog import (
    FIXTURE_CATALOG_BY_ID,
    golden_sample_entries,
)
from python.framework.fixture_catalog.fixture_production_store import FixtureProductionStore
from python.framework.fixture_catalog.golden_samples.golden_sample_checks import (
    FROZEN_NOT_REBUILT,
    FROZEN_SECTIONS,
    GOLDEN_SAMPLE_BLOCKS,
    check_block,
    check_views,
)
from python.framework.fixture_catalog.golden_samples.golden_sample_freezer import (
    GoldenSampleFreezer,
)
from python.framework.fixture_catalog.golden_samples.golden_sample_io import (
    frozen_entry_ids,
    load_golden_sample,
)
from python.framework.types.fixture_catalog_types import FixtureProduction
from python.framework.types.golden_sample_types import (
    FrozenRun,
    GoldenSample,
    GoldenSampleBlock,
    GoldenSampleDifference,
    GoldenSampleHeader,
)

_ROOT = Path(__file__).resolve().parents[3] / GOLDEN_SAMPLES_DIR
_ENTRIES = frozen_entry_ids(_ROOT)
_BLOCKS: Dict[str, GoldenSampleBlock] = {block.block_id: block for block in GOLDEN_SAMPLE_BLOCKS}
_FILE_OF_FIELD: Dict[str, str] = {name: spec.filename for name, spec in FROZEN_SECTIONS}


@lru_cache(maxsize=None)
def _sample(entry_id: str) -> GoldenSample:
    """
    One entry's golden sample, read once per session.

    Args:
        entry_id: The fixture catalog entry

    Returns:
        The sample
    """
    return load_golden_sample(_ROOT, entry_id)


def _header(entry_id: str) -> GoldenSampleHeader:
    """
    One entry's header, read for collection without loading its sections.

    Args:
        entry_id: The fixture catalog entry

    Returns:
        The header
    """
    return _sample(entry_id).header


def _block_cases() -> List[Tuple[str, str]]:
    """
    Every (entry, block) pair where the block applies to at least one frozen run.

    Decided from the headers' section lists, so collection reads no section.

    Returns:
        The pairs, in entry and block order
    """
    cases = []
    for entry_id in _ENTRIES:
        frozen = [set(run.sections) for run in _header(entry_id).runs]
        for block in GOLDEN_SAMPLE_BLOCKS:
            needed = {_FILE_OF_FIELD[name] for name in block.needs}
            if any(needed <= sections for sections in frozen):
                cases.append((entry_id, block.block_id))
    return cases


_BLOCK_CASES = _block_cases()

# What makes the frozen section of each family not rebuilt yet a reference worth having.
_HAS_CONTENT: Dict[str, Callable[[FrozenRun], bool]] = {
    'signal': lambda run: run.signal is not None and bool(run.signal.units),
    'feed-stability.counters': lambda run: (run.feed_stability is not None and any(
        row.fresh_ticks or row.stale_ticks or row.blind_ticks for row in run.feed_stability.units)),
}


def _report(differences: List[GoldenSampleDifference]) -> str:
    """
    The differences as a failure message — the first of them in full.

    Args:
        differences: What was not reproduced

    Returns:
        The message
    """
    lines = [f'{d.subject} · {d.path}: recorded {d.recorded!r}, rebuilt {d.rebuilt!r}'
             for d in differences[:15]]
    more = f'\n… and {len(differences) - 15} more' if len(differences) > 15 else ''
    return f'{len(differences)} field(s) not reproduced:\n' + '\n'.join(lines) + more


class TestEveryFrozenAggregateRebuilds:
    """The proof itself: today's code reproduces every frozen aggregate from its frozen inputs."""

    @pytest.mark.parametrize('entry_id, block_id', _BLOCK_CASES)
    def test_the_aggregate_rebuilds_from_its_frozen_inputs(self, entry_id, block_id):
        differences = check_block(_sample(entry_id), _BLOCKS[block_id])
        assert not differences, _report(differences)

    @pytest.mark.parametrize('entry_id', _ENTRIES)
    def test_the_read_time_views_compose_from_the_frozen_rows(self, entry_id):
        differences = check_views(_sample(entry_id))
        assert not differences, _report(differences)


class TestTheSamplesCoverWhatTheyMustCover:
    """A sample set that silently shrank would pass every test above; these say what it holds."""

    def test_every_entry_the_samples_are_taken_from_is_frozen(self):
        wanted = {entry.entry_id for entry in golden_sample_entries()}
        assert set(_ENTRIES) == wanted, (
            f'missing: {sorted(wanted - set(_ENTRIES))}, not in the catalog: '
            f'{sorted(set(_ENTRIES) - wanted)} — freeze with fixture_catalog_cli.py freeze')

    def test_every_block_is_checked_somewhere(self):
        checked = {block_id for _, block_id in _BLOCK_CASES}
        assert checked == set(_BLOCKS), f'checked by no sample: {sorted(set(_BLOCKS) - checked)}'

    def test_every_view_has_a_subject(self):
        views = [_sample(entry_id).views for entry_id in _ENTRIES]
        assert any(view.run_list_figures for view in views)
        assert any(view.deployments and view.deployment_details for view in views)
        assert any(view.deployment_booking_periods for view in views)
        assert any(view.sweeps and view.sweep_details for view in views)

    def test_every_family_not_rebuilt_yet_is_frozen_with_content(self):
        # Their samples are the reference package 2 rebuilds them against — an empty section
        # would be a reference for nothing. A family that is rebuilt leaves FROZEN_NOT_REBUILT
        # and its content check here; a new one arrives with its check.
        runs: List[FrozenRun] = [run for entry_id in _ENTRIES for run in _sample(entry_id).runs]
        assert set(FROZEN_NOT_REBUILT) == set(_HAS_CONTENT)
        for family, has_content in _HAS_CONTENT.items():
            assert any(has_content(run) for run in runs), f'{family} is frozen nowhere with content'


class TestEachSampleSaysWhereItComesFrom:
    """A sample nobody can date is one nobody dares to replace or to delete."""

    @pytest.mark.parametrize('entry_id', _ENTRIES)
    def test_it_names_its_production_its_code_and_its_freeze(self, entry_id):
        header = _header(entry_id)
        assert header.entry_id == entry_id
        assert header.produced_at and header.frozen_at and header.report_contract
        assert header.runs
        for run in header.runs:
            assert run.commit, f'{run.run_id} names no commit'
            assert run.dirty is not None, f'{run.run_id} cannot say whether its tree was clean'

    @pytest.mark.parametrize('entry_id', _ENTRIES)
    def test_its_files_are_the_runs_it_names(self, entry_id):
        sample = _sample(entry_id)
        named = [run.run_id for run in sample.header.runs]
        assert [run.run_id for run in sample.runs] == named
        for frozen, run in zip(sample.header.runs, sample.runs):
            present = {_FILE_OF_FIELD[name] for name, _ in FROZEN_SECTIONS
                       if getattr(run, name) is not None}
            assert present == set(frozen.sections), run.run_id
        assert {row.run_id for row in sample.inputs.ledger_rows} <= set(named)
        assert {run.run_id for run in sample.inputs.run_index} <= set(named)


class TestTheFrozenSectionsMatchTheRun:
    """The declaration of what a run freezes and the type that holds it, held to each other."""

    def test_every_frozen_section_has_its_field_and_every_field_its_section(self):
        fields = set(FrozenRun.__dataclass_fields__) - {'run_id', 'ledger_rows'}
        declared = {name for name, _ in FROZEN_SECTIONS}
        assert declared == fields

    def test_every_block_needs_only_sections_a_run_can_freeze(self):
        declared = {name for name, _ in FROZEN_SECTIONS}
        for block in GOLDEN_SAMPLE_BLOCKS:
            assert set(block.needs) <= declared, block.block_id


class TestTheFreezerRefusesWhatItCannotStandBehind:
    """
    The freezer writes nothing doubtful. Its stores are stood in for by the inputs of a committed
    sample, so the refusals are provoked on real report sections without producing a run.
    """

    _ENTRY = 'report_coverage'

    def _freezer(self, tmp_path: Path, sample: GoldenSample, dirty: bool = False,
                 record: bool = True) -> GoldenSampleFreezer:
        """
        A freezer whose stores answer with one committed sample's inputs.

        Args:
            tmp_path: Where the production record and the written samples go
            sample: The sample whose runs, rows and sections the stores answer with
            dirty: Whether the runs' headers say their tree was dirty
            record: Whether the production record names the sample's production

        Returns:
            The freezer
        """
        store = FixtureProductionStore(path=tmp_path / 'record.jsonl')
        if record:
            store.append(FixtureProduction(
                entry_id=self._ENTRY, produced_at=sample.header.produced_at,
                run_ids=[run.run_id for run in sample.header.runs], verified=True,
                report_contract=sample.header.report_contract))
        sections = {run.run_id: run for run in sample.runs}
        run_entries = MagicMock()
        run_entries.read_section.side_effect = lambda run_id, spec: getattr(
            sections[run_id], next(name for name, s in FROZEN_SECTIONS if s is spec))
        header = MagicMock()
        header.run_type = 'simulation'
        header.start_time = datetime(2026, 11, 5, tzinfo=timezone.utc)
        header.code_identity.framework.commit = 'abc1234'
        header.code_identity.framework.branch = 'dev'
        header.code_identity.framework.dirty = dirty
        header.code_identity.framework.diff_hash = 'f00d' if dirty else None
        run_entries.read_header.return_value = header
        ledger = MagicMock()
        ledger.read_rows.return_value = list(sample.inputs.ledger_rows)
        run_index = MagicMock()
        run_index.list_runs.return_value = list(sample.inputs.run_index)
        return GoldenSampleFreezer(store, root=tmp_path / 'samples', run_entries=run_entries,
                                   ledger=ledger, run_index=run_index)

    def test_an_entry_without_a_verified_production_is_refused(self, tmp_path):
        freezer = self._freezer(tmp_path, _sample(self._ENTRY), record=False)
        with pytest.raises(GoldenSampleNoProductionError):
            freezer.freeze(FIXTURE_CATALOG_BY_ID[self._ENTRY])

    def test_a_production_on_an_uncommitted_tree_is_refused_unless_allowed(self, tmp_path):
        sample = _sample(self._ENTRY)
        with pytest.raises(GoldenSampleDirtyProductionError):
            self._freezer(tmp_path, sample, dirty=True).freeze(FIXTURE_CATALOG_BY_ID[self._ENTRY])
        result = self._freezer(tmp_path, sample, dirty=True).freeze(
            FIXTURE_CATALOG_BY_ID[self._ENTRY], allow_dirty=True)
        written = load_golden_sample(tmp_path / 'samples', self._ENTRY).header
        assert all(run.dirty and run.diff_hash == 'f00d' for run in written.runs)
        assert result.block_checks > 0

    def test_a_field_that_does_not_rebuild_refuses_the_freeze_and_writes_nothing(self, tmp_path):
        sample = deepcopy(_sample(self._ENTRY))
        run = next(run for run in sample.runs if run.portfolio and run.portfolio.aggregates)
        run.portfolio.aggregates[0].total_trades += 1
        with pytest.raises(GoldenSampleNotReproducedError) as refused:
            self._freezer(tmp_path, sample).freeze(FIXTURE_CATALOG_BY_ID[self._ENTRY])
        # The run summary is composed from the portfolio headline, so the change shows twice
        assert [(d.check, d.path) for d in refused.value.differences] == [
            ('portfolio.aggregates', '[0].total_trades'),
            ('run-summary', 'currencies[0].total_trades')]
        assert not (tmp_path / 'samples' / self._ENTRY).exists()
