"""
Run-tree pruning (#482).

The run tree grows and nothing removed anything from it, because an empty run directory used to
mean three different things and two of them must never be deleted. `RunHeader.reporting` (#475)
separates them — but measured, the field is the GUARD rather than the selector: after the test
tree was isolated, no production path declares `none` at all.

So this suite pins two different kinds of promise:

- what the command must NEVER touch, whatever selector is used
- what each selector actually selects, including the two cases the tree's shape forces
  (a sweep is a family; a directory without a header is not a run)
"""

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_tree_pruner import (
    FIELD_STUDY_ARTIFACT,
    RunTreePruner,
)
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.api.report_types import (
    ParentKind,
    RunHeader,
    RunInfo,
    RunReporting,
    RunSummary,
    RunSummaryCurrency,
)
from python.framework.types.run_results_types import RunProvenance
from python.framework.types.config_types.file_logging_config_types import RunLogPaths
from python.framework.types.log_layout_types import (
    IO_SUBDIR,
    RUN_TYPE_LIVE,
    RUN_TYPE_SIMULATION,
)
from python.framework.types.run_prune_types import PruneSelectors

_START = datetime(2026, 8, 30, 13, 20, 34, tzinfo=timezone.utc)


def _roots(root: Path) -> RunLogPaths:
    """The two run-type roots under a tmp tree."""
    return RunLogPaths(simulation=root / 'simulation', live=root / 'live')


def _pruner(root: Path) -> RunTreePruner:
    """A pruner pointed entirely at the tmp tree — roots, index AND ledger."""
    return RunTreePruner(_roots(root), root / 'index.parquet', root / 'ledger')


def _plant(root: Path, run_id: str, name: str, *, run_type: str = RUN_TYPE_SIMULATION,
           artifacts: bool = True, reporting: RunReporting = RunReporting.EXPECTED,
           parent: str = None, parent_kind: ParentKind = None,
           field_study: bool = False, minutes: int = 0,
           started: datetime = None) -> Path:
    """
    Write one run the way a real one writes itself: header first, index row with it.

    Args:
        root: The tmp tree
        run_id: Its identity
        name: The owning scenario set / profile
        run_type: 'simulation' or 'live'
        artifacts: Whether it persisted report artifacts
        reporting: What it was commissioned to do
        parent: The sweep or deployment it belongs to, when it belongs to one
        parent_kind: Which of the two that is; defaults to a sweep, which is what a parented
            SIMULATION always is
        field_study: Whether it holds the raw record behind a release certificate
        minutes: Offset from the base start time
        started: An explicit start time, for the cases that care about AGE rather than order

    Returns:
        The run's directory
    """
    base = _roots(root)
    if parent and parent_kind is None:
        parent_kind = ParentKind.SWEEP
    if parent and parent_kind is ParentKind.SWEEP:
        run_dir = base.sweeps / parent / name / run_id
    elif run_type == RUN_TYPE_LIVE:
        run_dir = base.live / name / run_id
    else:
        run_dir = base.simulation / name / run_id
    run_dir.mkdir(parents=True)

    if artifacts:
        (run_dir / IO_SUBDIR).mkdir()
        (run_dir / IO_SUBDIR / 'portfolio.json').write_text('{}', encoding='utf-8')
    if field_study:
        (run_dir / FIELD_STUDY_ARTIFACT).write_text('{}\n', encoding='utf-8')

    header = RunHeader(
        run_id=run_id, start_time=started or _START + timedelta(minutes=minutes),
        run_type=run_type,
        run_name=name, parent_id=parent, parent_kind=parent_kind, reporting=reporting)
    index = RunIndex(root / 'index.parquet')
    index.register_run(header, run_dir)
    if artifacts:
        index.record_artifacts(run_id, run_dir)
    return run_dir


def _orphan(root: Path, relative: str) -> Path:
    """A directory that looks like a run but has no header — a logger built without a set."""
    path = _roots(root).simulation / relative
    path.mkdir(parents=True)
    (path / 'scenario_global_log.log').write_text('x', encoding='utf-8')
    return path


def _deleted_paths(report) -> set:
    """The set of directories a report would remove."""
    return {c.path for c in report.all_deletions()}


class TestWhatMayNeverBeDeleted:
    """
    The guard, and it is the reason this command can exist at all.

    A run that crashed before reporting is the only record of that failure. Before `reporting` it
    was byte-for-byte indistinguishable from a run that was never meant to report.
    """

    def test_a_crashed_run_survives_the_most_aggressive_selector(self, tmp_path):
        """`--keep-last 1` alongside a newer sibling — still kept."""
        crashed = _plant(tmp_path, '20260830_132034_aaaaaaaa', 'my_set',
                         artifacts=False, minutes=0)
        _plant(tmp_path, '20260830_140000_bbbbbbbb', 'my_set', minutes=40)

        report = _pruner(tmp_path).plan(PruneSelectors(keep_last=1))

        assert crashed not in _deleted_paths(report)
        assert [c.path for c in report.kept_incomplete] == [crashed]

    def test_field_study_evidence_survives_the_same(self, tmp_path):
        """It is the input to a real-money release certificate, not archive."""
        evidence = _plant(tmp_path, '20260830_132034_cccccccc', 'live_profile',
                          run_type=RUN_TYPE_LIVE, field_study=True, minutes=0)
        _plant(tmp_path, '20260830_140000_dddddddd', 'live_profile',
               run_type=RUN_TYPE_LIVE, minutes=40)

        report = _pruner(tmp_path).plan(PruneSelectors(keep_last=1))

        assert evidence not in _deleted_paths(report)
        assert [c.path for c in report.kept_field_study] == [evidence]

    def test_the_dry_run_changes_nothing(self, tmp_path):
        """The default is to show, and showing must be free of consequence."""
        _plant(tmp_path, '20260830_132034_eeeeeeee', 'my_set')
        _orphan(tmp_path, 'test_consistency/20260830_132100_ffffffff')
        before = sorted(p for p in (tmp_path / 'simulation').rglob('*'))

        _pruner(tmp_path).plan(PruneSelectors(keep_last=1, orphans=True))

        assert sorted(p for p in (tmp_path / 'simulation').rglob('*')) == before


class TestTheSelectors:
    def test_keep_last_counts_per_set_not_across_the_tree(self, tmp_path):
        """Two scenario sets, one run each — `--keep-last 1` removes neither."""
        _plant(tmp_path, '20260830_132034_aaaaaaaa', 'set_a')
        _plant(tmp_path, '20260830_140000_bbbbbbbb', 'set_b', minutes=40)

        report = _pruner(tmp_path).plan(PruneSelectors(keep_last=1))

        assert report.to_delete_redundant == []

    def test_keep_last_drops_the_older_runs_of_one_set(self, tmp_path):
        oldest = _plant(tmp_path, '20260830_130000_aaaaaaaa', 'my_set', minutes=0)
        middle = _plant(tmp_path, '20260830_140000_bbbbbbbb', 'my_set', minutes=40)
        newest = _plant(tmp_path, '20260830_150000_cccccccc', 'my_set', minutes=100)

        report = _pruner(tmp_path).plan(PruneSelectors(keep_last=1))

        assert _deleted_paths(report) == {oldest, middle}
        assert [c.path for c in report.kept_complete] == [newest]

    def test_an_uncommissioned_run_goes_without_any_selector(self, tmp_path):
        """`reporting=none` + nothing produced is the always-on criterion."""
        silent = _plant(tmp_path, '20260830_132034_aaaaaaaa', 'my_set',
                        artifacts=False, reporting=RunReporting.NONE)

        report = _pruner(tmp_path).plan(PruneSelectors())

        assert [c.path for c in report.to_delete_uncommissioned] == [silent]

    def test_orphans_are_untouched_unless_asked_for(self, tmp_path):
        orphan = _orphan(tmp_path, 'test_consistency/20260830_132100_ffffffff')

        without = _pruner(tmp_path).plan(PruneSelectors())
        with_flag = _pruner(tmp_path).plan(PruneSelectors(orphans=True))

        assert orphan not in _deleted_paths(without)
        assert [c.path for c in with_flag.to_delete_orphans] == [orphan]

    def test_a_runs_own_substructure_is_never_an_orphan(self, tmp_path):
        """io/ holds files and has no header — and belongs to its run, not to the orphan list."""
        run_dir = _plant(tmp_path, '20260830_132034_aaaaaaaa', 'my_set')

        report = _pruner(tmp_path).plan(PruneSelectors(orphans=True))

        assert run_dir / IO_SUBDIR not in _deleted_paths(report)
        assert report.to_delete_orphans == []


class TestASweepIsAFamily:
    """
    A half-pruned sweep is worse than an unpruned one: its `ranked.csv` would rank runs that no
    longer exist. So the SWEEP is the unit `--keep-last` counts, never the combination.
    """

    @staticmethod
    def _sweep(root: Path, sweep_id: str, minutes: int) -> list:
        return [_plant(root, f'{sweep_id[6:]}_{i}{"a" * 7}', f'my_set__{sweep_id}_c00{i}',
                       parent=sweep_id, minutes=minutes + i)
                for i in range(3)]

    def test_keep_last_keeps_whole_sweeps_not_newest_combinations(self, tmp_path):
        older = self._sweep(tmp_path, 'sweep_20260830_130000', minutes=0)
        newer = self._sweep(tmp_path, 'sweep_20260830_140000', minutes=40)

        report = _pruner(tmp_path).plan(PruneSelectors(keep_last=1))
        deleted = _deleted_paths(report)

        # Every combination of the older sweep, none of the newer — never 1 of 3.
        assert all(path in deleted for path in older)
        assert not any(path in deleted for path in newer)

    def test_the_sweep_directory_goes_with_its_last_combination(self, tmp_path):
        self._sweep(tmp_path, 'sweep_20260830_130000', minutes=0)
        self._sweep(tmp_path, 'sweep_20260830_140000', minutes=40)
        sweep_dir = _roots(tmp_path).sweeps / 'sweep_20260830_130000'
        (sweep_dir / 'ranked.csv').write_text('x', encoding='utf-8')

        report = _pruner(tmp_path).plan(PruneSelectors(keep_last=1))

        assert sweep_dir in {c.path for c in report.emptied_sweep_dirs}

    def test_a_sweep_directory_is_not_an_orphan(self, tmp_path):
        """It holds files and has no header — correctly, because a sweep is not a run."""
        self._sweep(tmp_path, 'sweep_20260830_130000', minutes=0)
        sweep_dir = _roots(tmp_path).sweeps / 'sweep_20260830_130000'
        (sweep_dir / 'mount_build.log').write_text('x', encoding='utf-8')

        report = _pruner(tmp_path).plan(PruneSelectors(orphans=True))

        assert sweep_dir not in {c.path for c in report.to_delete_orphans}
        assert sweep_dir in {c.path for c in report.skipped_sweep_dirs}


class TestAnEmptyOrStaleTree:
    """
    The two states a tree reaches when someone clears it by hand, both worth surviving:
    nothing there at all, and an index that outlived its directories.
    """

    def test_an_empty_tree_is_a_no_op_and_still_writes_an_index(self, tmp_path):
        """A fresh clone, or a tree just wiped — the roots do not even exist yet."""
        pruner = _pruner(tmp_path)

        report = pruner.plan(PruneSelectors(keep_last=1, orphans=True))
        result = pruner.apply(report)

        assert report.all_deletions() == []
        assert result.failed == []
        assert result.indexed_after_rebuild == 0
        assert RunIndex(tmp_path / 'index.parquet').list_runs() == []

    def test_index_rows_whose_directory_is_gone_are_REPORTED(self, tmp_path):
        """
        The rebuild drops them either way — the point is that the dry run says so first.

        Without this the operator sees an empty report while three rows are about to vanish,
        which is the one thing a dry run must never do.
        """
        for i in range(3):
            _plant(tmp_path, f'2026083{i}_120000_aaaaaaa{i}', 'my_set', minutes=i)
        shutil.rmtree(_roots(tmp_path).simulation)

        report = _pruner(tmp_path).plan(PruneSelectors(orphans=True))

        assert len(report.stale_rows) == 3
        assert report.all_deletions() == [], 'nothing to delete — the directories are already gone'

    def test_the_rebuild_clears_the_stale_rows(self, tmp_path):
        _plant(tmp_path, '20260830_120000_aaaaaaaa', 'my_set')
        shutil.rmtree(_roots(tmp_path).simulation)

        pruner = _pruner(tmp_path)
        result = pruner.apply(pruner.plan(PruneSelectors()))

        assert result.indexed_after_rebuild == 0
        assert RunIndex(tmp_path / 'index.parquet').list_runs() == []


class TestApplyAndTheIndex:
    """The index is DERIVED — after a prune it must follow the tree, in both directions."""

    def test_apply_deletes_exactly_what_the_report_planned(self, tmp_path):
        doomed = _plant(tmp_path, '20260830_130000_aaaaaaaa', 'my_set', minutes=0)
        kept = _plant(tmp_path, '20260830_140000_bbbbbbbb', 'my_set', minutes=40)

        pruner = _pruner(tmp_path)
        report = pruner.plan(PruneSelectors(keep_last=1))
        result = pruner.apply(report)

        assert result.failed == []
        assert not doomed.exists()
        assert kept.exists()

    def test_the_invariant_holds_both_ways_after_a_prune(self, tmp_path):
        _plant(tmp_path, '20260830_130000_aaaaaaaa', 'my_set', minutes=0)
        _plant(tmp_path, '20260830_140000_bbbbbbbb', 'my_set', minutes=40)
        _orphan(tmp_path, 'test_consistency/20260830_132100_ffffffff')

        pruner = _pruner(tmp_path)
        pruner.apply(pruner.plan(PruneSelectors(keep_last=1, orphans=True)))

        index = RunIndex(tmp_path / 'index.parquet')
        headers = {p.parent for p in tmp_path.rglob('header.json')}
        listed = {index.run_dir(r.run_id) for r in index.list_runs()}
        assert listed == headers, 'a header without a row, or a row without a header'
        assert headers, 'the prune emptied the tree — this asserts nothing'

    def test_an_unremovable_directory_does_not_abort_the_rest(self, tmp_path, monkeypatch):
        """One failure must not cost the operator the whole prune."""
        _plant(tmp_path, '20260830_130000_aaaaaaaa', 'my_set', minutes=0)
        _plant(tmp_path, '20260830_140000_bbbbbbbb', 'my_set', minutes=40)

        pruner = _pruner(tmp_path)
        report = pruner.plan(PruneSelectors(keep_last=1))
        monkeypatch.setattr(
            'python.framework.reporting.store.run_tree_pruner.shutil.rmtree',
            lambda path: (_ for _ in ()).throw(OSError('busy')))

        result = pruner.apply(report)

        assert result.deleted == []
        assert len(result.failed) == len(report.all_deletions())


class TestTheLedgerKeepsItsRowsAndSaysWhy:
    """
    A prune removes RECORDS, never RESULTS — the two stores have opposite retention (#390).

    What the deletion costs is the ability to re-derive a row's figures from the entries behind
    them, so the row is stamped rather than dropped: it keeps saying what the run produced, and
    stops implying anyone can still check it (§48).
    """

    def test_a_pruned_run_s_row_survives_and_is_stamped(self, tmp_path):
        _plant(tmp_path, 'r1', 'set', artifacts=False, reporting=RunReporting.NONE)
        ledger = RunResultsLedger(tmp_path / 'ledger')
        ledger.append(_summary(), _provenance('r1'))

        pruner = _pruner(tmp_path)
        result = pruner.apply(pruner.plan(PruneSelectors()))

        assert result.ledger_rows_marked == 1
        row = ledger.read_rows()[0]
        assert row.records_pruned_at != ''
        assert row.net_pnl == 412.0        # the figures are untouched

    def test_a_run_that_stays_keeps_an_unstamped_row(self, tmp_path):
        _plant(tmp_path, 'keeper', 'set', reporting=RunReporting.EXPECTED)
        ledger = RunResultsLedger(tmp_path / 'ledger')
        ledger.append(_summary(), _provenance('keeper'))

        pruner = _pruner(tmp_path)
        result = pruner.apply(pruner.plan(PruneSelectors()))

        assert result.ledger_rows_marked == 0
        assert ledger.read_rows()[0].records_pruned_at == ''


def _summary() -> RunSummary:
    """A one-currency summary whose figures the stamp must not touch."""
    return RunSummary(
        run_id='r1',
        currencies=[RunSummaryCurrency(
            currency='USD', net_pnl=412.0, profit_factor=1.5, win_rate=0.5,
            account_max_drawdown=-10.0, total_fees=1.0, total_trades=7,
            winning_trades=4, losing_trades=3, expectancy=0.3,
            avg_win_r=1.0, avg_loss_r=-0.5, r_trade_count=7)])


def _provenance(run_id: str) -> RunProvenance:
    """Provenance for a finished run, enough for the ledger to write a row."""
    return RunProvenance(
        param_hash='h', status='ok', error=None, run_id=run_id,
        run_timestamp=_START, scenario_set_name='set', app_version='1.3.1',
        git_commit='abc1234', git_branch='main', git_dirty=False,
        decision_logic_type='CORE/aggressive_trend', decision_version='1.0.0',
        worker_versions={}, config_snapshot='{}', symbols=['BTCUSD'],
        data_broker_type='kraken_spot')


class TestKeepLastCountsPerParentKind:
    """
    `--keep-last N` is a quota, and a quota shared between two populations belongs to the
    louder one (#386).

    Both parent ids are a prefix plus a timestamp, and the prefixes are `sweep_` and `deploy_`.
    Sorting them together sorts by the PREFIX first, so `s` beats `d` on every comparison and a
    deployment can never outrank a sweep — not even one minted years later. Pooled, the quota is
    therefore not merely contended, it is unreachable for deployments whenever enough sweeps
    exist. That is the case below.
    """

    @staticmethod
    def _combinations(root: Path, sweep_id: str, minutes: int) -> list:
        return [_plant(root, f'{sweep_id[6:]}_{i}{"a" * 7}', f'my_set__{sweep_id}_c00{i}',
                       parent=sweep_id, minutes=minutes + i)
                for i in range(2)]

    @staticmethod
    def _sessions(root: Path, deployment_id: str, minutes: int) -> list:
        stamp = deployment_id.split('_', 1)[1]
        return [_plant(root, f'{stamp}_{i}{"b" * 7}', 'my_bot',
                       run_type=RUN_TYPE_LIVE, parent=deployment_id,
                       parent_kind=ParentKind.DEPLOYMENT, minutes=minutes + i)
                for i in range(2)]

    def test_a_deployment_survives_although_two_sweeps_fill_the_quota(self, tmp_path):
        self._combinations(tmp_path, 'sweep_20260830_130000', minutes=0)
        self._combinations(tmp_path, 'sweep_20260830_140000', minutes=40)
        sessions = self._sessions(tmp_path, 'deploy_20260918_091413', minutes=80)

        report = _pruner(tmp_path).plan(PruneSelectors(keep_last=2))

        # The live sessions are the NEWEST runs in the tree, and every sweep is older — yet
        # pooled they lost to the prefix, whole.
        assert not any(path in _deleted_paths(report) for path in sessions)

    def test_the_older_parent_of_each_kind_still_goes(self, tmp_path):
        older_sweep = self._combinations(tmp_path, 'sweep_20260830_130000', minutes=0)
        newer_sweep = self._combinations(tmp_path, 'sweep_20260830_140000', minutes=40)
        older_dep = self._sessions(tmp_path, 'deploy_20260901_091413', minutes=80)
        newer_dep = self._sessions(tmp_path, 'deploy_20260918_091413', minutes=120)

        deleted = _deleted_paths(_pruner(tmp_path).plan(PruneSelectors(keep_last=1)))

        # One of each kind survives — the selector still SELECTS, it just no longer lets one
        # kind spend the other's quota.
        assert all(path in deleted for path in older_sweep + older_dep)
        assert not any(path in deleted for path in newer_sweep + newer_dep)


def _aged(days: float) -> datetime:
    """A start time `days` in the past. Returns: the instant."""
    return datetime.now(timezone.utc) - timedelta(days=days)


class TestTheAgeSelector:
    """
    `--older-than` (#357) — the second question the tree is asked.

    "Keep the newest five" and "keep the last month" are different questions, and a bot that
    runs once a day answers them very differently from one running forty backtests an hour.
    """

    def test_older_than_removes_the_old_and_keeps_the_young(self, tmp_path):
        old_run = _plant(tmp_path, '20260601_120000_aaaaaaaa', 'my_set', started=_aged(100))
        young = _plant(tmp_path, '20260923_120000_bbbbbbbb', 'my_set', started=_aged(2))

        report = _pruner(tmp_path).plan(PruneSelectors(older_than=timedelta(days=30)))

        assert _deleted_paths(report) == {old_run}
        assert [c.path for c in report.kept_recent] == [young]

    def test_it_reaches_across_families_where_keep_last_cannot(self, tmp_path):
        """
        The gap this closes: `--keep-last` is per family, so a set run ONCE a year ago is its
        own newest and survives every count. Age is the only selector that can see it.
        """
        stale_only_child = _plant(tmp_path, '20250901_120000_cccccccc', 'abandoned_set',
                                  started=_aged(365))
        _plant(tmp_path, '20260923_120000_dddddddd', 'busy_set', started=_aged(1))

        by_count = _pruner(tmp_path).plan(PruneSelectors(keep_last=1))
        by_age = _pruner(tmp_path).plan(PruneSelectors(older_than=timedelta(days=30)))

        assert stale_only_child not in _deleted_paths(by_count)
        assert stale_only_child in _deleted_paths(by_age)

    def test_the_two_selectors_compose_as_KEEP_rules(self, tmp_path):
        """
        The composition, and it is the decision worth pinning: a run goes only when BOTH
        release it. Here the middle run is old enough to go but sits among the newest two of
        its family — one selector says delete, the other says keep, and KEEP wins.
        """
        newest = _plant(tmp_path, '20260923_120000_11111111', 'my_set', started=_aged(1))
        old_but_recent_enough_in_rank = _plant(
            tmp_path, '20260101_120000_22222222', 'my_set', started=_aged(90))
        oldest = _plant(tmp_path, '20250101_120000_33333333', 'my_set', started=_aged(400))

        report = _pruner(tmp_path).plan(
            PruneSelectors(keep_last=2, older_than=timedelta(days=30)))

        assert _deleted_paths(report) == {oldest}
        kept = {c.path for c in report.kept_complete} | {c.path for c in report.kept_recent}
        assert newest in kept
        assert old_but_recent_enough_in_rank in kept, (
            'a run among the newest of its family was deleted for being old — the two '
            'selectors were composed as delete rules, not as keep rules')

    def test_neither_selector_deletes_nothing(self, tmp_path):
        """A prune with no selector is a report, not an action. Unchanged by #357."""
        _plant(tmp_path, '20250101_120000_44444444', 'my_set', started=_aged(400))

        report = _pruner(tmp_path).plan(PruneSelectors())

        assert _deleted_paths(report) == set()

    def test_a_run_with_no_start_time_is_kept_and_reported(self, tmp_path):
        """
        An age nobody can measure is not a reason to delete. The row is reported in its own
        group rather than silently joining the kept — 'not measurable' and 'recent' are two
        different statements about a directory somebody may want back.
        """
        assert RunTreePruner._is_older_than(
            RunInfo(run_id='x', group='simulation', name='my_set', start_time=''),
            datetime.now(timezone.utc), timedelta(days=30)) is None

    def test_an_unparseable_start_time_is_treated_the_same(self, tmp_path):
        """Never 'old' — that is the direction that deletes."""
        assert RunTreePruner._is_older_than(
            RunInfo(run_id='x', group='simulation', name='my_set', start_time='not a date'),
            datetime.now(timezone.utc), timedelta(days=30)) is None

    def test_the_guard_still_holds_against_an_ancient_run(self, tmp_path):
        """
        Age is a selector like any other, so it must not reach past the guard: a run that
        crashed before reporting is the only record of that failure, however old it is.
        """
        crashed = _plant(tmp_path, '20250101_120000_55555555', 'my_set',
                         artifacts=False, started=_aged(400))
        evidence = _plant(tmp_path, '20250101_130000_66666666', 'live_profile',
                          run_type=RUN_TYPE_LIVE, field_study=True, started=_aged(400))

        report = _pruner(tmp_path).plan(PruneSelectors(older_than=timedelta(days=30)))

        assert _deleted_paths(report) == set()
        assert [c.path for c in report.kept_incomplete] == [crashed]
        assert [c.path for c in report.kept_field_study] == [evidence]

    def test_an_age_pruned_run_keeps_its_ledger_row_and_gets_it_stamped(self, tmp_path):
        """
        The ledger and the tree keep opposite retention on purpose (#390), and an age
        selector is exactly the shape that looks like it should apply to both. The row stays,
        keeps its figures, and says its evidence is gone.
        """
        _plant(tmp_path, 'r1', 'my_set', started=_aged(400))
        ledger = RunResultsLedger(tmp_path / 'ledger')
        ledger.append(_summary(), _provenance('r1'))

        pruner = _pruner(tmp_path)
        result = pruner.apply(pruner.plan(PruneSelectors(older_than=timedelta(days=30))))

        assert result.ledger_rows_marked == 1
        row = ledger.read_rows()[0]
        assert row.records_pruned_at != ''
        assert row.net_pnl == 412.0
