"""
The config directory (#554) — every file that can start a run, read without running it.

The listing it replaces had four properties a directory must not have, and each one is pinned
here from the side it failed on: it WROTE a record store on every read, a file that did not parse
vanished without a word, AutoTrader profiles were not in it at all, and its answer cost a full
loader pass per file. Every test builds its own tree under tmp and points the directory at it
through the same getters the real one reads, so nothing here touches the operator's files.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from python.configuration.autotrader import (
    autotrader_config_loader as autotrader_config_loader_module,
)
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.config_directory import config_directory as config_directory_module
from python.framework.config_directory.config_directory import (
    ConfigDirectory,
    clear_config_directory_memo,
)
from python.framework.config_directory.config_directory_index import ConfigDirectoryIndex
from python.framework.exceptions.config_name_errors import ConfigNameConflictError
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from python.framework.types.api.report_types import RunHeader, RunSummary, RunSummaryCurrency
from python.framework.types.config_directory_types import (
    ConfigKind,
    ConfigOrigin,
    ConfigReadStatus,
)
from python.framework.types.log_layout_types import RUN_TYPE_AUTOTRADER, RUN_TYPE_SIMULATION
from python.framework.types.run_outcome_types import RunOutcome
from python.framework.types.run_results_types import RunProvenance
from python.framework.validators import config_name_validator
from python.framework.validators.config_name_validator import (
    clear_config_name_memo,
    refuse_config_name_conflict,
)
from python.scenario import scenario_config_loader as scenario_config_loader_module
from python.scenario.scenario_config_loader import ScenarioConfigLoader


class _Roots:
    """The getters the directory reads, answered from one tmp tree."""

    def __init__(self, root: Path):
        self._root = root

    def get_user_scenario_sets_path(self) -> str:
        return str(self._root / 'user_configs' / 'scenario_sets')

    def get_user_autotrader_profiles_path(self) -> str:
        return str(self._root / 'user_configs' / 'autotrader_profiles')

    def get_user_algo_dirs(self) -> list:
        return [str(self._root / 'user_algos')]

    def get_scenario_sets_path(self) -> str:
        return str(self._root / 'configs' / 'scenario_sets')

    def get_autotrader_profiles_path(self) -> str:
        return str(self._root / 'configs' / 'autotrader_profiles')

    def get_config_directory_path(self) -> str:
        return str(self._root / 'cache')

    def get_file_logging_config_object(self):
        return SimpleNamespace(run_index=self._root / 'runs' / 'runs_index.parquet')

    def get_run_ledger_path(self) -> str:
        return str(self._root / 'runs' / 'ledger')


def _set(name: str, scenarios: list, strategy: dict = None) -> dict:
    """A scenario set as a file declares it."""
    return {'scenario_set_name': name,
            'global': {'strategy_config': strategy or {
                'decision_logic_type': 'CORE/aggressive_trend',
                'worker_instances': {'rsi_fast': 'CORE/rsi'}}},
            'scenarios': scenarios}


def _scenario(name: str, symbol: str = 'BTCUSD', broker: str = 'kraken_spot', **extra) -> dict:
    """One scenario as a file declares it."""
    return {'scenario_name': name, 'symbol': symbol, 'data_broker_type': broker,
            'start_date': '2026-01-01T00:00:00+00:00', 'end_date': '2026-01-02T00:00:00+00:00',
            **extra}


def _profile(**extra) -> dict:
    """An AutoTrader profile as a file declares it."""
    return {'profile_name': 'btc_mock', 'bot_id': 'mybot01', 'symbol': 'BTCUSD',
            'broker_type': 'kraken_spot',
            'strategy_config': {'decision_logic_type': 'CORE/simple_consensus',
                                'worker_instances': {'obv': 'CORE/obv'}}, **extra}


def _write(path: Path, content) -> Path:
    """Write JSON — or raw text — to a file, creating its folder."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content if isinstance(content, str) else json.dumps(content),
                    encoding='utf-8')
    return path


@pytest.fixture
def tree(tmp_path):
    """An empty configuration tree and a directory pointed at it; the memos cleared around it."""
    clear_config_directory_memo()
    clear_config_name_memo()
    yield tmp_path
    clear_config_directory_memo()
    clear_config_name_memo()


def _directory(tree: Path) -> ConfigDirectory:
    """A directory over the tmp tree."""
    return ConfigDirectory(_Roots(tree))


def _rows(tree: Path) -> dict:
    """Every served row by file name, walked now."""
    return {row.file: row for row in _directory(tree).list_configs(refresh=True).rows}


class TestWhatAFileDeclares:

    def test_a_scenario_set_counts_its_disabled_scenarios_as_declared(self, tree):
        _write(tree / 'configs/scenario_sets/my_set.json', _set('my_set', [
            _scenario('a'), _scenario('b', symbol='ETHUSD'),
            _scenario('off', symbol='SOLUSD', enabled=False)]))

        row = _rows(tree)['my_set.json']

        assert row.kind == ConfigKind.SCENARIO_SET and row.status == ConfigReadStatus.READABLE
        assert (row.scenarios_declared, row.scenarios_enabled) == (3, 2)
        assert row.symbols == ['BTCUSD', 'ETHUSD'], 'a disabled scenario does not run'
        assert (row.broker_types, row.market_types) == (['kraken_spot'], ['crypto'])

    def test_the_strategy_is_the_one_the_cascade_resolves(self, tree):
        """A scenario overriding the decision logic shows both — the loader's own merge."""
        _write(tree / 'configs/scenario_sets/my_set.json', _set('my_set', [
            _scenario('a'),
            _scenario('b', strategy_config={'decision_logic_type': 'CORE/cautious_macd',
                                            'worker_instances': {'macd': 'CORE/macd'}})]))

        row = _rows(tree)['my_set.json']

        assert row.decision_logics == ['CORE/aggressive_trend', 'CORE/cautious_macd']
        assert row.workers == ['CORE/macd', 'CORE/rsi']

    def test_a_profile_is_one_unit_with_its_live_facts(self, tree):
        _write(tree / 'configs/autotrader_profiles/production/btc_live.json',
               _profile(adapter_type='live', dry_run=False))
        _write(tree / 'configs/autotrader_profiles/mock/btc_mock.json', _profile())

        rows = _rows(tree)
        live, mock = rows['btc_live.json'], rows['btc_mock.json']

        assert live.kind == ConfigKind.AUTOTRADER_PROFILE and live.folder == 'production'
        assert (live.bot_id, live.adapter_type, live.dry_run_declared) == ('mybot01', 'live', False)
        assert (mock.adapter_type, mock.dry_run_declared) == ('mock', None), (
            "the loader's default adapter, and no declaration means the broker decides")
        assert (live.scenarios_declared, live.decision_logics) == (1, ['CORE/simple_consensus'])

    def test_a_broker_the_market_config_does_not_know_is_unknown_not_a_crash(self, tree):
        _write(tree / 'configs/scenario_sets/odd.json',
               _set('odd', [_scenario('a', broker='no_such_broker')]))

        assert _rows(tree)['odd.json'].market_types == ['unknown']


class TestAFileBeingEditedIsARowNotAnError:

    def test_broken_json_is_an_unreadable_row_with_its_line(self, tree):
        _write(tree / 'user_configs/scenario_sets/my_wip.json',
               '{"scenario_set_name": "x",\n  oops')

        row = _rows(tree)['my_wip.json']

        assert row.status == ConfigReadStatus.UNREADABLE and row.kind is None
        assert row.reason.startswith('line 2')
        assert row.origin == ConfigOrigin.USER_CONFIGS

    def test_a_marker_with_the_wrong_shape_is_unreadable(self, tree):
        _write(tree / 'configs/scenario_sets/bad.json',
               {'scenario_set_name': 'bad', 'scenarios': 'not a list'})

        row = _rows(tree)['bad.json']

        assert row.status == ConfigReadStatus.UNREADABLE
        assert '`scenarios`' in row.reason

    def test_json_that_is_no_configuration_is_not_served(self, tree):
        """An analysis result beside a strategy is JSON, and it is not a run configuration."""
        _write(tree / 'user_algos/my_algo/analysis/result.json', {'rows': [1, 2, 3]})

        assert 'result.json' not in _rows(tree)


class TestWhichCopyWins:

    def test_the_operators_copy_wins_and_names_what_it_shadows(self, tree):
        """The resolver's order: user_configs over user_algos over configs."""
        _write(tree / 'configs/scenario_sets/my_set.json', _set('shipped', [_scenario('a')]))
        _write(tree / 'user_algos/my_algo/my_set.json', _set('algo', [_scenario('a')]))
        _write(tree / 'user_configs/scenario_sets/my_set.json', _set('mine', [_scenario('a')]))

        row = _rows(tree)['my_set.json']

        assert (row.origin, row.name) == (ConfigOrigin.USER_CONFIGS, 'mine')
        assert row.shadowed == [ConfigOrigin.USER_ALGOS, ConfigOrigin.CONFIGS]

    def test_the_operators_own_layout_is_not_served(self, tree):
        _write(tree / 'user_algos/my_algo/deep/my_set.json', _set('algo', [_scenario('a')]))

        assert _rows(tree)['my_set.json'].folder == ''


class TestOneNameBelongsToOneKind:
    """
    A run records its configuration by file name alone, so a scenario set and a profile of one
    name would make every such record ambiguous. Refused — in the directory and at run start.
    """

    def test_a_set_and_a_profile_of_one_name_are_one_conflict(self, tree):
        _write(tree / 'configs/scenario_sets/same_name.json', _set('a set', [_scenario('a')]))
        _write(tree / 'user_configs/autotrader_profiles/same_name.json', _profile())

        row = _rows(tree)['same_name.json']

        assert row.status == ConfigReadStatus.UNREADABLE
        assert 'also the name of a scenario set (configs)' in row.reason

    def test_a_copy_of_the_same_kind_is_precedence_not_a_conflict(self, tree):
        _write(tree / 'configs/scenario_sets/my_set.json', _set('shipped', [_scenario('a')]))
        _write(tree / 'user_configs/scenario_sets/my_set.json', _set('mine', [_scenario('a')]))

        assert _rows(tree)['my_set.json'].status == ConfigReadStatus.READABLE

    def test_the_run_start_is_refused_for_either_kind(self, tree, monkeypatch):
        _write(tree / 'configs/scenario_sets/same_name.json', _set('a set', [_scenario('a')]))
        _write(tree / 'user_algos/my_algo/same_name.json', _profile())
        monkeypatch.setattr(config_name_validator, 'AppConfigManager', lambda: _Roots(tree))

        with pytest.raises(ConfigNameConflictError,
                           match='an AutoTrader profile \\(user_algos\\)'):
            refuse_config_name_conflict('same_name.json', ConfigKind.SCENARIO_SET)
        with pytest.raises(ConfigNameConflictError, match='a scenario set \\(configs\\)'):
            refuse_config_name_conflict('same_name.json', ConfigKind.AUTOTRADER_PROFILE)
        refuse_config_name_conflict('another_name.json', ConfigKind.SCENARIO_SET)   # no conflict

    def test_both_loaders_ask_before_a_run_starts(self, monkeypatch, tmp_path):
        """The refusal is wired into where each pipeline loads its configuration."""
        def refuse(file_name, kind):
            raise ConfigNameConflictError(f'{kind.value}:{file_name}')

        monkeypatch.setattr(scenario_config_loader_module, 'refuse_config_name_conflict', refuse)
        monkeypatch.setattr(autotrader_config_loader_module, 'refuse_config_name_conflict', refuse)
        scenario_set = _write(tmp_path / 'probe_set.json', _set('probe', [_scenario('a')]))
        profile = _write(tmp_path / 'probe_profile.json', _profile())

        with pytest.raises(ConfigNameConflictError, match='scenario_set:probe_set.json'):
            ScenarioConfigLoader().load_config(str(scenario_set))
        with pytest.raises(ConfigNameConflictError, match='autotrader_profile:probe_profile.json'):
            load_autotrader_config(str(profile))


class TestTheCacheReadsOnlyWhatChanged:

    @pytest.fixture
    def reads(self, monkeypatch):
        """Count the files the directory actually opens and parses."""
        opened = []
        real = config_directory_module.read_config_file

        def counting(candidate, market_type_of):
            opened.append(candidate.path.name)
            return real(candidate, market_type_of)

        monkeypatch.setattr(config_directory_module, 'read_config_file', counting)
        return opened

    def test_an_unchanged_file_is_not_read_again(self, tree, reads):
        _write(tree / 'configs/scenario_sets/a.json', _set('a', [_scenario('x')]))
        _write(tree / 'configs/scenario_sets/b.json', _set('b', [_scenario('x')]))
        _rows(tree)
        reads.clear()

        _rows(tree)

        assert reads == [], 'the cache answered for both files'

    def test_a_changed_file_is_read_again_and_a_deleted_one_disappears(self, tree, reads):
        changed = _write(tree / 'configs/scenario_sets/a.json', _set('a', [_scenario('x')]))
        deleted = _write(tree / 'configs/scenario_sets/b.json', _set('b', [_scenario('x')]))
        _rows(tree)
        reads.clear()
        _write(changed, _set('a', [_scenario('x'), _scenario('y')]))
        deleted.unlink()

        rows = _rows(tree)

        assert reads == ['a.json']
        assert rows['a.json'].scenarios_declared == 2 and 'b.json' not in rows

    def test_a_new_logic_version_reads_everything_again(self, tree, reads, monkeypatch):
        _write(tree / 'configs/scenario_sets/a.json', _set('a', [_scenario('x')]))
        _rows(tree)
        reads.clear()
        monkeypatch.setattr(ConfigDirectoryIndex, 'LOGIC_VERSION',
                            ConfigDirectoryIndex.LOGIC_VERSION + 1)

        _rows(tree)

        assert reads == ['a.json']

    def test_within_the_freshness_window_nothing_is_walked(self, tree, monkeypatch):
        _write(tree / 'configs/scenario_sets/a.json', _set('a', [_scenario('x')]))
        directory = _directory(tree)
        directory.list_configs(refresh=True)
        walks = []
        real = config_directory_module.discover_config_files
        monkeypatch.setattr(config_directory_module, 'discover_config_files',
                            lambda app_config: walks.append(1) or real(app_config))

        directory.list_configs()
        assert walks == [], 'served from the memo'
        directory.list_configs(refresh=True)
        assert walks == [1], 'refresh walks at once'

    def test_a_read_writes_nothing_but_its_own_cache(self, tree):
        """The listing it replaces froze every file into the run-config store."""
        _write(tree / 'configs/scenario_sets/a.json', _set('a', [_scenario('x')]))
        before = {path for path in tree.rglob('*') if path.is_file()}

        _rows(tree)

        written = {path for path in tree.rglob('*') if path.is_file()} - before
        assert written and all(path.is_relative_to(tree / 'cache') for path in written)


class TestTheRunsAreJoinedFromTheRunIndex:

    @staticmethod
    def _run(tree: Path, run_id: str, run_type: str, snapshot: str, hour: int) -> None:
        """Register one run the way a real one registers itself."""
        run_dir = tree / 'runs' / run_type / 'x' / run_id
        run_dir.mkdir(parents=True)
        RunIndex(tree / 'runs' / 'runs_index.parquet').register_run(RunHeader(
            run_id=run_id, start_time=datetime(2026, 9, 25, hour, tzinfo=timezone.utc),
            run_type=run_type, run_name='x', config_snapshot=snapshot), run_dir)

    def test_each_file_counts_the_runs_of_its_own_pipeline(self, tree):
        _write(tree / 'configs/scenario_sets/my_set.json', _set('my_set', [_scenario('a')]))
        _write(tree / 'configs/autotrader_profiles/mock/my_bot.json', _profile())
        _write(tree / 'configs/scenario_sets/never_ran.json', _set('never', [_scenario('a')]))
        self._run(tree, '20260925_080000_aaaaaaaa', RUN_TYPE_SIMULATION, 'my_set.json', 8)
        self._run(tree, '20260925_090000_bbbbbbbb', RUN_TYPE_SIMULATION, 'my_set.json', 9)
        self._run(tree, '20260925_100000_cccccccc', RUN_TYPE_AUTOTRADER, 'my_bot.json', 10)
        # A live run naming a scenario set's file is not a run OF that set.
        self._run(tree, '20260925_110000_dddddddd', RUN_TYPE_AUTOTRADER, 'my_set.json', 11)

        rows = _rows(tree)

        assert (rows['my_set.json'].run_count, rows['my_set.json'].last_run_id) == (
            2, '20260925_090000_bbbbbbbb')
        assert rows['my_bot.json'].run_count == 1
        assert (rows['never_ran.json'].run_count, rows['never_ran.json'].last_run_at) == (0, '')

    def test_the_detail_lists_its_scenarios_and_its_runs_newest_first(self, tree):
        _write(tree / 'configs/scenario_sets/my_set.json', _set('my_set', [
            _scenario('a'), _scenario('b', max_ticks=5000, end_date=None, enabled=False)]))
        self._run(tree, '20260925_080000_aaaaaaaa', RUN_TYPE_SIMULATION, 'my_set.json', 8)
        self._run(tree, '20260925_090000_bbbbbbbb', RUN_TYPE_SIMULATION, 'my_set.json', 9)

        detail = _directory(tree).detail('my_set.json')

        assert [s.name for s in detail.scenarios] == ['a', 'b']
        assert (detail.scenarios[1].max_ticks, detail.scenarios[1].enabled) == (5000, False)
        assert detail.scenarios[0].decision_logic == 'CORE/aggressive_trend'
        assert detail.runs == ['20260925_090000_bbbbbbbb', '20260925_080000_aaaaaaaa']

    def test_an_unknown_file_has_no_detail(self, tree):
        assert _directory(tree).detail('nope.json') is None

    def test_the_newest_run_says_what_it_did(self, tree):
        """The run list's own ledger join, for the newest run of each file."""
        _write(tree / 'configs/scenario_sets/my_set.json', _set('my_set', [_scenario('a')]))
        _write(tree / 'configs/scenario_sets/unbooked.json', _set('unbooked', [_scenario('a')]))
        self._run(tree, '20260925_080000_aaaaaaaa', RUN_TYPE_SIMULATION, 'my_set.json', 8)
        self._run(tree, '20260925_090000_bbbbbbbb', RUN_TYPE_SIMULATION, 'unbooked.json', 9)
        RunResultsLedger(tree / 'runs' / 'ledger').append(
            RunSummary(run_id='20260925_080000_aaaaaaaa', currencies=[RunSummaryCurrency(
                currency='USD', net_pnl=12.5, profit_factor=0.0, win_rate=0.0,
                account_max_drawdown=0.0, total_fees=0.0, gross_profit=0.0, gross_loss=0.0,
                total_trades=4, winning_trades=0, losing_trades=0, expectancy=0.0,
                avg_win_r=0.0, avg_loss_r=0.0, r_trade_count=0)],
                orders_sent=0, orders_executed=0, orders_rejected=0, sl_tp_triggered=0,
                unit_count=1),
            RunProvenance(
                param_hash='h', status='ok', error=None, run_id='20260925_080000_aaaaaaaa',
                run_timestamp=datetime(2026, 9, 25, 8, tzinfo=timezone.utc),
                scenario_set_name='my_set', app_version='1.4.0', git_commit='abc', git_branch='dev',
                git_dirty=False, decision_logic_type='CORE/aggressive_trend', decision_version='1',
                worker_versions={}, strategy_config_json='{}', symbols=['BTCUSD'],
                data_broker_type='kraken_spot', run_outcome=RunOutcome.SUCCESS, error_count=0,
                warning_count=1, log_warning_count=3))

        rows = _rows(tree)

        figures = rows['my_set.json'].last_run_figures
        assert [(r.currency, r.net_pnl, r.total_trades) for r in figures.results] == [
            ('USD', 12.5, 4)]
        assert (figures.run_outcome, figures.warning_count) == (RunOutcome.SUCCESS, 1)
        # A run the ledger does not know says nothing, rather than a clean zero.
        assert rows['unbooked.json'].last_run_figures is None
