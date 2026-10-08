"""
FiniexTestingIDE - The Field Study's Capture through the AutoTrader Pipeline (#566)

Runs the mock field study profile end to end and reads what it leaves behind: a capture whose
order lines are the live core's own record — each under the phase that submitted its order, a
close as a line of its own, the rejection battery's refusals as lines, the venue's start and end
reads as its snapshots — and a certificate analysis over it that measures slippage and reads the
fees from the run's own report.

The phases' OUTCOMES are not asserted. A mock venue fills on the first status read, and those
reads are paced on the machine's clock while the ticks replay at full speed, so whether a fill
lands inside a phase's fifteen replayed seconds depends on processing speed (#494) — measured
2026-10-07, the same profile ends with different phases timed out from one run to the next, on
this commit and on the one before it. What the capture guarantees does not depend on it, and that
is what is held here.
"""

import json
from pathlib import Path

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.reporting.certificates.field_study_certificate import FieldStudyCertificate
from python.framework.reporting.io.artifact_specs import ORDER_EVENTS_STREAM
from python.framework.types.log_layout_types import IO_SUBDIR
from python.framework.types.log_level import LogLevel
from tests.shared.fixture_helpers import logged_messages, remove_run_dir

MOCK_PROFILE = 'configs/autotrader_profiles/mock/field_study_mock.json'


@pytest.fixture(scope='module')
def session():
    """Run the mock field study once; yield its result and run directory."""
    config = load_autotrader_config(MOCK_PROFILE)
    trader = AutotraderMain(config)
    result = trader.run()
    run_dir = Path(trader._run_dir)
    yield result, run_dir
    remove_run_dir(run_dir)


@pytest.fixture(scope='module')
def capture(session):
    """The capture's path, header and lines."""
    _, run_dir = session
    path = run_dir / 'field_study.jsonl'
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()
            if line.strip()]
    return path, rows[0], rows[1:]


def _of(lines, event_type, phase=None):
    return [line for line in lines if line['event_type'] == event_type
            and (phase is None or line['phase'] == phase)]


def _fills(lines):
    return [line for line in lines if line['event_type'] in ('order_filled', 'partial_close')]


class TestTheCapture:
    """What the session leaves behind, line by line."""

    def test_it_is_the_projected_schema(self, capture):
        _, header, _ = capture
        assert header['schema_version'] == '2.0'
        assert 'release_target' not in header

    def test_a_fill_names_the_phase_that_submitted_its_order(self, capture):
        """The study's first order is sent by its first phase — whenever its fill arrives."""
        _, header, lines = capture
        first_open = [line for line in _fills(lines)
                      if line['order_id'] == 'pos_ethusd_1' and line['extra']['action'] == 'open']
        assert first_open
        assert all(line['phase'] == header['phases'][0] for line in first_open)

    def test_the_session_ended_cleanly(self, session):
        """An error inside the projection would surface here — the run itself survives it."""
        result, _ = session
        assert result.shutdown_mode == 'normal'
        assert logged_messages(result, LogLevel.ERROR) == []

    def test_every_execution_of_the_stream_is_one_fill_line(self, session, capture):
        """The capture is the session's record, copied: joined by `stream_seq`, nothing lost."""
        _, run_dir = session
        _, _, lines = capture
        stream = [json.loads(line) for line in
                  (run_dir / IO_SUBDIR / ORDER_EVENTS_STREAM).read_text(encoding='utf-8')
                  .splitlines()[1:] if line.strip()]
        executions = {row['seq']: row for row in stream
                      if row.get('event_type') in ('filled', 'partially_filled')}
        fills = {line['extra']['stream_seq']: line for line in _fills(lines)}

        assert executions
        assert set(fills) == set(executions)
        for seq, row in executions.items():
            assert fills[seq]['lots'] == pytest.approx(row['lots'])
            assert fills[seq]['extra']['fee'] == pytest.approx(row['fee'])
            assert fills[seq]['extra']['action'] == row['action']

    def test_the_rejection_battery_writes_its_refusals(self, capture):
        _, _, lines = capture
        refused = _of(lines, 'order_rejected', 'reject_below_min')
        assert refused and all(line['status'] == 'denied' for line in refused)

    def test_the_venue_reads_bracket_the_session(self, capture):
        _, _, lines = capture
        snapshots = _of(lines, 'broker_snapshot')
        assert [line['phase'] for line in snapshots] == ['preflight', 'session_end']
        assert all('order_count' in line['extra'] for line in snapshots)


class TestTheCertificateOverIt:
    """The analysis reads the capture and the run's own report beside it."""

    def test_it_reads_the_fees_and_measures_the_slippage(self, capture):
        path, _, _ = capture
        analysis = FieldStudyCertificate.analyze(str(path))

        assert analysis['fees_charged']['source'] == 'run_record'
        assert analysis['fees_charged']['value'] > 0
        assert analysis['slippage']['by_order_type']['market']['measured'] > 0
        assert analysis['account_delta']['status'] == 'ok'

    def test_the_report_s_fees_are_the_capture_s(self, session, capture):
        """Every fee the run charged was charged on a fill the capture holds."""
        path, _, lines = capture
        analysis = FieldStudyCertificate.analyze(str(path))

        assert analysis['fees_charged']['value'] == pytest.approx(
            sum(line['extra']['fee'] for line in _fills(lines)))

    def test_a_mock_session_says_it_did_not_reconcile(self, capture):
        """A mock profile switches reconciliation off; the certificate says so, not zero."""
        path, _, _ = capture
        analysis = FieldStudyCertificate.analyze(str(path))

        assert analysis['reconciliation'] == {'status': 'disabled'}
