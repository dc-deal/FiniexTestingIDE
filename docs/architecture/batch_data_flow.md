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

**The package dict is keyed by `SingleScenario.scenario_index`, never by a loop position.** The index is assigned once at config load (`scenario_config_loader.py`) and stays with the scenario; `SharedDataPreparator` fills the dict with it. Some consumers receive the COMPLETE scenario list (`ExecutionCoordinator`) and some receive the list FILTERED to the still-valid scenarios (`ScenarioDataValidator`, via `mount_preparer._valid()`), so a position matches the index only in the first case — and only until one scenario is excluded. Keying by position silently pairs a scenario with a neighbour's data. A missing package raises `ScenarioPackageMissingError`: after keying correctly, a hole can only mean the preparator and the consumer disagree about what was prepared, which is framework logic and not operator config (§33).

**The scenario log buffer crosses as `list[LogRecord]`, not as rendered lines.** A record
(`framework/types/log_record_types.py`) carries level, observation timestamp, scope, message and
— inside the tick loop — the tick index and the tick's own time. Rendering (colours, the level
column, the elapsed timestamp, the tick prefix) happens at the surface that prints it. A buffer
of rendered lines forces every later consumer to take the fact apart again, and the run report is
such a consumer: it used to recover the message with `split(' | ', 1)` and carried ANSI escape
codes into the persisted JSON on the way.

### Where a run's logs land — three categories, one source

A run belongs to exactly ONE category, and the category IS its `group` in the API:

```
file_logging.run_logs.autotrader    logs/autotrader/<profile>/<run_ts>/
file_logging.run_logs.single_runs   logs/scenario_sets/single_runs/<set>/<run_ts>/
file_logging.run_logs.sweeps        logs/scenario_sets/sweeps/<sweep_id>/<set>/<run_ts>/
```

**The three paths are configuration** (`app_config.json` → `file_logging.run_logs`), read by the
writers (`ScenarioSet`, `autotrader_startup`) AND by `ReportStore` — one source, so a moved log
root cannot make runs invisible to the API. Before that, the sim root was config, the live root
was a hard-coded `Path('logs/autotrader')` and the reader assumed a third thing: changing the
config would silently have emptied the run index.

The category NAMES live in `framework/types/log_layout_types.py`, because they are a contract:
the API publishes them as `RunInfo.group`. The run index lists every category — a consumer that
wants only standalone runs filters on the group, which it can, and an index that silently omitted
a category would be its own surprise. `/sweeps` adds the sweep-shaped view on the same data:
one sweep, its combinations ranked by the objective the sweep declared.

The distinction is structural on purpose. A sweep is not a run, it is a family of them, and the
two want different views: `/reports/runs` lists standalone runs, `/sweeps` lists sweeps and ranks
their combinations. Keeping them apart by PATH means the index needs no filter on names, and a
combination cannot silently reappear in the run picker. It stays fully **addressable** — every
report route resolves any `run_id`, at any depth; the index is a browse aid, not the authority
on what can be read.

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

**Example**: `data_format_versions` follows pattern 1 — populated from tick index during `SharedDataPreparator.prepare_scenario_packages()`, stored on `SingleScenario`, judged by `PostRunValidator._check_data_version()` and rendered by `WarningsSummary`. Zero subprocess overhead.

## Report Sections: Spot-Aware Reporting

`PortfolioStats` carries spot-mode fields (`spot_mode`, `balances`, `initial_balances`, `last_price`, `symbol`) populated in the tick loop via `PortfolioManager.get_portfolio_statistics()`. These follow pattern 2 (produced during tick execution, returned in `ProcessResult`).

Reporting adapts layout based on `portfolio_stats.spot_mode`:
- **Margin mode** — single balance line (`Balance: $10,000.00`)
- **Spot mode** — dual balance lines (`USD 9,800.00 | ETH 0.0500`) with estimated portfolio value at last market price

Mixed batches (margin + spot scenarios in the same currency group) are split into separate subtotals in the executive summary. Portfolio boxes show a `[SPOT]` suffix for spot scenarios.

## Report Sections: WarningsSummary

`WarningsSummary` (`python/framework/reporting/console/warnings_summary.py`) consolidates all global warnings into a single report section. Unlike other report sections, it is **always rendered** regardless of the `summary.detail` flag — with a clean zero-state line when there are none.

Current warnings:
- **Stress test active** — lists active stress test configs grouped by signature
- **Data format version unknown** — the tick index carries no version for a file; points at the index rebuild. The version itself is a declared schema (an operator-set collector input), so nothing derives a data-quality claim from it
