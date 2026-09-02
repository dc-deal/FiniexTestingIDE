"""
Store catalog tests (#486).

The catalog's whole value is that it is COMPLETE and that its indexes are DISPOSABLE. Both are
properties a review cannot check by reading — a store added without a registration looks exactly
like one that was never added, and a stale index looks exactly like a fresh one until something
compares. So both are asserted here.
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from python.framework.exceptions.store_errors import StoreCatalogError
from python.framework.reporting.certificates.certificate_index import CertificateIndex
from python.framework.store.abstract_store_index import (
    MIXED_LOGIC_VERSION,
    AbstractStoreIndex,
    store_index_filename,
)
from python.framework.store.store_catalog import StoreCatalog
from python.framework.types.store_types import (
    RetrievalForm,
    StoreBackend,
    StoreId,
    StoreKind,
)


class _ToyIndex(AbstractStoreIndex):
    """A minimal index over a directory of JSON files, for testing the base class itself."""

    COLUMNS = ['name', 'size']
    LOGIC_VERSION = 3

    def __init__(self, path, source_dir):
        super().__init__(path)
        self._source = source_dir

    def rebuild(self) -> int:
        rows = [{'name': f.name, 'size': f.stat().st_size}
                for f in sorted(self._source.glob('*.json'))]
        self.write(pd.DataFrame(rows, columns=self.COLUMNS))
        return len(rows)


class TestCatalogCompleteness:
    """Every store is declared, and every declaration says enough to act on."""

    def test_every_store_id_is_registered(self):
        """
        A StoreId without a registration is a store nothing can find (CLAUDE.md §44).

        Necessary but NOT sufficient, and the insufficiency is the point: on its own this asks
        only whether every DECLARED store is declared. Two real stores — the generator profiles
        and the finished archive — existed for months and fell straight through it, because
        nothing here looks at the disk. `test_no_written_directory_is_unregistered` below is the
        half that does.
        """
        registered = {d.store_id for d in StoreCatalog().all()}
        missing = sorted(s.value for s in StoreId if s not in registered)
        assert not missing, f'unregistered stores: {missing}'

    def test_every_descriptor_carries_kind_form_and_root(self):
        for descriptor in StoreCatalog().all():
            assert isinstance(descriptor.kind, StoreKind)
            assert isinstance(descriptor.form, RetrievalForm)
            assert isinstance(descriptor.backend, StoreBackend)
            assert str(descriptor.root), f'{descriptor.store_id} has no root'

    def test_a_special_store_states_why_it_is_special(self):
        """SPECIAL is a declaration, not a loophole — it has to say what it is instead."""
        for descriptor in StoreCatalog().all():
            if descriptor.kind is StoreKind.SPECIAL:
                assert descriptor.note, f'{descriptor.store_id} is SPECIAL without a reason'
                assert descriptor.form is RetrievalForm.NONE

    def test_a_managed_store_has_an_index_or_says_why_not(self):
        """The index obligation, asserted rather than reviewed."""
        for descriptor in StoreCatalog().all():
            if descriptor.kind is StoreKind.SPECIAL:
                continue
            assert descriptor.index_path is not None or descriptor.note, (
                f'{descriptor.store_id} has neither an index nor a stated reason for having none')

    def test_an_unregistered_store_is_named_rather_than_answered_with_none(self):
        catalog = StoreCatalog()
        with pytest.raises(StoreCatalogError):
            catalog.rebuild(StoreId.RAW_INBOX)

    def test_a_derived_store_names_what_it_derives_from(self):
        """DERIVED without a source is a claim with no address."""
        for descriptor in StoreCatalog().all():
            if descriptor.kind is StoreKind.DERIVED and descriptor.derived_from is None:
                assert descriptor.note, (
                    f'{descriptor.store_id} is DERIVED with neither a source store nor a stated '
                    f'reason for having none')

    def test_a_derivation_edge_points_at_a_registered_store(self):
        """An edge to a store the catalog does not carry is worse than no edge."""
        registered = {d.store_id for d in StoreCatalog().all()}
        for descriptor in StoreCatalog().all():
            if descriptor.derived_from is not None:
                assert descriptor.derived_from in registered, descriptor.store_id
                assert descriptor.derived_from is not descriptor.store_id, 'self-derivation'

    def test_an_owned_index_is_named_after_its_store(self):
        """
        One naming rule, structurally enforced.

        Seven indexes were named five ways — `index.parquet` twice in different folders, and
        three dot-prefixed, which is Unix HIDING: a store index is not a hidden file, and a file
        on disk did not say which store it belonged to. The rule covers all seven, including the
        three legacy managers this model does not otherwise own: renaming their file is a
        constant, not a migration.
        """
        for descriptor in StoreCatalog().all():
            if descriptor.index_path is None:
                continue
            expected = store_index_filename(descriptor.store_id)
            assert descriptor.index_path is not None
            assert descriptor.index_path.name == expected, (
                f'{descriptor.store_id}: index is {descriptor.index_path.name}, '
                f'the rule says {expected}')

    def test_no_written_directory_is_unregistered(self):
        """
        The honest half: start from the DISK and demand a home for what is actually there.

        The circular test above cannot fail on a store nobody declared, which is exactly the
        failure it needs to catch — two real stores fell through it, the generator profiles and
        the finished archive, and both were on disk the whole time.

        Scope is deliberate. It walks `data/` and `configs/` one level, which is where both
        misses were, and NOT `runs/` or `logs/`: those are single-store roots whose children are
        the store's own internal structure, and the session fixture redirects them to tmp anyway,
        so a walk there would compare the real tree against an isolated catalog.

        The allowlist is short and annotated on purpose. Adding to it is a decision, not a shrug.
        """
        allowed = {
            Path('data/test'): 'import-pipeline fixtures, written only by tests (§34)',
            Path('data/raw_sample_data'): 'the shipped sample package — an input nobody derives from',
            Path('data/runtime'): 'the parent of registered stores, not a store itself',
            Path('configs/brokers'): 'hand-written broker seed configuration (§28)',
            Path('configs/credentials'): 'credential files (§29) — never indexed, never listed',
            Path('configs/scenario_sets'): 'hand-written scenario configuration',
            Path('configs/autotrader_profiles'): 'hand-written live/backtest profiles',
            Path('configs/discoveries'): 'hand-written discovery configuration',
            Path('configs/test_scenarios'): 'hand-written test scenario configuration',
            Path('configs/broker_settings'): 'hand-written per-broker settings (§28)',
            Path('configs/generator'): 'hand-written generator configuration + header template',
            Path('configs/sweeps'): 'hand-written parameter-sweep specifications (#390)',
        }
        roots = [d.root for d in StoreCatalog().all()]

        def homed(path: Path) -> bool:
            if any(path == r or r in path.parents for r in roots):
                return True
            return any(path == a or a in path.parents for a in allowed)

        unhomed = sorted(
            str(d) for base in (Path('data'), Path('configs'))
            if base.is_dir()
            for d in base.iterdir()
            if d.is_dir() and not d.name.startswith('.') and not homed(d))
        assert not unhomed, (
            f'directories holding persistent data with no registered store: {unhomed}. '
            f'Register them in store_registrations.py, or add them to this allowlist WITH a '
            f'reason (CLAUDE.md §44).')


class TestIndexBase:
    """The shared machinery: atomic writes, a stamped logic version, and a rebuildable file."""

    @staticmethod
    def _tree(tmp_path, count=3):
        source = tmp_path / 'source'
        source.mkdir()
        for i in range(count):
            (source / f'{i}.json').write_text('{}', encoding='utf-8')
        return _ToyIndex(tmp_path / 'index.parquet', source)

    def test_a_missing_index_reads_as_empty_with_its_columns(self, tmp_path):
        index = self._tree(tmp_path)
        frame = index.read()
        assert frame.empty
        assert list(frame.columns) == _ToyIndex.COLUMNS

    def test_the_write_leaves_no_temp_file(self, tmp_path):
        index = self._tree(tmp_path)
        index.rebuild()
        leftovers = list(tmp_path.glob('*.tmp'))
        assert not leftovers, f'temp file survived the write: {leftovers}'

    def test_deleting_the_index_loses_nothing(self, tmp_path):
        """DERIVED means disposable — the property the whole store model rests on."""
        index = self._tree(tmp_path)
        index.rebuild()
        before = index.read()

        index.get_path().unlink()
        assert index.read().empty, 'a deleted index must read as empty, not stale'

        assert index.rebuild() == len(before)
        pd.testing.assert_frame_equal(index.read(), before)

    def test_the_logic_version_is_stamped_and_read_back(self, tmp_path):
        index = self._tree(tmp_path)
        index.rebuild()
        assert index.stored_logic_version() == _ToyIndex.LOGIC_VERSION
        assert index.is_current()

    def test_a_bumped_logic_version_invalidates_the_file(self, tmp_path):
        """
        The blind spot this field exists for: the sources did not change, the CODE did.

        A staleness rule keyed on source mtime cannot see this — the index is still newer than
        every source, so it would keep serving content the current logic would not produce.
        """
        index = self._tree(tmp_path)
        index.rebuild()
        assert index.is_current()

        index.LOGIC_VERSION = _ToyIndex.LOGIC_VERSION + 1
        assert not index.is_current(), 'a changed logic version must invalidate the index'

    def test_a_deletion_is_seen_even_though_no_mtime_moved(self, tmp_path):
        """
        The case a purely time-based freshness rule cannot see.

        Removing a source file leaves every SURVIVING file's mtime untouched, so "the index is
        newer than everything" still holds while the index describes entries that are gone. A
        subclass that can count its sources catches it; the base class cannot, and says so by
        answering only the code question.
        """
        index = self._tree(tmp_path, count=3)
        index.rebuild()
        assert len(index.read()) == 3

        next(iter(sorted((tmp_path / 'source').glob('*.json')))).unlink()
        assert index.is_current(), 'the code did not change, so the code question still passes'
        assert len(index.read()) == 3, 'and the file still describes the deleted entry'

        assert index.rebuild() == 2
        assert len(index.read()) == 2

    def test_an_incremental_append_does_not_launder_the_version(self, tmp_path):
        """
        The seam that structurally disabled the version check for the store that appends most.

        An incremental index reads its own file, adds a row and writes everything back. A plain
        write would stamp the CURRENT version onto rows the previous version produced — the file
        would then claim to be current while most of its content is not. Appending across a bump
        must therefore mark the file MIXED, which matches no version and demands a rebuild.
        """
        index = self._tree(tmp_path)
        index.rebuild()
        assert index.is_current()

        index.LOGIC_VERSION = _ToyIndex.LOGIC_VERSION + 1
        index.write_incremental(index.read())

        assert index.stored_logic_version() == MIXED_LOGIC_VERSION
        assert not index.is_current(), 'a mixed-generation file must never read as current'

        assert index.rebuild() == 3
        assert index.is_current(), 'a full rebuild is single-generation again'

    def test_an_incremental_append_on_a_matching_file_keeps_the_version(self, tmp_path):
        """The common case must not be dragged into MIXED by the guard above."""
        index = self._tree(tmp_path)
        index.rebuild()
        index.write_incremental(index.read())
        assert index.stored_logic_version() == _ToyIndex.LOGIC_VERSION
        assert index.is_current()

    def test_an_unstamped_index_counts_as_out_of_date(self, tmp_path):
        """An index written before the stamp existed cannot claim to be current."""
        index = self._tree(tmp_path)
        pd.DataFrame(columns=_ToyIndex.COLUMNS).to_parquet(index.get_path(), index=False)
        assert index.stored_logic_version() is None
        assert not index.is_current()


class TestStatus:
    """The operator's overview reports what it can measure, and admits what it cannot."""

    def test_every_store_reports_a_status_row(self):
        rows = StoreCatalog().status()
        assert len(rows) == len(list(StoreId))
        assert {r.store_id for r in rows} == set(StoreId)

    def test_a_store_without_an_index_of_ours_reports_no_staleness(self):
        """`None` is the honest answer — the catalog cannot judge an index it does not own."""
        rows = {r.store_id: r for r in StoreCatalog().status()}
        assert rows[StoreId.RAW_INBOX].stale_reason is None
        assert rows[StoreId.TICKS].stale_reason is None, 'a foreign index is not ours to grade'


class TestStalenessReason:
    """A bare "stale" flag sends the operator to rebuild the wrong thing."""

    class _Idx(AbstractStoreIndex):
        COLUMNS = ['a']
        LOGIC_VERSION = 2

        def rebuild(self) -> int:
            self.write(pd.DataFrame([{'a': 1}], columns=self.COLUMNS))
            return 1

    def test_a_never_built_index_says_so(self, tmp_path):
        index = self._Idx(tmp_path / 'x_index.parquet')
        assert index.staleness_reason() == 'never built'
        assert not index.is_valid()

    def test_a_valid_index_has_no_reason(self, tmp_path):
        index = self._Idx(tmp_path / 'x_index.parquet')
        index.rebuild()
        assert index.staleness_reason() is None
        assert index.is_valid()

    def test_a_version_mismatch_names_both_versions(self, tmp_path):
        index = self._Idx(tmp_path / 'x_index.parquet')
        index.rebuild()
        index.LOGIC_VERSION = 5
        reason = index.staleness_reason()
        assert 'v2' in reason and 'v5' in reason, reason

    def test_a_mixed_generation_file_says_mixed(self, tmp_path):
        index = self._Idx(tmp_path / 'x_index.parquet')
        index.rebuild()
        index.LOGIC_VERSION = 5
        index.write_incremental(index.read())
        assert 'more than one generation' in index.staleness_reason()

    def test_is_valid_and_the_reason_can_never_disagree(self, tmp_path):
        """They are one answer, not two — is_valid() is defined as 'no reason'."""
        index = self._Idx(tmp_path / 'x_index.parquet')
        for step in (lambda: None, index.rebuild):
            step()
            assert index.is_valid() == (index.staleness_reason() is None)


class TestOperatorSignal:
    """What the catalog ASKS OF the operator, and what it merely notes."""

    def test_a_self_healing_store_is_not_a_task(self):
        """
        The ledger goes stale after every run — a run appends a fragment, by construction.

        Its own reader rebuilds the index before serving it, so reporting that as "rebuild before
        trusting it" would be a permanent warning about nothing. The flag is what separates a
        task from a note.
        """
        rows = {r.store_id: r for r in StoreCatalog().status()}
        assert rows[StoreId.RUN_LEDGER].self_healing is True
        assert rows[StoreId.RUNS].self_healing is False, (
            'the run index is written incrementally at run start and does NOT self-heal')

    def test_only_the_newest_certificate_of_a_family_can_expire_a_gate(self, tmp_path):
        """
        An old certificate expiring is what old certificates do.

        Listing every expired one turns a release gate into permanent noise; the question a
        release asks is whether the certificate that WOULD be presented still holds.
        """
        family = tmp_path / 'some_gate' / 'reports'
        family.mkdir(parents=True)
        (family / 'x_report_1.0.0_2020-01-01_000000.json').write_text(json.dumps({
            'release_version': '1.0.0', 'timestamp': '2020-01-01T00:00:00+00:00',
            'valid_until': '2020-04-01T00:00:00+00:00'}), encoding='utf-8')
        (family / 'x_report_2.0.0_2099-01-01_000000.json').write_text(json.dumps({
            'release_version': '2.0.0', 'timestamp': '2099-01-01T00:00:00+00:00',
            'valid_until': '2099-04-01T00:00:00+00:00'}), encoding='utf-8')

        index = CertificateIndex(tmp_path)
        index.rebuild()
        assert len(index.read()) == 2, 'both are indexed — the history stays'
        assert index.expired_families() == [], 'the newest is valid, so the gate is not expired'

        (family / 'x_report_2.0.0_2099-01-01_000000.json').unlink()
        index.rebuild()
        assert [f for f, _, _ in index.expired_families()] == ['some_gate']
