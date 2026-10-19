"""
The question a wrapper script asks before it starts a run (#603): is enough memory free? Exit 0 when
it is, 1 when not.
"""

from python.cli.activity_cli import ActivityCli
from python.framework.types.machine_resources_types import LimitSource, MemoryReading
from python.framework.utils.machine_resources_reader import MachineResourcesReader
from python.framework.validators.free_memory_validator import judge_free_memory

_GIB = 1024 ** 3


def _memory(limit_gb, used_gb):
    return MemoryReading(limit_bytes=int(limit_gb * _GIB), used_bytes=int(used_gb * _GIB),
                         used_pct=round(used_gb / limit_gb * 100, 1),
                         limit_source=LimitSource.MACHINE)


class TestTheVerdict:

    def test_free_is_the_limit_minus_what_is_used(self):
        verdict = judge_free_memory(_memory(30, 18.5), 8)
        assert verdict.free_bytes == int(30 * _GIB) - int(18.5 * _GIB)
        assert verdict.required_bytes == 8 * _GIB
        assert verdict.enough

    def test_too_little_free_is_not_enough(self):
        assert not judge_free_memory(_memory(30, 25), 8).enough

    def test_exactly_enough_is_enough(self):
        assert judge_free_memory(_memory(30, 22), 8).enough


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
