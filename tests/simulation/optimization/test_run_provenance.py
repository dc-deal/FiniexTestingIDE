"""
Run-provenance (session) tests (#403 · 5.a).

`build_run_provenance_from_session` is the live counterpart to the sim `build_run_provenance`:
it lets a live session append to the same Run Results Ledger. The key property is sim/live
parity — the profile's strategy_config has the same shape as a sim scenario's, so the
param_hash is computed identically and the live row is directly comparable to the backtest.
Built against the REAL AutoTraderConfig / WarningsErrorsReport, never stand-ins.
"""

from datetime import datetime, timezone

from python.framework.reporting.builders.warnings_errors_report_builder import (
    build_warnings_errors_report_from_session,
)
from python.framework.reporting.store.run_provenance_builder import (
    _consumption_record,
    build_run_provenance_from_session,
)
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
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
        name='my_profile', symbol='BTCUSD', broker_type='kraken_spot',
        strategy_config={'decision_logic_type': 'CORE/aggressive_trend',
                         'worker_instances': {}})


class TestSessionProvenance:
    """The live session maps onto the same RunProvenance the ledger ranks over."""

    def test_maps_config_to_provenance(self):
        p = build_run_provenance_from_session(_config(), _RUN_ID, _TS, None)
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
        p = build_run_provenance_from_session(cfg, _RUN_ID, _TS, None)
        assert p.param_hash == generate_config_fingerprint(cfg.strategy_config)

    def test_status_ok_without_report(self):
        p = build_run_provenance_from_session(_config(), _RUN_ID, _TS, None)
        assert p.status == 'ok'
        assert p.error is None

    def test_status_error_on_emergency(self):
        # An emergency session (every unit failed) → a status='error' ledger row, never absent.
        result = AutoTraderResult(emergency_reason='boom', shutdown_mode='emergency')
        report = build_warnings_errors_report_from_session(_RUN_ID, result, 'my_profile', 'BTCUSD')
        p = build_run_provenance_from_session(_config(), _RUN_ID, _TS, report)
        assert p.status == 'error'
        assert 'boom' in p.error


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

        A live session consumes a socket and has no archive input, so its three joined strings
        are empty by construction. Without `input_plane` that is the same bytes as a sim row
        whose recording failed — the exact failure mode this project spent the day removing
        from its producers.
        """
        p = build_run_provenance_from_session(_config(), _RUN_ID, _TS, None)

        assert p.input_plane == 'stream'
        assert p.data_format_versions == ''
        assert p.origin_classes == ''
        assert p.origin_evidence_grades == ''
        assert p.input_files == 0
        # The ONE field that is filled on the live side, and deliberately so: a live session
        # renders its bars at runtime, so there is no file to stamp and nothing can be out of
        # date with the declaration. Blank here would defeat the field — the parity proof has
        # to compare the live basis against the backtest's, and `input_plane` is what says
        # this one is DECLARED rather than measured (§31c).
        assert p.price_bases == 'order_driven'

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

        record = _consumption_record(scenarios)

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

        record = _consumption_record(scenarios)

        assert record['price_bases'] == 'order_driven,unknown'
        # The file COUNT is unchanged: the basis comes from the bar archive and the count from
        # ticks plus signals, so one is not a subset of the other.
        assert record['input_files'] == 1

    def test_a_scenario_that_mounted_no_bars_records_no_basis(self):
        """Empty is the honest answer; the declaration would be a guess in its shape."""
        record = _consumption_record([_scenario_reading(['1.7.0'], ['production'], ['stamped'])])

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

        record = _consumption_record(scenarios)

        assert record['input_files'] == 4
        assert record['unstamped_input_files'] == 3

    def test_a_run_that_read_nothing_says_so_without_inventing_a_value(self):
        record = _consumption_record([])

        assert record['input_plane'] == 'archive'
        assert record['input_files'] == 0
        assert record['origin_classes'] == ''
