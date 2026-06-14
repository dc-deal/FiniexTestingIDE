"""
FiniexTestingIDE - Diagnostics CSV Sink Tests (#376)

Tests the generic strategy-owned diagnostics CSV channel: the DiagnosticsCsvSink
(file logistics), the AbstractDecisionLogic API (diagnostics_csv / get_diagnostics_sinks),
and the flush_decision_diagnostics helper used by both pipelines.
"""

import csv
from unittest.mock import MagicMock

import pytest

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.reporting.diagnostics_csv_sink import (
    DiagnosticsCsvSink,
    flush_decision_diagnostics,
)


class _DummyDecision(AbstractDecisionLogic):
    """Minimal concrete decision logic for exercising the sink API."""

    @classmethod
    def get_required_order_types(cls, decision_logic_config):
        return []

    def get_required_worker_instances(self):
        return {}

    def compute_tick(self, tick, worker_results):
        return None

    def _execute_decision_impl(self, decision, tick):
        return None


def _make_decision() -> _DummyDecision:
    return _DummyDecision(name='dummy', logger=MagicMock())


def _read_csv(path):
    with open(path, newline='') as f:
        return list(csv.reader(f))


class TestDiagnosticsCsvSink:
    """File logistics of a single sink."""

    def test_flush_writes_header_and_rows(self, tmp_path):
        sink = DiagnosticsCsvSink('funnel', ['id', 'outcome', 'conf'])
        sink.append_row({'id': 'p1', 'outcome': 'TRADED', 'conf': 0.6})
        sink.append_row({'id': 'p2', 'outcome': 'RESET', 'conf': 0.1})

        out = sink.flush(tmp_path)

        assert out == tmp_path / 'funnel.csv'
        rows = _read_csv(out)
        assert rows[0] == ['id', 'outcome', 'conf']
        assert rows[1] == ['p1', 'TRADED', '0.6']
        assert rows[2] == ['p2', 'RESET', '0.1']

    def test_missing_key_renders_empty_cell(self, tmp_path):
        sink = DiagnosticsCsvSink('funnel', ['id', 'outcome', 'conf'])
        sink.append_row({'id': 'p1', 'outcome': 'REJECTED'})  # no conf

        rows = _read_csv(sink.flush(tmp_path))

        assert rows[1] == ['p1', 'REJECTED', '']

    def test_extra_key_is_ignored(self, tmp_path):
        sink = DiagnosticsCsvSink('funnel', ['id'])
        sink.append_row({'id': 'p1', 'unexpected': 'x'})

        rows = _read_csv(sink.flush(tmp_path))

        assert rows[0] == ['id']
        assert rows[1] == ['p1']

    def test_scenario_suffix_in_filename(self, tmp_path):
        sink = DiagnosticsCsvSink('funnel', ['id'])
        sink.append_row({'id': 'p1'})

        out = sink.flush(tmp_path, scenario_suffix='EURUSD_w1')

        assert out == tmp_path / 'funnel_EURUSD_w1.csv'
        assert out.exists()

    def test_noop_when_run_dir_none(self):
        sink = DiagnosticsCsvSink('funnel', ['id'])
        sink.append_row({'id': 'p1'})

        assert sink.flush(None) is None

    def test_noop_when_no_rows(self, tmp_path):
        sink = DiagnosticsCsvSink('funnel', ['id'])

        assert sink.flush(tmp_path) is None
        assert not (tmp_path / 'funnel.csv').exists()

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

        flush_decision_diagnostics(dl, tmp_path)

        # Strategy diagnostics live in a dedicated diagnostics/ subfolder
        assert (tmp_path / 'diagnostics' / 'funnel.csv').exists()
        assert (tmp_path / 'diagnostics' / 'quality.csv').exists()

    def test_applies_scenario_suffix(self, tmp_path):
        dl = _make_decision()
        dl.diagnostics_csv('funnel', ['id']).append_row({'id': 'p1'})

        flush_decision_diagnostics(dl, tmp_path, scenario_suffix='BTCUSD_b04')

        assert (tmp_path / 'diagnostics' / 'funnel_BTCUSD_b04.csv').exists()

    def test_no_sinks_is_safe(self, tmp_path):
        flush_decision_diagnostics(_make_decision(), tmp_path)  # no raise

    def test_run_dir_none_is_safe(self):
        dl = _make_decision()
        dl.diagnostics_csv('funnel', ['id']).append_row({'id': 'p1'})

        flush_decision_diagnostics(dl, None)  # no raise, nothing written
