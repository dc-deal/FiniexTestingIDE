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
from python.framework.types.log_layout_types import IO_SUBDIR
from python.framework.types.api.report_types import RunHeader
from python.framework.types.config_types.file_logging_config_types import RunLogPaths
from python.framework.types.log_layout_types import RUN_TYPE_LIVE, RUN_TYPE_SIMULATION
from python.framework.utils.run_id_utils import mint_run_id

_START = datetime(2026, 8, 30, 13, 20, 34, tzinfo=timezone.utc)


def _header(run_id: str, run_type: str = RUN_TYPE_SIMULATION, parent: str = None) -> RunHeader:
    return RunHeader(run_id=run_id, start_time=_START, run_type=run_type,
                     run_name='my_set', parent_id=parent, config_snapshot='scenario_config.json',
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
        index = RunIndex(tmp_path / 'index.parquet')

        planted = [
            (_header('20260830_132034_aaaaaaaa'),
             roots.simulation / 'my_set' / '20260830_132034_aaaaaaaa'),
            (_header('20260830_132035_bbbbbbbb', RUN_TYPE_LIVE),
             roots.live / 'my_profile' / '20260830_132035_bbbbbbbb'),
            # A sweep combination is a SIMULATION with a parent — nesting is not a type.
            (_header('20260830_132036_cccccccc', parent='sweep_20260830_132030'),
             roots.sweeps / 'sweep_20260830_132030' / 'my_set_c000' / '20260830_132036_cccccccc'),
        ]
        for header, run_dir in planted:
            index.register_run(header, run_dir)
        before = index.list_runs()

        (tmp_path / 'index.parquet').unlink()
        assert index.list_runs() == [], 'a deleted index must read as empty, not stale'

        assert index.rebuild(roots) == len(planted)
        assert index.list_runs() == before

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
