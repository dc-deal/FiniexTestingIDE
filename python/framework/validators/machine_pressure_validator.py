"""
FiniexTestingIDE - Machine Pressure Validator (#595)

How close the machine's memory is to its limit, judged against the configured thresholds. A
verdict, which is why it lives here rather than beside the route that serves it: the route
reports, this decides.

Memory alone is graded. A busy processor is the machine doing its work — every backtest uses all
of them — while memory that runs out is the moment the kernel ends a process, and on a machine
whose container sets no limit, nothing refuses politely before that.
"""

from typing import List, Tuple

from python.framework.types.config_types.machine_resources_config_types import (
    MachineResourcesConfig,
)
from python.framework.types.machine_resources_types import (
    LimitSource,
    MemoryReading,
    PressureLevel,
)

_GIB = 1024 ** 3


def judge_memory_pressure(
    memory: MemoryReading,
    thresholds: MachineResourcesConfig,
) -> Tuple[PressureLevel, List[str]]:
    """
    Grade the memory in use against the warn and critical thresholds.

    Args:
        memory: The reading
        thresholds: Where `warn` and `critical` begin

    Returns:
        The level, and a sentence for a person per reason — none when the level is `ok`
    """
    if memory.used_pct < thresholds.memory_warn_pct:
        return PressureLevel.OK, []
    limit_gib = memory.limit_bytes / _GIB
    whose = ('this container may use' if memory.limit_source is LimitSource.CGROUP
             else 'the machine has')
    if memory.used_pct >= thresholds.memory_critical_pct:
        return PressureLevel.CRITICAL, [
            f'Memory at {memory.used_pct:.0f} % of the {limit_gib:.1f} GiB {whose} — when it runs '
            f'out, the kernel ends a process, so a further scenario may be stopped.']
    return PressureLevel.WARN, [
        f'Memory at {memory.used_pct:.0f} % of the {limit_gib:.1f} GiB {whose} — '
        f'{thresholds.memory_critical_pct:.0f} % is critical.']
