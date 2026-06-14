# Diagnostics CSV Sink

`DiagnosticsCsvSink` (`python/framework/reporting/diagnostics_csv_sink.py`) is a generic
per-run CSV channel for **strategy-owned diagnostics** — signal funnels, near-miss analysis,
per-attempt quality metrics. The split that keeps it clean:

> **The framework owns the file logistics; the strategy owns the schema.**

The strategy declares the columns and appends rows; the framework decides where the file
goes, what it is named, and when it is written — identically in both pipelines. This is
distinct from `events.csv` (`event_stream_csv_writer.py`), which is a fixed trade-domain
schema reconstructed post-loop.

## Why

A backtest's trade record (`events.csv`) answers *what was traded*. It does not answer *why
a setup did or did not fire* — the funnel of attempts, where they dropped out, how close the
near-misses were. Strategies need their own structured artifact for that, but they should
not each reinvent run-directory placement, naming, and flush lifecycle. The sink provides one
audited channel; the strategy only declares columns and appends rows.

## API

On `AbstractDecisionLogic`:

```python
sink = self.diagnostics_csv('setup_funnel', ['pattern_id', 'outcome', 'aggregated_confidence'])
sink.append_row({'pattern_id': 'p1', 'outcome': 'TRADED', 'aggregated_confidence': 0.62})
```

- `diagnostics_csv(name, columns)` — get-or-create a named sink (same name → same instance).
- The sink buffers rows in memory; `append_row(dict)` is called at decision moments
  (low-frequency), so **nothing touches the hot tick path**. Declared columns missing from a
  row render as empty cells; extra keys are ignored.

## Lifecycle (framework-owned)

The framework flushes every declared sink once at run end via
`flush_decision_diagnostics(decision_logic, run_dir, scenario_suffix=None)`, into a dedicated
**`diagnostics/` subfolder** of the run directory — strategy-owned ("custom") artifacts kept
separate from the framework's trade-event CSVs, and the run dir kept tidy when many scenarios
produce many files:

- **AutoTrader** (`autotrader_main.py`): flushed at session end → `diagnostics/<name>.csv`.
- **Simulation** (`process/process_main.py`): flushed in the scenario subprocess after the
  tick loop → `diagnostics/<name>_<scenario>.csv`. The scenario suffix matches
  `events_<scenario>.csv`, the join-partner — join on scenario + `order_id`.

A no-op when file logging is disabled (`run_dir` None) or no rows were appended (the
subfolder is created lazily, only when there is something to write).

```
run dir
├── events.csv                         ← framework trade record (AutoTrader)
├── events/  events_<scenario>.csv      ← framework trade record (simulation, per scenario)
└── diagnostics/                        ← strategy-owned diagnostics (this sink)
    ├── <name>.csv                       (AutoTrader)
    └── <name>_<scenario>.csv            (simulation, per scenario)
```

## Boundary

Rows accumulate in memory and write once at run end — a crash mid-run loses the buffered
diagnostics. That is acceptable: this is diagnostics, not the trade record, and it matches
the §35 villain/pot separation (a hard crash is the villain; diagnostics are not a recovery
artifact). For a long live session this trades crash-durability for zero hot-path cost; an
incremental-flush mode can be added later if needed.

Exposed on decision logic only for now (workers can follow if demand appears). The schema is
entirely the strategy's — the framework never inspects the columns or values.
