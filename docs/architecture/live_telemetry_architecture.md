# Live Telemetry Stream — Console Dashboards & Viewer Push (Groundwork)

The **live telemetry stream** is the throttled, lossy per-run feed that drives the live console
dashboards *while a run is in flight*. It is the deliberate counterpart of the unified **report
pipeline** — and is kept strictly separate from it (see [Boundary](#boundary-vs-the-report-pipeline)).

Both execution pipelines emit this feed through one shared model, derived off the hot loop:

```
CAPTURE (per pipeline, throttled ~300ms)        MODEL (shared core)            PRESENT (surfaces)
  sim subprocess ─ process_live_export ──┐                                     ┌─► console (rich.live)   ← today
                   builds LiveScenarioStats ├─► LiveCoreSnapshot ───────────────┼─► JSONL replay (#379)   ← groundwork
  live thread    ─ AutotraderDisplayExporter┘   + per-domain extension          └─► socket push (#380/#331) ← groundwork
                   builds AutoTraderDisplayStats
```

## The model

| Type | Home | Role |
|---|---|---|
| `LiveCoreSnapshot` | `framework/types/live_types/live_core_snapshot_types.py` | the subset BOTH frames share: `symbol`, `ticks_processed`, `balance`, `initial_balance`, `total_trades`, `winning_trades`, `losing_trades`, `last_awareness` |
| `LiveScenarioStats` | `framework/types/live_types/live_scenario_stats_types.py` | **sim** frame: `core` + batch-progress envelope (`progress_percent`, `total_ticks`, in-time tracking, `portfolio_dirty_flag`) + optional detailed exports (`portfolio_stats`, `current_bars`) |
| `LiveStatusFrame` | same unit | **sim** lightweight status-only update (warmup / lifecycle), carried on the same queue |
| `AutoTraderDisplayStats` | `framework/types/autotrader_types/autotrader_display_types.py` | **live** frame: `core` + rich session state (positions, orders, trades, clipping, safety, rejections, drift, reconcile, api_perf, events, pulse) |

The shared core is **composed** (a `core: LiveCoreSnapshot` field on each frame), not collapsed —
the two pipelines stay separate, exactly like the report pipeline's `RunUnit`. A run is a list of
units: simulation emits N scenario frames; AutoTrader emits 1 session frame.

## Console flow — both pipelines

The strategy layer is identical in both modes; only the **transport** and the **frame builder**
differ. The throttle (`update_interval_ms`, default 300 ms) keeps the feed off the per-tick
critical path.

### Simulation (batch) — cross-process

```
subprocess (ProcessPool worker)                         batch process
───────────────────────────────                         ─────────────
process_tick_loop.execute_tick_loop
   └─ process_live_export.process_live_export  ─────┐
        builds LiveScenarioStats(core=…)            │  multiprocessing.Queue
   └─ process_live_queue_helper / LiveStatsCoordinator   (pickled, maxsize=100)
        build LiveStatusFrame (warmup/lifecycle)    │
                                                     ▼
                                            LiveProgressDisplay._process_update
                                              isinstance dispatch:
                                                LiveStatusFrame → touch status only
                                                LiveScenarioStats → replace cache entry
                                              renders progress bars + balances + awareness
```

The frame is built typed in the producer and put on the queue directly — the consumer no longer
reconstructs it field-by-field. A `multiprocessing.Queue` pickles every payload; a dataclass costs
the same as the former dict (which already built intermediate dicts).

### AutoTrader (live) — in-thread

```
main thread (AutotraderTickLoop.run)                    display thread
────────────────────────────────────                   ──────────────
per tick / heartbeat:
   AutotraderDisplayExporter.build(...)        ─────┐
     builds AutoTraderDisplayStats(core=…)          │  queue.Queue
     (volatile state — safety, rejections,          │  (object by reference, maxsize=10)
      spot-equity baseline — passed per call)        │
                                                     ▼
                                            AutoTraderLiveDisplay
                                              reads stats.core.* + stats.* directly
                                              renders the panel dashboard
```

`AutotraderDisplayExporter` holds the stable collaborators (executor, orchestrator, monitors,
config); the tick loop passes the volatile per-frame state and stays orchestration-only. An
in-thread `queue.Queue` passes the dataclass by reference (no pickling — same process).

## Viewer push — preview (groundwork only)

The frames are JSON-serializable so the FiniexViewer live windows (#379 Algo State / Session /
Portfolio / Trade History / Orders) can render the **same** model. The encoder is a thin
PRESENT-stage step:

```python
# framework/utils/live_frame_serialization_utils.py
frame_to_json(frame)  # = serialize_value(asdict(frame))  — enums→value, datetime→isoformat
```

The frames stay `@dataclass` runtime domain types (§6); JSON is a render concern. **The push
transport itself is not built here** — it is owned by #380 (live streaming, transport designed
once with #331's WebSocket) and #379 (the per-run JSONL replay artifact). The hard part is the
**cross-process bridge**: the sim feed already crosses a process boundary (subprocess → batch
process), and the read-only API server ([api_server_architecture.md](api_server_architecture.md))
is a separate uvicorn process — so exposing the live feed needs a bridge (in-process host, a light
message bus, or a JSONL tail that doubles as #379's replay artifact). That design lives in #380.

## Boundary vs the report pipeline

This stream is **not** the [reporting pipeline](reporting_pipeline.md). The report pipeline is the
coherent, post-derived artifact set written at run end (and persisted / REST-served). The telemetry
stream is the fast, lossy live feed. The two never share code:

| | Report pipeline (#391) | Live telemetry stream (this) |
|---|---|---|
| When | end of run (+ snapshot #392) | during the run, throttled |
| Fidelity | coherent, derived once | lossy, latest-wins |
| Transport | persisted JSON/CSV → REST | mp.Queue / thread queue → console (push later) |
| Model | `report_types.py` (Pydantic) | `LiveCoreSnapshot` + frames (`@dataclass`) |

## Files

- Producers: `framework/process/process_live_export.py` (sim), `framework/process/process_live_queue_helper.py` + `framework/batch/live_stats_coordinator.py` (sim status), `framework/autotrader/autotrader_display_exporter.py` (live)
- Consumers: `system/ui/live_progress_display.py` (sim), `system/ui/autotrader_live_display.py` (live)
- Model: `framework/types/live_types/live_core_snapshot_types.py`, `live_scenario_stats_types.py`, `framework/types/autotrader_types/autotrader_display_types.py`
- Serializer: `framework/utils/live_frame_serialization_utils.py`
- Tick-flow context: [simulation_vs_live_flow.md](simulation_vs_live_flow.md)
