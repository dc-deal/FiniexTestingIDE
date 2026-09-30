"""
FiniexTestingIDE - Scenario Execution Time Unit

`execution_time_ms` is read by the console, the report and the viewer as MILLISECONDS. The check
is a floor no Python tick loop can beat — one microsecond per processed tick, a million ticks a
second. A duration recorded in seconds under this name sits a thousand times below it, which is
how a value of 1.98 for 13,584 ticks read as 6.9 million ticks a second.
"""

from python.framework.types.probe_metadata_types import ProbeMetadata
from python.framework.types.process_data_types import ProcessResult

# One microsecond per tick: far faster than the loop runs, far slower than a value in seconds.
_FLOOR_MS_PER_TICK = 0.001


def test_execution_time_is_in_milliseconds(
        process_result: ProcessResult, probe_metadata: ProbeMetadata):
    """The scenario's execution time is plausible as milliseconds for its tick count."""
    ticks = probe_metadata.tick_count
    assert ticks > 0, 'the baseline processed no ticks — this test would check nothing'
    per_tick_ms = process_result.execution_time_ms / ticks
    assert per_tick_ms >= _FLOOR_MS_PER_TICK, (
        f'{process_result.execution_time_ms} for {ticks} ticks is {per_tick_ms:.6f} ms per tick — '
        f'faster than any Python tick loop, so the value is not in milliseconds')
