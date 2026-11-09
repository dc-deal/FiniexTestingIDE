"""
FiniexTestingIDE - Run Meta Report Builder

Projects the run-level execution facts the orchestrator measures primarily (timing split +
scenario identity) into the `RunMetaReport` model — once, at DERIVE. Reads them straight from
the source (`BatchExecutionSummary`, where the orchestrator stored its `time.time()` measurements),
so PRESENT no longer reaches back into the raw type for them. The parallelism the batch ran with
and the machine reading are handed in by the coordinator; the throughput is derived once the
scenario details, the profiling and the run summary exist (#557).
"""
from typing import List, Optional

from python.framework.reporting.aggregation.aggregation_fold import fold_group
from python.framework.reporting.aggregation.tables.run_meta_figure_table import RUN_THROUGHPUT_TABLE
from python.framework.types.api.report_types import (
    ProfilingReport,
    RunMetaReport,
    RunSummary,
    ScenarioDetailsReport,
)
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.machine_resources_types import MachineResourcesReading
from python.framework.types.scenario_types.window_set_types import WindowSet


def build_run_meta_report_from_batch(run_id: str, batch: BatchExecutionSummary,
                                     disabled_count: int = 0,
                                     parallel_scenarios: bool = False,
                                     max_parallel_scenarios: int = 0,
                                     generator_profiles: Optional[List[WindowSet]] = None,
                                     machine: Optional[MachineResourcesReading] = None
                                     ) -> RunMetaReport:
    """
    Build the run-level meta report from the batch execution summary.

    Args:
        run_id: The run this report belongs to
        batch: The finished batch execution summary (the orchestrator's measurements)
        disabled_count: Scenarios the set switched off, as the loader counted them — the batch
            cannot say, because the loader drops them before any batch exists
        parallel_scenarios: Whether the batch was configured to run scenarios side by side
        max_parallel_scenarios: At most how many it was configured to run at once
        generator_profiles: A Profile Run's generator profiles; None for a scenario set
        machine: The machine reading taken when the report was derived; None without one

    Returns:
        RunMetaReport with scenario identity + the wall-clock timing split
    """
    scenarios = batch.single_scenario_list
    is_profile_run = bool(scenarios) and getattr(
        scenarios[0], 'is_profile_run', False)

    # #137 performance-tracking layer presence (any scenario carried the data) — Layer A =
    # per-worker stats, Layer B = tick-loop operation profiling. Drives the "tracking OFF" notice.
    worker_on = any(
        r.tick_loop_results and r.tick_loop_results.worker_statistics
        for r in batch.process_result_list)
    profiling_on = any(
        r.tick_loop_results and r.tick_loop_results.profiling_data
        and r.tick_loop_results.profiling_data.profile_times
        for r in batch.process_result_list)

    return RunMetaReport(
        run_id=run_id,
        scenario_count=len(scenarios),
        disabled_count=disabled_count,
        symbols=sorted(set(s.symbol for s in scenarios)),
        is_profile_run=is_profile_run,
        debug_execution=batch.debug_execution,
        execution_time_s=batch.batch_execution_time,
        warmup_time_s=batch.batch_warmup_time,
        tickrun_time_s=batch.batch_tickrun_time,
        pickle_time_s=batch.batch_pickle_time,
        pickle_sample_mb=batch.batch_pickle_sample_mb,
        worker_tracking_on=worker_on,
        profiling_tracking_on=profiling_on,
        parallel_scenarios=parallel_scenarios,
        max_parallel_scenarios=max_parallel_scenarios,
        generator_profile_count=len(generator_profiles or []),
        generator_profile_modes=sorted({profile.mode for profile in generator_profiles or []}),
        machine_cpu_count=machine.cpu.count if machine else None,
        machine_memory_limit_bytes=machine.memory.limit_bytes if machine else None,
        machine_memory_used_bytes=machine.memory.used_bytes if machine else None,
    )


def derive_run_throughput(run_meta: RunMetaReport, scenario_details: ScenarioDetailsReport,
                          profiling: ProfilingReport, run_summary: RunSummary) -> RunMetaReport:
    """
    The run's throughput beside its timing — derived once every section it reads exists.

    Args:
        run_meta: The run meta report, its timing measured
        scenario_details: The scenarios' details — the ticks the algo path processed
        profiling: The profiling report — the ticks the loop iterated where a budget clipped
        run_summary: The run summary — the market time the scenarios processed

    Returns:
        The run meta report with its throughput
    """
    total_ticks = fold_group(scenario_details.units, RUN_THROUGHPUT_TABLE)['total_ticks_processed']
    # What the loop iterated: the clipping total where a budget clipped, else the algo ticks
    clipping_total = profiling.aggregate.clipping_total_ticks
    loop_ticks = clipping_total if clipping_total > 0 else total_ticks
    tickrun = run_meta.tickrun_time_s
    processed_hours = (run_summary.tick_timespan_total_seconds or 0.0) / 3600
    return run_meta.model_copy(update={
        'total_ticks_processed': total_ticks,
        'loop_ticks': loop_ticks,
        'has_clipping': clipping_total > 0 and clipping_total != total_ticks,
        'ticks_per_second': loop_ticks / tickrun if tickrun > 0 else 0.0,
        'speedup': (processed_hours * 3600) / tickrun if tickrun > 0 else 0.0,
        'ticks_per_hour': loop_ticks / processed_hours if processed_hours > 0 else 0.0,
        'avg_hours_per_unit': (processed_hours / run_summary.unit_count
                               if run_summary.unit_count else 0.0),
    })
