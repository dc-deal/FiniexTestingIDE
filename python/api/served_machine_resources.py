"""
The machine this server runs on, as the API serves it (#595, #603).

One description of the machine's memory for both routes that serve it — `/machine-resources`
beside the processors, `/activities` beside what runs — with the pressure verdict next to the
thresholds it was judged against, so no consumer grades the thresholds itself.
"""

from typing import List, Tuple

from python.framework.types.api.api_types import (
    MachineCpuResponse,
    MachineMemoryResponse,
    MachineResourcesResponse,
)
from python.framework.types.config_types.machine_resources_config_types import (
    MachineResourcesConfig,
)
from python.framework.types.machine_resources_types import (
    MachineResourcesReading,
    MemoryReading,
    PressureLevel,
)
from python.framework.validators.machine_pressure_validator import judge_memory_pressure


def describe_machine(reading: MachineResourcesReading,
                     thresholds: MachineResourcesConfig) -> MachineResourcesResponse:
    """
    Serve one reading of the machine, with the pressure verdict on its memory.

    Args:
        reading: The processors and the memory, read now
        thresholds: Where `warn` and `critical` begin

    Returns:
        The reading as served, its thresholds beside the memory they grade
    """
    memory, level, reasons = describe_machine_memory(reading.memory, thresholds)
    return MachineResourcesResponse(
        measured_at=reading.measured_at.isoformat(),
        cpu=MachineCpuResponse(
            count=reading.cpu.count,
            count_source=reading.cpu.count_source,
            used_pct=reading.cpu.used_pct,
            window_s=reading.cpu.window_s,
        ),
        memory=memory,
        pressure_level=level,
        pressure_reasons=reasons,
    )


def describe_machine_memory(
    memory: MemoryReading,
    thresholds: MachineResourcesConfig,
) -> Tuple[MachineMemoryResponse, PressureLevel, List[str]]:
    """
    Serve the machine's memory with the verdict on it.

    Args:
        memory: The memory, read now
        thresholds: Where `warn` and `critical` begin

    Returns:
        The memory as served, its pressure level and the reasons for it
    """
    level, reasons = judge_memory_pressure(memory, thresholds)
    served = MachineMemoryResponse(
        limit_bytes=memory.limit_bytes,
        used_bytes=memory.used_bytes,
        used_pct=memory.used_pct,
        limit_source=memory.limit_source,
        warn_at_pct=thresholds.memory_warn_pct,
        critical_at_pct=thresholds.memory_critical_pct,
    )
    return served, level, reasons
