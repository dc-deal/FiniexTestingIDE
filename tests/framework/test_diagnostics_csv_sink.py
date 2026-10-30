"""
FiniexTestingIDE - Diagnostics CSV Sink Tests (#376)

Tests the generic strategy-owned diagnostics CSV channel: the DiagnosticsCsvSink
(file logistics), the AbstractDecisionLogic API (diagnostics_csv / get_diagnostics_sinks),
the flush_decision_diagnostics helper used by both pipelines, and the rows a session streams as
they are appended (#476).
"""

import csv
import json
from datetime import datetime, timezone
from enum import Enum
from unittest.mock import MagicMock

import numpy as np
import pytest

from python.framework.autotrader.run_record.session_records import stream_diagnostics_rows

from python.framework.constants.run_layout_constants import (
    RUN_TYPE_AUTOTRADER,
    RUN_TYPE_SIMULATION,
)
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.exceptions.data_home_errors import DirectoryCreationDeniedError
from python.framework.reporting.diagnostics_csv_sink import (
    DiagnosticsCsvSink,
    flush_decision_diagnostics,
)
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.store_member_types import RunEntryMemberId


class _DummyDecision(AbstractDecisionLogic):
    """Minimal concrete decision logic for exercising the sink API."""

    @classmethod
    def get_required_order_features(cls, decision_logic_config) -> list:
        return []

    @classmethod
    def get_required_order_types(cls, decision_logic_config):
        return []

    def get_required_workers(self):
        return {}

    def compute_tick(self, tick, worker_results):
        return None

    def _execute_decision_impl(self, decision, tick):
        return None


def _make_decision() -> _DummyDecision:
    return _DummyDecision(name='dummy', logger=MagicMock())


def _writer(run_dir) -> RunEntryWriter:
    """A session's writer over a run directory in the test's temporary folder."""
    return RunEntryWriter(run_dir, RUN_TYPE_AUTOTRADER)


def _backtest_writer(run_dir) -> RunEntryWriter:
    """A backtest's writer — the one a scenario's tables, named with its suffix, belong to."""
    return RunEntryWriter(run_dir, RUN_TYPE_SIMULATION)


def _read_csv(path):
    with open(path, newline='') as f:
        return list(csv.reader(f))


class TestDiagnosticsCsvSink:
    """File logistics of a single sink."""

    def test_flush_writes_header_and_rows(self, tmp_path):
        sink = DiagnosticsCsvSink('funnel', ['id', 'outcome', 'conf'])
        sink.append_row({'id': 'p1', 'outcome': 'TRADED', 'conf': 0.6})
        sink.append_row({'id': 'p2', 'outcome': 'RESET', 'conf': 0.1})

        out = sink.flush(_writer(tmp_path))

        assert out == tmp_path / 'diagnostics' / 'funnel.csv'
        rows = _read_csv(out)
        assert rows[0] == ['id', 'outcome', 'conf']
        assert rows[1] == ['p1', 'TRADED', '0.6']
        assert rows[2] == ['p2', 'RESET', '0.1']

    def test_a_directory_the_ide_may_not_create_stops_the_run(self, tmp_path):
        """A refusal is not a table that failed to write: it ends the run with its named error."""
        (tmp_path / 'diagnostics').write_text('a file where the folder should be')
        sink = DiagnosticsCsvSink('funnel', ['id'])
        sink.append_row({'id': 'p1'})

        with pytest.raises(DirectoryCreationDeniedError):
            sink.flush(_backtest_writer(tmp_path), scenario_suffix='window_01')

    def test_missing_key_renders_empty_cell(self, tmp_path):
        sink = DiagnosticsCsvSink('funnel', ['id', 'outcome', 'conf'])
        sink.append_row({'id': 'p1', 'outcome': 'REJECTED'})  # no conf

        rows = _read_csv(sink.flush(_writer(tmp_path)))

        assert rows[1] == ['p1', 'REJECTED', '']

    def test_extra_key_is_ignored(self, tmp_path):
        sink = DiagnosticsCsvSink('funnel', ['id'])
        sink.append_row({'id': 'p1', 'unexpected': 'x'})

        rows = _read_csv(sink.flush(_writer(tmp_path)))

        assert rows[0] == ['id']
        assert rows[1] == ['p1']

    def test_scenario_suffix_in_filename(self, tmp_path):
        sink = DiagnosticsCsvSink('funnel', ['id'])
        sink.append_row({'id': 'p1'})

        out = sink.flush(_backtest_writer(tmp_path), scenario_suffix='EURUSD_w1')

        assert out == tmp_path / 'diagnostics' / 'funnel_EURUSD_w1.csv'
        assert out.exists()

    def test_noop_without_a_writer(self):
        sink = DiagnosticsCsvSink('funnel', ['id'])
        sink.append_row({'id': 'p1'})

        assert sink.flush(None) is None

    def test_noop_when_no_rows(self, tmp_path):
        sink = DiagnosticsCsvSink('funnel', ['id'])

        assert sink.flush(_writer(tmp_path)) is None
        assert not (tmp_path / 'diagnostics' / 'funnel.csv').exists()

    def test_get_name(self):
        assert DiagnosticsCsvSink('funnel', ['id']).get_name() == 'funnel'


class TestDecisionLogicSinkApi:
    """The AbstractDecisionLogic-facing API."""

    def test_diagnostics_csv_get_or_create_same_instance(self):
        dl = _make_decision()
        first = dl.diagnostics_csv('funnel', ['id', 'outcome'])
        second = dl.diagnostics_csv('funnel', ['ignored'])  # same name → same sink

        assert first is second

    def test_distinct_names_distinct_sinks(self):
        dl = _make_decision()
        a = dl.diagnostics_csv('funnel', ['id'])
        b = dl.diagnostics_csv('quality', ['id'])

        assert a is not b
        assert {s.get_name() for s in dl.get_diagnostics_sinks()} == {'funnel', 'quality'}

    def test_no_sinks_by_default(self):
        assert _make_decision().get_diagnostics_sinks() == []


class TestFlushDecisionDiagnostics:
    """The shared run-end flush helper used by both pipelines."""

    def test_flushes_all_sinks_into_diagnostics_subdir(self, tmp_path):
        dl = _make_decision()
        dl.diagnostics_csv('funnel', ['id']).append_row({'id': 'p1'})
        dl.diagnostics_csv('quality', ['id']).append_row({'id': 'q1'})

        flush_decision_diagnostics(dl, _writer(tmp_path))

        # Strategy diagnostics live in a dedicated diagnostics/ subfolder
        assert (tmp_path / 'diagnostics' / 'funnel.csv').exists()
        assert (tmp_path / 'diagnostics' / 'quality.csv').exists()

    def test_applies_scenario_suffix(self, tmp_path):
        dl = _make_decision()
        dl.diagnostics_csv('funnel', ['id']).append_row({'id': 'p1'})

        flush_decision_diagnostics(dl, _backtest_writer(tmp_path), scenario_suffix='BTCUSD_b04')

        assert (tmp_path / 'diagnostics' / 'funnel_BTCUSD_b04.csv').exists()

    def test_no_sinks_is_safe(self, tmp_path):
        flush_decision_diagnostics(_make_decision(), _writer(tmp_path))  # no raise

    def test_no_writer_is_safe(self):
        dl = _make_decision()
        dl.diagnostics_csv('funnel', ['id']).append_row({'id': 'p1'})

        flush_decision_diagnostics(dl, None)  # no raise, nothing written


class _Side(Enum):
    LONG = 'long'


def _stream_lines(run_dir, sink: str) -> list:
    """The records a sink's stream holds, header first."""
    path = RunEntryWriter(run_dir, RUN_TYPE_AUTOTRADER).path_of(
        RunEntryMemberId.DIAGNOSTICS_ROWS, sink=sink)
    return [json.loads(line) for line in path.read_text().splitlines()]


class TestASessionStreamsItsRows:
    """
    A session streams every row as it is appended (#476): the table is written at the end, and a
    session killed before it would keep no row. A row the table takes must never end the session
    through its stream.
    """

    def test_a_row_is_a_line_the_moment_it_is_appended(self, tmp_path):
        dl = _make_decision()
        stream_diagnostics_rows(RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER), 'run_x', dl,
                                MagicMock())
        dl.diagnostics_csv('setup', ['bars', 'note']).append_row({'note': 'n', 'bars': 3, 'x': 1})

        header, row = _stream_lines(tmp_path, 'setup')
        assert header['columns'] == ['bars', 'note'] and header['sink'] == 'setup'
        assert row == {'bars': 3, 'note': 'n'}

    def test_rows_appended_before_the_stream_opened_are_written_when_it_opens(self, tmp_path):
        dl = _make_decision()
        dl.diagnostics_csv('setup', ['bars']).append_row({'bars': 1})
        stream_diagnostics_rows(RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER), 'run_x', dl,
                                MagicMock())
        dl.diagnostics_csv('setup', ['bars']).append_row({'bars': 2})

        assert [line.get('bars') for line in _stream_lines(tmp_path, 'setup')[1:]] == [1, 2]

    def test_a_cell_only_the_table_could_write_is_written_as_the_table_writes_it(self, tmp_path):
        stamp = datetime(2026, 10, 25, 12, tzinfo=timezone.utc)
        dl = _make_decision()
        stream_diagnostics_rows(RunEntryWriter(tmp_path, RUN_TYPE_AUTOTRADER), 'run_x', dl,
                                MagicMock())
        dl.diagnostics_csv('setup', ['at', 'bars', 'ok', 'side', 'arr']).append_row(
            {'at': stamp, 'bars': np.int64(3), 'ok': np.bool_(True), 'side': _Side.LONG,
             'arr': np.array([1, 2])})

        row = _stream_lines(tmp_path, 'setup')[1]
        assert row == {'at': str(stamp), 'bars': 3, 'ok': True, 'side': str(_Side.LONG),
                       'arr': str(np.array([1, 2]))}

