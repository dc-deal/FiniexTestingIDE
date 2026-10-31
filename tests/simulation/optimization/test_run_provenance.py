"""
Run-provenance (session) tests (#403 · 5.a).

`build_run_provenance_from_session` is the AutoTrader counterpart to the sim
`build_run_provenance`: it lets an AutoTrader session append to the same Run Results Ledger.
The key property is sim/live parity — the profile's strategy_config has the same shape as a sim
scenario's, so the param_hash is computed identically and the session's row is directly
comparable to the backtest.
Built against the REAL AutoTraderConfig / WarningsErrorsReport, never stand-ins.
"""

from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Optional
from unittest.mock import patch

import pytest

from python.framework.constants.run_layout_constants import RUN_TYPE_AUTOTRADER
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.reporting.builders.warnings_errors_report_builder import (
    build_warnings_errors_report_from_session,
)
from python.framework.reporting.io.run_header_io import write_run_header
from python.framework.reporting.store.run_provenance_builder import (
    build_run_provenance,
    consumption_record,
    build_run_provenance_from_session,
)
from python.framework.types.api.report_types import RunHeader, WarningsErrorsReport
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.git_info_types import GitInfo
from python.framework.types.run_kind_types import OrdersTo
from python.framework.types.run_origin_types import (
    CodeIdentity,
    RepositoryStand,
    RepositoryState,
)
from python.framework.types.run_results_types import RunProvenance
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.utils.config_fingerprint_utils import generate_config_fingerprint

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'

_TS = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _scenario_reading(versions, classes, grades, bases=()) -> SingleScenario:
    """
    A scenario carrying the input lists the mount fills.

    Args:
        versions: The data format versions of the files it read
        classes: Their resolved origin classes
        grades: Their resolved evidence grades
        bases: The price bases of the BAR files it mounted — a separate archive from the
            three above, which is why it is counted separately and defaults to none

    Returns:
        A scenario with only those fields set — nothing else is read here
    """
    scenario = SingleScenario(
        name='s', scenario_index=0, symbol='BTCUSD',
        data_broker_type='kraken_spot', start_date='2026-01-01')
    scenario.data_format_versions = list(versions)
    scenario.origin_classes = list(classes)
    scenario.origin_evidence_grades = list(grades)
    scenario.price_bases = list(bases)
    return scenario


def _config() -> AutoTraderConfig:
    return AutoTraderConfig(
        profile_name='my_profile', symbol='BTCUSD', broker_type='kraken_spot',
        strategy_config={'decision_logic_type': 'CORE/aggressive_trend',
                         'worker_instances': {}})


def _session_provenance(config: AutoTraderConfig,
                        report: Optional[WarningsErrorsReport] = None,
                        replayed_scenario: Optional[SingleScenario] = None) -> RunProvenance:
    """
    The provenance of a session that has no run directory — these tests pin the mapping,
    not the header read, which `tests/framework/reporting/test_run_origin.py` covers.

    Args:
        config: The profile
        report: The session's warnings/errors report, or None
        replayed_scenario: A mock session's scenario as the mount filled it, or None for a
            live-adapter session

    Returns:
        The provenance
    """
    return build_run_provenance_from_session(
        config, _RUN_ID, _TS, report, run_dir=None, logger=get_global_logger(),
        replayed_scenario=replayed_scenario)


class TestSessionProvenance:
    """An AutoTrader session maps onto the same RunProvenance the ledger ranks over."""

    def test_maps_config_to_provenance(self):
        p = _session_provenance(_config())
        # The MINTED id, passed in — no longer read off the directory name (#475).
        assert p.run_id == _RUN_ID
        assert p.run_timestamp == _TS
        assert p.scenario_set_name == 'my_profile'
        assert p.symbols == ['BTCUSD']
        assert p.data_broker_type == 'kraken_spot'
        # Live is never swept.
        assert p.sweep_id is None and p.sweep_params is None
        assert p.sweep_objective is None and p.sweep_maximize is None

    def test_param_hash_parity_with_backtest(self):
        # The whole point of 5.a: the live param_hash is the SAME fingerprint the sim run
        # produces for the same strategy_config — so live + backtest rows compare directly.
        cfg = _config()
        p = _session_provenance(cfg)
        assert p.param_hash == generate_config_fingerprint(cfg.strategy_config)

    def test_status_ok_without_report(self):
        p = _session_provenance(_config())
        assert p.status == 'ok'
        assert p.error is None

    def test_status_error_on_emergency(self):
        # An emergency session (every unit failed) → a status='error' ledger row, never absent.
        result = AutoTraderResult(emergency_reason='boom', shutdown_mode='emergency')
        report = build_warnings_errors_report_from_session(_RUN_ID, result, 'my_profile', 'BTCUSD')
        p = _session_provenance(_config(), report)
        assert p.status == 'error'
        assert 'boom' in p.error


class TestWhereTheOrdersWent:
    """
    The ledger row says whether a run's orders went to the venue (#362), by the rule its header
    was stamped with — so the row and the header cannot disagree about real money.
    """

    def test_a_mock_session_simulates(self):
        config = replace(_config(), adapter_type='mock')

        assert _session_provenance(config).orders_to is OrdersTo.SIMULATED

    def test_real_money_goes_to_the_venue(self):
        config = replace(_config(), adapter_type='live')
        with patch('python.framework.autotrader.dry_run_resolver.MarketConfigManager') as manager:
            manager.return_value.get_dry_run.return_value = False

            assert _session_provenance(config).orders_to is OrdersTo.VENUE

    def test_a_backtest_simulates(self, tmp_path):
        scenario = SingleScenario(
            name='s0', scenario_index=0, symbol='BTCUSD', data_broker_type='kraken_spot',
            start_date='2026-01-01',
            strategy_config={'decision_logic_type': 'CORE/aggressive_trend',
                             'worker_instances': {}})
        scenario_set = SimpleNamespace(
            logger=SimpleNamespace(get_log_dir=lambda: tmp_path),
            printed_summary_logger=get_global_logger(), run_timestamp=_TS,
            scenario_set_name='my_set')

        provenance = build_run_provenance(
            SimpleNamespace(single_scenario_list=[scenario], broker_scenario_map={}),
            scenario_set, _RUN_ID)

        assert provenance.orders_to is OrdersTo.SIMULATED


class TestWhatARunConsumed:
    """
    A finished run records WHICH DATA it was produced over (#518).

    It lives here rather than in the run header, and that placement is the point. The header
    is written at the run's START — before anything is mounted, with no update path, because a
    run that crashes is exactly the run somebody needs to identify — so it cannot know. This
    record is built from a FINISHED run.

    The argument is `logic_version`'s, one drawer over: that column exists because a ranking
    cannot otherwise tell it is comparing a measure taken one way against one taken another.
    These say the same about the INPUT, and the thirty-day parity proof rests on being able to
    show that a live run and its backtest read the same archive.
    """

    def test_a_live_session_says_it_read_a_stream_rather_than_leaving_it_blank(self):
        """
        The emptiness has to MEAN something, or it is indistinguishable from a broken recorder.

        A live-adapter session consumes a socket and has no archive input, so its three joined strings
        are empty by construction. Without `input_plane` that is the same bytes as a sim row
        whose recording failed — the exact failure mode this project spent the day removing
        from its producers.
        """
        p = _session_provenance(_config())

        assert p.input_plane == 'stream'
        assert p.data_format_versions == ''
        assert p.origin_classes == ''
        assert p.origin_evidence_grades == ''
        assert p.input_files == 0
        # The ONE field that is filled for a live-adapter session, and deliberately so: such a
        # session renders its bars at runtime, so there is no file to stamp and nothing can be
        # out of date with the declaration. Blank here would defeat the field — the parity proof
        # has to compare the live-adapter session's price basis against the backtest's, and
        # `input_plane` is what says this one is DECLARED rather than measured (§31c).
        assert p.price_bases == 'order_driven'

    def test_a_mock_session_records_the_archive_it_replayed(self):
        """
        A mock session reads tick files, and its record has to say so.

        Every AutoTrader session used to be stamped `stream` with the declared price basis,
        so a mock session that replayed the archive was recorded as "a socket, nothing read" —
        the ledger held that on every mock row on disk. It now takes its consumption record
        from its own scenario, exactly as a backtest does.
        """
        replayed = _scenario_reading(['1.7.0'], ['production'], ['stamped'],
                                     bases=['order_driven'])

        p = _session_provenance(_config(), replayed_scenario=replayed)

        assert p.input_plane == 'archive'
        assert p.data_format_versions == '1.7.0'
        assert p.origin_classes == 'production'
        assert p.input_files == 1
        assert p.price_bases == 'order_driven', 'measured from the mounted bars, not declared'

    def test_a_sim_run_reports_the_distinct_values_and_the_counts(self):
        """
        Both halves are needed, and neither substitutes for the other.

        The joined strings say WHAT was read and collapse multiplicity; the counts say HOW MUCH
        and cannot be recovered from them. "This run read production data" and "this run read
        three development files out of forty-one" are different answers to different questions.
        """
        scenarios = [
            _scenario_reading(['1.5.0', '1.5.0'], ['production', 'production'],
                              ['attested', 'attested']),
            _scenario_reading(['1.7.0'], ['development'], ['stamped']),
        ]

        record = consumption_record(scenarios)

        assert record['input_plane'] == 'archive'
        assert record['data_format_versions'] == '1.5.0,1.7.0'
        assert record['origin_classes'] == 'development,production'
        assert record['origin_evidence_grades'] == 'attested,stamped'
        assert record['input_files'] == 3
        assert record['unstamped_input_files'] == 3

    def test_a_half_re_rendered_archive_answers_with_both_bases(self):
        """
        The condition the stamp exists to EXPOSE, not to smooth over.

        A re-render walks the archive file by file. While it runs, one scenario legitimately
        mounts bars written under the new basis beside bars still holding the old answer — and
        a run over that mixture is not comparable with one over either half. Collapsing it to a
        single value, or filling the gap from the broker's declaration, is exactly the
        borrowing the stamp prevents: config describes what a render WOULD produce, and half
        the files on disk disagree with it (§31c).
        """
        scenarios = [_scenario_reading(
            ['1.7.0'], ['production'], ['stamped'],
            bases=['order_driven', 'unknown', 'order_driven'])]

        record = consumption_record(scenarios)

        assert record['price_bases'] == 'order_driven,unknown'
        # The file COUNT is unchanged: the basis comes from the bar archive and the count from
        # ticks plus signals, so one is not a subset of the other.
        assert record['input_files'] == 1

    def test_a_scenario_that_mounted_no_bars_records_no_basis(self):
        """Empty is the honest answer; the declaration would be a guess in its shape."""
        record = consumption_record([_scenario_reading(['1.7.0'], ['production'], ['stamped'])])

        assert record['price_bases'] == ''

    def test_only_production_AND_stamped_counts_as_stamped(self):
        """
        The count asks the shared rule rather than spelling it out a third time.

        `production` from a producer's own stamp and `production` from a claim we recorded are
        the same word and a different fact — collapsing them is what would let a claim present
        itself as a measurement.
        """
        scenarios = [_scenario_reading(
            ['1.7.0'] * 4,
            ['production', 'production', 'development', 'unknown'],
            ['stamped', 'attested', 'stamped', 'unknown'])]

        record = consumption_record(scenarios)

        assert record['input_files'] == 4
        assert record['unstamped_input_files'] == 3

    def test_a_run_that_read_nothing_says_so_without_inventing_a_value(self):
        record = consumption_record([])

        assert record['input_plane'] == 'archive'
        assert record['input_files'] == 0
        assert record['origin_classes'] == ''


# What git would answer by the time a session writes its row: another commit than it started on.
_GIT_AT_THE_END = GitInfo(branch='later', commit='fffffff', date=_TS, message='checked out later',
                          dirty=False, uncommitted_count=0)
_GET_GIT_INFO = 'python.framework.reporting.store.run_provenance_builder.get_git_info'


def _session_from_header(tmp_path, identity: Optional[CodeIdentity]) -> RunProvenance:
    """
    The provenance of a session whose header recorded the given code identity.

    Args:
        tmp_path: The session's run directory
        identity: What the header recorded, None for a header that carries none

    Returns:
        The provenance, built while git answers something else than the header
    """
    write_run_header(RunHeader(run_id=_RUN_ID, start_time=_TS, run_type=RUN_TYPE_AUTOTRADER,
                               run_name='my_profile', app_version='1.4.0', git_commit='abc1234',
                               code_identity=identity), tmp_path)
    with patch(_GET_GIT_INFO, return_value=_GIT_AT_THE_END):
        return build_run_provenance_from_session(
            _config(), _RUN_ID, _TS, run_dir=tmp_path, logger=get_global_logger())


class TestTheCodeStandReadsTheHeader:
    """
    The ledger's `code_stand` says which code a session started on (#559). The row is written long
    after the start, so it comes from the header, never from what git answers then.
    """

    def test_it_states_what_the_header_recorded_and_not_what_git_answers(self, tmp_path):
        identity = CodeIdentity(
            framework=RepositoryState(root='/srv/releases/v1', commit='a1b2c3d',
                                      commit_subject='Fix the fee rounding',
                                      recent_commits=['a1b2c3d4e5f6', '0f1e2d3c4b5a']),
            repositories=[RepositoryState(root='/srv/home/my_strategy', commit='2f054f5',
                                          dirty=True, diff_hash='9f2c' * 16)])

        stand = _session_from_header(tmp_path, identity).code_stand

        assert stand.repositories == [
            RepositoryStand(repository='framework', commit='a1b2c3d4e5f6', dirty=False,
                            subject='Fix the fee rounding',
                            recent_commits=['a1b2c3d4e5f6', '0f1e2d3c4b5a']),
            RepositoryStand(repository='/srv/home/my_strategy', commit='2f054f5', dirty=True,
                            diff_hash='9f2c' * 16)]

    def test_a_framework_named_by_its_root_would_move_with_every_deploy(self, tmp_path):
        """A deploy starts the next session from a new code tree: the same repository."""
        identity = CodeIdentity(framework=RepositoryState(root='/srv/releases/v2', commit='a1b2c3d'))

        stand = _session_from_header(tmp_path, identity).code_stand

        assert [entry.repository for entry in stand.repositories] == ['framework']

    def test_a_session_that_did_not_read_the_history_keeps_the_recorded_short_hash(self, tmp_path):
        identity = CodeIdentity(framework=RepositoryState(root='/app', commit='a1b2c3d'))

        [framework] = _session_from_header(tmp_path, identity).code_stand.repositories

        assert framework.commit == 'a1b2c3d'
        assert framework.subject is None and framework.recent_commits is None

    @pytest.mark.parametrize('state', [
        RepositoryState(root='/srv/home/my_strategy', commit=None),
        RepositoryState(root='/srv/home/my_strategy', in_repository=False),
        RepositoryState(root='/srv/home/my_strategy', in_repository=None),
    ], ids=['unreadable', 'no_repository', 'git_refused'])
    def test_an_unknown_commit_stays_unknown_and_is_never_clean(self, tmp_path, state):
        identity = CodeIdentity(framework=RepositoryState(root='/app', commit='a1b2c3d'),
                                repositories=[state])

        [_, entry] = _session_from_header(tmp_path, identity).code_stand.repositories

        assert entry.commit is None and entry.dirty is True

    def test_a_header_without_an_identity_is_an_unknown_stand_never_a_missing_one(self, tmp_path):
        """An empty column reads as a row that predates it, and so as no change: fail-open."""
        stand = _session_from_header(tmp_path, None).code_stand

        assert stand.repositories == [RepositoryStand(repository='framework', dirty=True)]

    def test_no_run_directory_is_an_unknown_stand_too(self):
        with patch(_GET_GIT_INFO, return_value=_GIT_AT_THE_END):
            stand = _session_provenance(_config()).code_stand

        assert stand.repositories == [RepositoryStand(repository='framework', dirty=True)]

    def test_an_identity_with_no_framework_entry_names_it_as_unknown(self, tmp_path):
        stand = _session_from_header(tmp_path, CodeIdentity(framework=None)).code_stand

        assert stand.repositories == [RepositoryStand(repository='framework', dirty=True)]

    def test_a_backtest_leaves_it_none(self, tmp_path):
        """A backtest runs at one commit and has no sessions to compare, so it pays nothing."""
        scenario = SingleScenario(
            name='s0', scenario_index=0, symbol='BTCUSD', data_broker_type='kraken_spot',
            start_date='2026-01-01',
            strategy_config={'decision_logic_type': 'CORE/aggressive_trend',
                             'worker_instances': {}})
        scenario_set = SimpleNamespace(
            logger=SimpleNamespace(get_log_dir=lambda: tmp_path),
            printed_summary_logger=get_global_logger(), run_timestamp=_TS,
            scenario_set_name='my_set')

        provenance = build_run_provenance(
            SimpleNamespace(single_scenario_list=[scenario], broker_scenario_map={}),
            scenario_set, _RUN_ID)

        assert provenance.code_stand is None
