"""
Run identity (#475).

A run used to be identified by a second-resolution timestamp directory name. Measured on one
machine: 188 runs, 4 collisions, two of them across categories — and `ReportStore` returned the
FIRST match, so on a collision the API served a different run's artifacts than the index listed
under that id. The directory name was also the only answer to "what was this run".

Three things replaced it, and this suite pins each:

- an id that carries a readable half and a distinct half
- a `header.json` written at the run's START, so a crashed run is still identifiable
- an index DERIVED from those headers — which is only safe to rely on if it can be rebuilt
"""

from datetime import datetime, timezone
from pathlib import Path

from python.framework.reporting.io.run_header_io import (
    RUN_HEADER_ARTIFACT,
    read_run_header,
    write_run_header,
)
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.report_types import ParentKind, RunHeader
from python.framework.types.config_types.file_logging_config_types import RunLogPaths
from python.framework.types.log_layout_types import IO_SUBDIR, RUN_TYPE_LIVE, RUN_TYPE_SIMULATION
from python.framework.utils.run_id_utils import mint_run_id

_START = datetime(2026, 8, 30, 13, 20, 34, tzinfo=timezone.utc)


def _header(run_id: str, run_type: str = RUN_TYPE_SIMULATION, parent: str = None,
            parent_kind: ParentKind = None) -> RunHeader:
    return RunHeader(run_id=run_id, start_time=_START, run_type=run_type,
                     run_name='my_set', parent_id=parent, parent_kind=parent_kind,
                     config_snapshot='scenario_config.json',
                     app_version='1.4.0', git_commit='abc1234')


class TestTheIdIsDistinctAndStillReadable:
    def test_two_runs_in_the_same_second_get_different_ids(self):
        """The exact case that collided: same second, two runs."""
        assert mint_run_id(_START) != mint_run_id(_START)

    def test_the_readable_half_keeps_byte_order_equal_to_time_order(self):
        """`list_runs` sorts on the id and the sweep ranking tie-breaks on it — both rest on this."""
        earlier = mint_run_id(datetime(2026, 8, 30, 13, 20, 34, tzinfo=timezone.utc))
        later = mint_run_id(datetime(2026, 8, 30, 13, 20, 35, tzinfo=timezone.utc))
        assert earlier < later

    def test_the_id_is_url_safe_by_construction(self):
        """
        A consumer interpolates `run_id` into a URL path without encoding, and asked whether the
        character class is a guarantee or an accident. It is a guarantee — but only as long as
        something holds it, which is what this test is for.

        The id is MINTED, never taken from input: the free-text a user supplies is `run_name`, a
        different field. Nothing outside `mint_run_id` can widen the class.
        """
        allowed = set('0123456789abcdef_')
        for _ in range(200):
            assert set(mint_run_id(_START)) <= allowed

    def test_a_taken_id_is_re_minted(self, tmp_path):
        """The mint site claims; it does not join a directory that already exists."""
        taken = mint_run_id(_START, tmp_path)
        (tmp_path / taken).mkdir()
        assert mint_run_id(_START, tmp_path) != taken

    def test_an_unknown_id_resolves_to_nothing(self, tmp_path):
        """
        What replaced the format check: the index is an exact-match whitelist, so a crafted id
        cannot resolve. It used to be interpolated into a glob, where `'*'` matched the first
        run in the tree — membership is the stronger guard, and it costs legacy ids nothing.
        """
        index = RunIndex(tmp_path / 'index.parquet')
        header = _header('20260830_132034_aaaaaaaa')
        write_run_header(header, tmp_path / 'a_run')
        index.register_run(header, tmp_path / 'a_run')

        for crafted in ('*', '../secret', '20260830_132034', ''):
            assert index.run_dir(crafted) is None


class TestTheHeaderSurvivesTheRunItDescribes:
    def test_it_round_trips(self, tmp_path):
        header = _header('20260830_132034_a3f9c2d1')
        assert read_run_header(write_run_header(header, tmp_path)) == header

    def test_it_is_written_before_anything_else_can_fail(self, tmp_path):
        """
        Written at the START. A run that crashes is exactly the run somebody needs to identify,
        so an artifact produced on the way out is missing whenever it matters most.
        """
        run_dir = tmp_path / 'a_run'
        write_run_header(_header('20260830_132034_a3f9c2d1'), run_dir)
        # Nothing else ran — no io/, no logs. The header still stands on its own.
        assert (run_dir / RUN_HEADER_ARTIFACT).exists()
        assert not (run_dir / 'io').exists()


class TestTheIndexIsDerivedAndRebuildable:
    """
    The property the whole design rests on: the index may be deleted or go stale without anything
    being lost. If a rebuild did not reproduce it, the index would be a second source of truth.
    """

    @staticmethod
    def _tree(root: Path) -> RunLogPaths:
        return RunLogPaths(simulation=root / 'simulation', live=root / 'live')

    def test_rebuild_reproduces_what_the_appends_wrote(self, tmp_path):
        roots = self._tree(tmp_path)
        index = RunIndex(tmp_path / 'index.parquet', roots)

        planted = [
            (_header('20260830_132034_aaaaaaaa'),
             roots.simulation / 'my_set' / '20260830_132034_aaaaaaaa'),
            (_header('20260830_132035_bbbbbbbb', RUN_TYPE_LIVE),
             roots.live / 'my_profile' / '20260830_132035_bbbbbbbb'),
            # A sweep combination is a SIMULATION with a parent — nesting is not a type.
            (_header('20260830_132036_cccccccc', parent='sweep_20260830_132030',
                     parent_kind=ParentKind.SWEEP),
             roots.sweeps / 'sweep_20260830_132030' / 'my_set_c000' / '20260830_132036_cccccccc'),
        ]
        for header, run_dir in planted:
            index.register_run(header, run_dir)
        before = index.list_runs()

        (tmp_path / 'index.parquet').unlink()
        assert index.list_runs() == [], 'a deleted index must read as empty, not stale'

        assert index.rebuild() == len(planted)
        rebuilt = index.list_runs()

        # IDENTITY reproduces exactly — that is the property this test exists for.
        identity = lambda rows: [r.model_dump(exclude={'size_bytes'}) for r in rows]
        assert identity(rebuilt) == identity(before)

        # `size_bytes` deliberately does NOT, and it is the one field that cannot: it is a
        # MEASUREMENT taken when the run finished, where every other column comes from a
        # header written at its start. Registration knows nothing about a directory that has
        # not been filled yet, so it records 0; the rebuild reads what is actually there. A
        # rebuilt index carrying the CURRENT truth is the repair path working, not drift.
        assert all(r.size_bytes == 0 for r in before)
        assert all(r.size_bytes > 0 for r in rebuilt), (
            'the rebuild did not measure what each run occupies')

    def test_a_run_is_addressable_without_walking_the_tree(self, tmp_path):
        """The sweep combination sits one level deeper — the lookup no longer has to know that."""
        roots = self._tree(tmp_path)
        index = RunIndex(tmp_path / 'index.parquet')
        deep = roots.sweeps / 'sweep_20260830_132030' / 'my_set_c000' / '20260830_132036_cccccccc'
        header = _header('20260830_132036_cccccccc', parent='sweep_20260830_132030')
        index.register_run(header, deep)

        assert index.run_dir('20260830_132036_cccccccc') == deep
        assert index.run_dir('20260830_132036_dddddddd') is None

    def test_reports_are_marked_when_they_are_written(self, tmp_path):
        roots = self._tree(tmp_path)
        index = RunIndex(tmp_path / 'index.parquet')
        run_dir = roots.simulation / 'my_set' / '20260830_132034_aaaaaaaa'
        header = _header('20260830_132034_aaaaaaaa')
        index.register_run(header, run_dir)

        assert index.list_runs()[0].artifacts == []
        assert index.list_runs()[0].has_reports is False

        (run_dir / IO_SUBDIR).mkdir(parents=True)
        for name in ('portfolio.json', 'trade_history.csv'):
            (run_dir / IO_SUBDIR / name).write_text('{}', encoding='utf-8')
        index.record_artifacts('20260830_132034_aaaaaaaa', run_dir)

        # The LIST, not a boolean: the two pipelines produce different sets, so a consumer
        # that only learned "yes, some" would still be guessing which.
        assert index.list_runs()[0].artifacts == ['portfolio.json', 'trade_history.csv']
        assert index.list_runs()[0].has_reports is True


class TestTheParentIdSaysWhatKindOfParentItIs:
    """
    `parent_id` holds two different things and they have the same shape (#386).

    A sweep groups the COMBINATIONS of one search; a deployment groups the SESSIONS of one live
    bot across restarts. Both are an identity that groups runs without being one, and both are a
    prefix plus a timestamp — so a consumer holding only the id has nothing to tell them apart
    by, and the pruner's `--keep-last` quota was spent by whichever prefix sorted higher.
    """

    def test_the_kind_survives_the_index(self, tmp_path):
        index = RunIndex(tmp_path / 'index.parquet')
        index.register_run(
            _header('20260830_132036_cccccccc', parent='sweep_20260830_132030',
                    parent_kind=ParentKind.SWEEP),
            tmp_path / 'a')
        index.register_run(
            _header('20260918_091413_dddddddd', RUN_TYPE_LIVE, parent='deploy_20260918_091413',
                    parent_kind=ParentKind.DEPLOYMENT),
            tmp_path / 'b')

        kinds = {r.run_id: r.parent_kind for r in index.list_runs()}

        assert kinds['20260830_132036_cccccccc'] is ParentKind.SWEEP
        assert kinds['20260918_091413_dddddddd'] is ParentKind.DEPLOYMENT

    def test_a_standalone_run_names_no_kind(self, tmp_path):
        """None here means "no parent", never "a parent of some kind we did not record"."""
        index = RunIndex(tmp_path / 'index.parquet')
        index.register_run(_header('20260830_132034_aaaaaaaa'), tmp_path / 'a')

        row = index.list_runs()[0]

        assert row.parent_id is None and row.parent_kind is None

    def test_a_header_written_before_the_discriminator_still_reads(self, tmp_path):
        """
        The field is ADDITIVE, and it has to be: every header already on disk predates it.

        Such a run keeps its parent and reports an unknown kind — which is the truth. Refusing
        the pair instead would make the index unreadable for its own history.
        """
        run_dir = tmp_path / 'simulation' / 'my_set' / '20260830_132036_cccccccc'
        run_dir.mkdir(parents=True)
        (run_dir / RUN_HEADER_ARTIFACT).write_text(
            '{"run_id": "20260830_132036_cccccccc", "start_time": "2026-08-30T13:20:34+00:00",'
            ' "run_type": "simulation", "run_name": "my_set",'
            ' "parent_id": "sweep_20260830_132030"}', encoding='utf-8')

        header = read_run_header(run_dir / RUN_HEADER_ARTIFACT)

        assert header.parent_id == 'sweep_20260830_132030'
        assert header.parent_kind is None
