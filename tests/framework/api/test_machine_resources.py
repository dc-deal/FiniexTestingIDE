"""
Machine Resources — what the machine the server runs on has and uses (#595).

The viewer polls this before anything else, so its numbers have to be the ones that bind: a
container's own limit where one is set below the machine's, the machine's where none is, and the
source named either way (#416). The processors' busy share is measured over the window since the
previous reading, and that window is served beside it. The pressure level grades memory alone,
against thresholds the answer carries.
"""

from collections import namedtuple
from pathlib import Path
from typing import List

import pytest
from fastapi.testclient import TestClient

from python.api.api_app import create_app
from python.framework.types.config_types.machine_resources_config_types import (
    MachineResourcesConfig,
)
from python.framework.types.machine_resources_types import (
    LimitSource,
    MemoryReading,
    PressureLevel,
)
from python.framework.utils.machine_resources_reader import MachineResourcesReader
from python.framework.validators.machine_pressure_validator import judge_memory_pressure

GIB = 1024 ** 3
CpuTimes = namedtuple('CpuTimes', 'user nice system idle iowait irq softirq steal guest guest_nice')
VirtualMemory = namedtuple('VirtualMemory', 'total available')


class _Samples:
    """Cumulative processor times and a monotonic clock, played back one sample per call."""

    def __init__(self, busy_idle: List[tuple], seconds: List[float]):
        self._times = [CpuTimes(b, 0.0, 0.0, i, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0) for b, i in busy_idle]
        self._seconds = list(seconds)

    def cpu_times(self) -> CpuTimes:
        return self._times.pop(0)

    def clock(self) -> float:
        return self._seconds.pop(0)


def _reader(cgroup_root: Path, samples: _Samples = None, total_gib: float = 32.0,
            available_gib: float = 16.0, usable_cpus: int = 16) -> MachineResourcesReader:
    samples = samples or _Samples([(0.0, 0.0), (0.0, 0.0)], [0.0, 0.0])
    return MachineResourcesReader(
        cgroup_root=cgroup_root,
        cpu_times_fn=samples.cpu_times,
        memory_fn=lambda: VirtualMemory(int(total_gib * GIB), int(available_gib * GIB)),
        usable_cpus_fn=lambda: usable_cpus,
        clock_fn=samples.clock,
    )


def _write(root: Path, **files: str) -> Path:
    for name, content in files.items():
        path = root / name.replace('__', '/')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


class TestTheLimitThatBinds:

    def test_a_cgroup_v2_limit_below_the_machine_binds(self, tmp_path):
        root = _write(tmp_path, **{'memory.max': str(8 * GIB), 'memory.current': str(2 * GIB)})
        memory = _reader(root).read().memory
        assert (memory.limit_bytes, memory.used_bytes) == (8 * GIB, 2 * GIB)
        assert memory.used_pct == 25.0
        assert memory.limit_source is LimitSource.CGROUP

    def test_no_cgroup_limit_reads_the_machine_and_says_so(self, tmp_path):
        """This container's own state: `memory.max` reads `max`, and the machine is the limit."""
        root = _write(tmp_path, **{'memory.max': 'max', 'memory.current': str(GIB)})
        memory = _reader(root, total_gib=32.0, available_gib=24.0).read().memory
        assert memory.limit_bytes == 32 * GIB
        assert memory.used_bytes == 8 * GIB
        assert memory.limit_source is LimitSource.MACHINE

    def test_a_cgroup_limit_above_the_machine_does_not_bind(self, tmp_path):
        root = _write(tmp_path, **{'memory.max': str(64 * GIB), 'memory.current': str(GIB)})
        assert _reader(root, total_gib=32.0).read().memory.limit_source is LimitSource.MACHINE

    def test_a_cgroup_v1_limit_binds_too(self, tmp_path):
        root = _write(tmp_path, **{'memory__memory.limit_in_bytes': str(4 * GIB),
                                   'memory__memory.usage_in_bytes': str(3 * GIB)})
        memory = _reader(root).read().memory
        assert (memory.limit_bytes, memory.used_pct, memory.limit_source) == (
            4 * GIB, 75.0, LimitSource.CGROUP)

    def test_a_cgroup_v1_unlimited_value_is_no_limit(self, tmp_path):
        root = _write(tmp_path, **{'memory__memory.limit_in_bytes': str(9223372036854771712),
                                   'memory__memory.usage_in_bytes': str(GIB)})
        assert _reader(root).read().memory.limit_source is LimitSource.MACHINE

    def test_a_processor_quota_below_the_machine_binds(self, tmp_path):
        root = _write(tmp_path, **{'cpu.max': '150000 100000'})
        cpu = _reader(root, usable_cpus=16).read().cpu
        assert (cpu.count, cpu.count_source) == (1.5, LimitSource.CGROUP)

    def test_no_processor_quota_counts_the_usable_processors(self, tmp_path):
        root = _write(tmp_path, **{'cpu.max': 'max 100000'})
        cpu = _reader(root, usable_cpus=16).read().cpu
        assert (cpu.count, cpu.count_source) == (16.0, LimitSource.MACHINE)


class TestTheBusyShareHasAWindow:

    def test_it_is_measured_since_the_previous_reading(self, tmp_path):
        # constructed: 10 s busy of 100 s at t=0 · read: 40 s busy of 200 s at t=5
        samples = _Samples([(10.0, 90.0), (40.0, 160.0)], [0.0, 5.0])
        cpu = _reader(tmp_path, samples).read().cpu
        assert cpu.used_pct == 30.0          # 30 s busy of the 100 s spent in between
        assert cpu.window_s == 5.0

    def test_no_time_spent_gives_no_share_rather_than_zero(self, tmp_path):
        samples = _Samples([(10.0, 90.0), (10.0, 90.0)], [0.0, 0.0])
        cpu = _reader(tmp_path, samples).read().cpu
        assert (cpu.used_pct, cpu.window_s) == (None, None)


class TestThePressureVerdict:

    @staticmethod
    def _memory(used_pct: float, source: LimitSource = LimitSource.MACHINE) -> MemoryReading:
        return MemoryReading(limit_bytes=30 * GIB, used_bytes=int(30 * GIB * used_pct / 100),
                             used_pct=used_pct, limit_source=source)

    def test_below_the_warning_is_ok_and_says_nothing(self):
        assert judge_memory_pressure(self._memory(79.9), MachineResourcesConfig()) == (
            PressureLevel.OK, [])

    def test_from_the_warning_on_it_warns_and_names_the_critical_mark(self):
        level, reasons = judge_memory_pressure(self._memory(80.0), MachineResourcesConfig())
        assert level is PressureLevel.WARN
        assert reasons == ['Memory at 80 % of the 30.0 GiB the machine has — 90 % is critical.']

    def test_from_the_critical_mark_on_it_says_what_is_at_stake(self):
        level, reasons = judge_memory_pressure(
            self._memory(91.0, LimitSource.CGROUP), MachineResourcesConfig())
        assert level is PressureLevel.CRITICAL
        assert 'this container may use' in reasons[0]
        assert 'the kernel ends a process' in reasons[0]

    @pytest.mark.parametrize('warn, critical', [(90.0, 80.0), (0.0, 90.0), (80.0, 101.0)])
    def test_thresholds_that_cannot_grade_are_refused(self, warn, critical):
        with pytest.raises(ValueError):
            MachineResourcesConfig(memory_warn_pct=warn, memory_critical_pct=critical)


def test_the_route_serves_the_reading_with_its_verdict_and_thresholds():
    body = TestClient(create_app()).get('/api/v1/machine-resources').json()

    assert set(body) == {'measured_at', 'cpu', 'memory', 'pressure_level', 'pressure_reasons'}
    assert body['cpu']['count'] > 0
    assert body['memory']['limit_source'] in ('cgroup', 'machine')
    assert 0 < body['memory']['used_pct'] < 100
    assert (body['memory']['warn_at_pct'], body['memory']['critical_at_pct']) == (80.0, 90.0)
    assert body['pressure_level'] in ('ok', 'warn', 'critical')
