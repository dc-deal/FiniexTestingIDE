"""
The question a wrapper script asks before it starts a run (#603): is enough memory free? Exit 0 when
it is, 1 when not, 2 when there is no answer — and a list that cannot be read never changes it.
"""

import sys
from datetime import datetime, timezone

import pytest

from python.cli import activity_cli as activity_cli_module
from python.cli.activity_cli import ActivityCli
from python.framework.activities.activity_registry_reader import ActivityRegistryReader
from python.framework.reporting.console.activity_list_summary import render_activity_list
from python.framework.types.activity_registry_types import (
    ACTIVITY_ENTRY_SCHEMA,
    ActivityEntry,
    ActivityListing,
    ActivitySnapshot,
)
from python.framework.types.machine_resources_types import LimitSource, MemoryReading
from python.framework.utils.machine_resources_reader import MachineResourcesReader
from python.framework.validators.free_memory_validator import judge_free_memory

_GIB = 1024 ** 3


def _memory(limit_gb, used_gb):
    return MemoryReading(limit_bytes=int(limit_gb * _GIB), used_bytes=int(used_gb * _GIB),
                         used_pct=round(used_gb / limit_gb * 100, 1),
                         limit_source=LimitSource.MACHINE)


def _entry(activity_id, label, started_at):
    return ActivityEntry.from_dict({
        'schema_version': ACTIVITY_ENTRY_SCHEMA, 'activity_id': activity_id,
        'activity_kind': 'backtest', 'activity_state': 'running', 'activity_label': label,
        'pid': 1, 'process_started_at': 0.0, 'command_line': ['x'], 'started_at': started_at,
        'started_monotonic': 0.0, 'working_tree': '/elsewhere/tree', 'console_session': '',
        'git_commit': None, 'origin': None, 'run_id': None, 'sweep_id': None,
        'parent_activity_id': None,
    })


class TestTheVerdict:

    def test_free_is_the_limit_minus_what_is_used(self):
        verdict = judge_free_memory(_memory(30, 18.5), 8 * _GIB)
        assert verdict.free_bytes == int(30 * _GIB) - int(18.5 * _GIB)
        assert verdict.required_bytes == 8 * _GIB
        assert verdict.enough

    def test_too_little_free_is_not_enough(self):
        assert not judge_free_memory(_memory(30, 25), 8 * _GIB).enough

    def test_exactly_enough_is_enough(self):
        assert judge_free_memory(_memory(30, 22), 8 * _GIB).enough

    def test_more_in_use_than_the_limit_leaves_nothing_free(self):
        assert _memory(30, 31).free_bytes() == 0


class TestTheExitCode:

    def test_check_exits_0_when_enough_is_free_and_1_when_not(self, registry_dir, monkeypatch,
                                                                capsys):
        monkeypatch.setattr(MachineResourcesReader, 'read_memory',
                            lambda self: _memory(30, 20))
        cli = ActivityCli()
        assert cli.cmd_check(5) == 0
        assert '✅' in capsys.readouterr().out
        assert cli.cmd_check(15) == 1
        output = capsys.readouterr().out
        assert '❌' in output and 'What holds memory' in output

    def test_a_list_that_cannot_be_read_never_changes_the_answer(self, registry_dir, monkeypatch,
                                                                  capsys):
        reads = []

        def unreadable(self):
            reads.append(1)
            raise PermissionError(13, 'Permission denied')

        monkeypatch.setattr(MachineResourcesReader, 'read_memory', lambda self: _memory(30, 20))
        monkeypatch.setattr(ActivityRegistryReader, 'read', unreadable)
        cli = ActivityCli()
        assert cli.cmd_check(5) == 0
        assert reads == [], 'enough memory is answered without reading the list'
        assert cli.cmd_check(15) == 1
        assert 'could not be read: Permission denied' in capsys.readouterr().out

    def test_no_answer_exits_2(self, registry_dir, monkeypatch):
        def broken(self):
            raise RuntimeError('the memory reading broke')

        monkeypatch.setattr(MachineResourcesReader, 'read_memory', broken)
        monkeypatch.setattr(sys, 'argv', ['activity_cli.py', 'check', '--min-free-gb', '1'])
        assert activity_cli_module.main() == 2

    @pytest.mark.parametrize('size', ['nan', 'inf', '0', '-1', 'eight', '1e300'])
    def test_a_size_that_is_no_size_is_refused(self, size, monkeypatch):
        monkeypatch.setattr(sys, 'argv', ['activity_cli.py', 'check', '--min-free-gb', size])
        with pytest.raises(SystemExit) as refused:
            activity_cli_module.main()
        assert refused.value.code == 2


class TestTheConsoleList:

    def test_an_earlier_day_shows_its_date_and_a_long_label_keeps_its_end(self, capsys):
        measured_at = datetime(2026, 10, 19, 14, 41, tzinfo=timezone.utc)
        snapshot = ActivitySnapshot(
            measured_at=measured_at,
            listing=ActivityListing(entries=[
                _entry('act_today', 'my_sweep', '2026-10-19T14:02:00+00:00'),
                _entry('act_older', 'my_set__sweep_20261017_090000_ab12cd34_c003',
                       '2026-10-17T09:00:00+00:00'),
            ]),
            memory={},
            machine_memory=_memory(30, 20),
        )
        render_activity_list(snapshot, None)
        lines = capsys.readouterr().out.splitlines()
        [today] = [line for line in lines if 'my_sweep ' in line]
        [older] = [line for line in lines if 'my_set__' in line]
        assert ' 14:02 ' in today
        assert ' 10-17 09:00 ' in older
        assert '…' in older and older.split('…')[1].split()[0].endswith('_c003')
