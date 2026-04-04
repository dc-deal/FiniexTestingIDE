# Batch Data Flow: Main Process → Subprocesses → Reports

The batch execution pipeline moves data through three distinct channels. Understanding which channel carries what is critical for avoiding unnecessary serialization and keeping subprocess boundaries clean.

> **Core architecture:** see [architecture_execution_layer.md](architecture_execution_layer.md)
> **Batch orchestration (7 phases):** see [batch_preparation_system.md](../data_pipeline/batch_preparation_system.md)
> **Subprocess execution:** see [process_execution_guide.md](../process_execution_guide.md)

---

## The Three Data Channels

```
MAIN PROCESS                          SUBPROCESSES                    REPORTS
─────────────────────────────────────────────────────────────────────────────

Channel A: Process Input (pickle →)
  ProcessDataPackage ──────────────→  tick loop consumes
  (ticks, bars, broker_configs)       data, produces results
                                           │
Channel B: Process Output (← pickle)       │
  ProcessResult ←──────────────────────────┘
  (execution_stats, trade_history,
   order_history, pending_stats,
   worker_performance, ...)

Channel C: Main-Process Only (no serialization)
  SingleScenario ─────────────────────────────────→ BatchExecutionSummary
  (data_format_versions, validation,                      │
   scenario config, logger, ...)                          ▼
                                                    Report Sections
  BrokerConfig (via broker_scenario_map) ─────────→ (PortfolioSummary,
                                                     BrokerSummary, ...)
```

## Channel A: Process Input (`ProcessDataPackage`)

Data prepared in the main process and distributed to subprocesses via pickle serialization:

- **Ticks**: Symbol → Tuple of tick objects (immutable, CoW-shared)
- **Bars**: Warmup bar data per symbol/timeframe
- **Broker configs**: Serialized dict for subprocess re-hydration (loaded once, shared via CoW)

Each scenario gets its own package (3-5 MB) instead of one global package (61 MB) — 5x pickle overhead reduction.

## Channel B: Process Output (`ProcessResult`)

Results returned from subprocesses after tick loop execution:

- Execution statistics, cost breakdown
- Trade history, order history
- Worker performance stats, decision logic stats
- Pending order statistics
- Scenario logger buffer

**Important**: Only data *produced during the tick loop* crosses back. Input data (broker configs, scenario metadata) is NOT returned — the main process already has it.

## Channel C: Main-Process Only (`SingleScenario`, `BrokerConfig`)

Data that stays in the main process and feeds reports directly via `BatchExecutionSummary`:

- **`SingleScenario`** — enriched during data preparation (e.g., `data_format_versions` populated from Parquet metadata). Never pickled to subprocesses. Available in `BatchExecutionSummary.single_scenario_list` for report sections.
- **`broker_scenario_map`** — broker configs grouped by `BrokerType`. Distributed to subprocesses (Channel A) for execution but NOT returned (Channel B). Independently available in `BatchExecutionSummary.broker_scenario_map` for `BrokerSummary` rendering.

## Why This Matters

Adding metadata to reports does NOT require threading through subprocesses. The pattern for new report data:

1. **If the data exists before subprocess launch** (e.g., Parquet metadata, index info, config values):
   → Populate on `SingleScenario` during data preparation → access in report via `BatchExecutionSummary.single_scenario_list`

2. **If the data is produced during tick execution** (e.g., trade results, performance stats):
   → Return in `ProcessResult` → aggregate in report sections

3. **If the data is loaded once for all scenarios** (e.g., broker configs):
   → Distribute via `ProcessDataPackage.broker_configs` AND tag on `BatchExecutionSummary.broker_scenario_map`. No round-trip.

**Example**: `data_format_versions` (V1.3.0 warning) follows pattern 1 — populated from tick index during `SharedDataPreparator.prepare_scenario_packages()`, stored on `SingleScenario`, read by `WarningsSummary._build_data_version_warning()`. Zero subprocess overhead.

## Report Sections: WarningsSummary

`WarningsSummary` (`python/framework/batch_reporting/warnings_summary.py`) consolidates all global warnings into a single report section. Unlike other report sections, it is **always rendered** regardless of the `summary.detail` flag, but only when at least one warning is active.

Current warnings:
- **Stress test active** — lists active stress test configs grouped by signature
- **Data format version** — flags pre-V1.3.0 data with synthesized `collected_msc` intervals, includes Kraken-specific caveat about synthetic 1ms fill spacing
