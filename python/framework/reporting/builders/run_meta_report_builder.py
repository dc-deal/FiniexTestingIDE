"""
FiniexTestingIDE - Run Meta Report Builder

Projects the run-level execution facts the orchestrator measures primarily (timing split +
scenario identity) into the `RunMetaReport` model — once, at DERIVE. Reads them straight from
the source (`BatchExecutionSummary`, where the orchestrator stored its `time.time()` measurements),
so PRESENT no longer reaches back into the raw type for them.
"""
from python.framework.types.api.report_types import RunMetaReport
from python.framework.types.batch_execution_types import BatchExecutionSummary


def build_run_meta_report_from_batch(run_id: str, batch: BatchExecutionSummary,
                                     disabled_count: int = 0) -> RunMetaReport:
    """
    Build the run-level meta report from the batch execution summary.

    Args:
        run_id: The run this report belongs to
        batch: The finished batch execution summary (the orchestrator's measurements)
        disabled_count: Scenarios the set switched off, as the loader counted them — the batch
            cannot say, because the loader drops them before any batch exists

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
    )
