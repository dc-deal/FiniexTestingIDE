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
the run's own `event_time`. Rendering (colours, the level column, the elapsed timestamp, the
event-time column) happens at the surface that prints it.

The two times are §9's pair and must not be conflated: the elapsed bracket is OBSERVATION time
(how far into the run we were), the column is EVENT time (what time it was in the market, from
the canonical clock). The clock is PULLED through an injected `clock_fn`, attached once the
executor exists — so heartbeat and ghost passes stamp their own instant, and the timer /
resolution events of #375 need no further wiring. Whether a log renders the column at all is a
ROLE declared at construction (`event_time_column`): the per-scenario logs and the live session
log carry it, the run-level logs do not, and a declared column with no clock yet renders a
fixed-width filler rather than a wall-clock substitute. A buffer
of rendered lines forces every later consumer to take the fact apart again, and the run report is
such a consumer: it used to recover the message with `split(' | ', 1)` and carried ANSI escape
codes into the persisted JSON on the way.

### What identifies a run (#475)

A run is identified by `run_id` — `<timestamp>_<hash>`, e.g. `20260830_132034_a3f9c2d1`. The
readable half keeps ordering intact (the run index sorts on it, the sweep ranking tie-breaks on
it); the random half makes it distinct, because a second-resolution stamp collided in ordinary
use — 4 of 188 runs, and a collision made the API serve a different run's artifacts than the index
listed under that id.

It is minted ONCE per run and passed down: to the three loggers of a live session, and through
`ProcessScenarioConfig` into each simulation subprocess. Deriving it per logger would give one run
three ids and therefore three directories.

Each run writes a `header.json` at its START — id, start time, category, owner, and the parent it
belongs to (a sweep, today; a session for #476's daily fragments). At the start rather than the
end, because a run that crashes is exactly the run somebody needs to identify.

`runs/runs_index.parquet` is ONE compacted file DERIVED from those headers, and it is what the API
reads. Derived is the point: it may be deleted or go stale without anything being lost —
`run_index_cli.py rebuild` reconstructs it. Directories stay human-navigable (§36) but no program
reads meaning out of them any more.

### Where a run's logs land — three categories, one source

A run belongs to exactly ONE category, and the category IS its `group` in the API:

```
file_logging.run_logs.simulation    runs/simulation/<set>/<run_id>/
                                    runs/simulation/sweeps/<sweep_id>/<combination>/<run_id>/
file_logging.run_logs.live          runs/live/<profile>/<run_id>/
```

**Two roots, because there are two run TYPES** — and the type is the PIPELINE, not the nesting.
A sweep combination is a `simulation` with a `parent_id`; a live day fragment (#476) will be a
`live` with a `parent_id`. Folding nesting into the type would make the most basic question —
"is this a simulation?" — a two-value comparison, and would need a new value for every new kind
of parent.

**The paths are configuration** (`app_config.json` → `file_logging.run_logs`), read by the
writers (`ScenarioSet`, `autotrader_startup`) AND by the run index — one source, so a moved log
root cannot make runs invisible to the API. Before that, the sim root was config, the live root
was a hard-coded `Path('logs/autotrader')` and the reader assumed a third thing: changing the
config would silently have emptied the run index.

The type NAMES live in `framework/types/log_layout_types.py`, because they are a contract: the
API publishes them as `RunInfo.group`. The index lists both types — a consumer that wants only
standalone runs filters on `parent_id`, and an index that silently omitted a type would be its
own surprise. `/sweeps` adds the sweep-shaped view on the same data: one sweep, its combinations
ranked by the objective the sweep declared.

**A directory means nothing to any program (#475).** Identity is a field: a run is located
through the run index by an exact `run_id` match, never by walking the tree, so the nesting level
of a sweep combination is invisible to every consumer. The tree stays the way a HUMAN browses it.
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
