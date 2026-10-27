# Live Telemetry Stream — Console Dashboards & Viewer Push (Groundwork)

The **live telemetry stream** is the throttled, lossy per-run feed that drives the live console
dashboards *while a run is in flight*. It is the deliberate counterpart of the unified **report
pipeline** — and is kept strictly separate from it (see [Boundary](#boundary-vs-the-report-pipeline)).

Both execution pipelines emit this feed through one shared model, derived off the hot loop:

```
CAPTURE (per pipeline, throttled ~300ms)        MODEL (shared core)            PRESENT (surfaces)
  sim subprocess ─ process_live_export ──┐                                     ┌─► console (rich.live)   ← today
                   builds LiveScenarioStats ├─► LiveCoreSnapshot ───────────────┼─► JSONL replay (#379)   ← groundwork
  AutoTrader     ─ AutotraderDisplayExporter┘   + per-domain extension          └─► socket push (#380/#331) ← groundwork
                   builds AutoTraderDisplayStats
```

## The model

| Type | Home | Role |
|---|---|---|
| `LiveCoreSnapshot` | `framework/types/live_types/live_core_snapshot_types.py` | the subset BOTH frames share: `symbol`, `ticks_processed`, `balance`, `initial_balance`, `total_trades`, `winning_trades`, `losing_trades`, `last_awareness` |
| `LiveScenarioStats` | `framework/types/live_types/live_scenario_stats_types.py` | **sim** frame: `core` + batch-progress envelope (`progress_percent`, `total_ticks`, in-time tracking, `portfolio_dirty_flag`) + optional detailed exports (`portfolio_stats`, `current_bars`) |
| `LiveStatusFrame` | same unit | **sim** lightweight status-only update (warmup / lifecycle), carried on the same queue |
| `AutoTraderDisplayStats` | `framework/types/autotrader_types/autotrader_display_types.py` | **AutoTrader** frame: `core` + rich session state (positions, orders, trades, clipping, safety, rejections, drift, reconcile, api_perf, events, pulse) |

The shared core is **composed** (a `core: LiveCoreSnapshot` field on each frame), not collapsed —
the two pipelines stay separate, exactly like the report pipeline's `RunUnit`. A run is a list of
units: simulation emits N scenario frames; AutoTrader emits 1 session frame.

> **Worker cadence telemetry (#420).** A `BAR_CLOSE` worker computes far less than once per tick,
> so its per-tick timing reads 0 ms on the ticks it serves a cached value. The honest read —
> `compute_basis`, the compute/tick ratio, and the ticks-idle-since-last-compute — is carried in
> the **end-of-run WORKER report** (`performance_summary`, model-fed from `WorkerPerformanceStats`).
> Surfacing it live on the AutoTrader dashboard (a "last compute: <tf> close, X ms (N idle)" line)
> is a follow-up — the data already lives on the worker stats.

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

### AutoTrader — in-thread

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

### Rendering, and what a failure does

The dashboard renders through one public entry, `AutoTraderLiveDisplay.render(stats, connection,
now, width)`. Everything a frame shows arrives there: the snapshot, the tick source's readings
(polled once per frame into a `TickSourceSnapshot`), the wall clock and the terminal width.
Nothing in the render path reads a live object, so every panel is rendered by a test from a
fully populated frame — at each layout, and with every way a trade can close — without a
session, a thread or a terminal.

A failure never ends a session or a batch, and it is never silent:

- **A panel whose builder raises** shows as failed in place; the rest of the frame renders.
- **A frame that cannot be printed** — markup that only fails when it is printed — never reaches
  the live display. Each frame is printed once into a scratch console first, and one that fails
  is replaced by a failure frame and tried again on the next cycle. The order matters: a live
  display re-renders the frame it holds whenever ANY thread prints, so an unprintable frame, once
  held, makes that other thread's print raise — the tick loop's own logging included. Printing
  happens in the display's own loop rather than in a refresh thread of rich's own. A venue's or a
  strategy's words are escaped before they enter a panel, so a bracket in them is text.
- **Each failure is reported once**, and only a few per place, by
  `system/ui/render_failure_reporter.py`: the AutoTrader dashboard writes to the session log as
  an ERROR, so the session ends graded as finished with errors; the simulation's progress screen
  and the import screen write to the global log.

Should a display thread still end, the run is unaffected: the producers hand frames over with
`put_nowait` and drop them when nobody takes them, so nothing ever waits on the screen. A full
queue is the one failure they skip without a word. Any other failure to hand a frame over is
reported to the global log once and never raised; a scenario whose frames cannot be sent stops
sending them and runs on.

## Viewer push — preview (groundwork only)

The frames are JSON-serializable so the FiniexViewer live windows (#379 Algo State / Session /
Portfolio / Trade History / Orders) can render the **same** model. The encoder is a thin
PRESENT-stage step:

```python
# framework/utils/live_frame_serialization_utils.py
frame_to_json(frame)  # = serialize_value(asdict(frame))  — enums→value, datetime→isoformat
```

The frames stay `@dataclass` runtime domain types; JSON is a render concern. **The push
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

## The recorded state frame — not this stream

The dashboard's frame is built only while a display runs, carries the latest state and drops the
rest. The **state frame** (#579 part A) is a record: an AutoTrader session writes it into
`io/state_frames.jsonl` whether or not a display runs — on a cadence of the canonical clock, when a
bar of a timeframe its workers read closes, when the decision sends an order, and once at its end,
at most one per pass of the loop. It is what a backtest of the same period is compared against
(#512), and what the check-in on a running session reads (#611).

| | Dashboard frame (this stream) | State frame (#579) |
|---|---|---|
| Written | while a display runs, every refresh | always, on its cadence and at its events |
| Kept | latest wins | every frame, one flushed line each |
| Outputs | those marked `display=True` | every output of every worker and of the decision |
| A health nobody checked | a default (`reconcile_clean` true without a reconciler) | null |
| Positions and orders | the dashboard's own rows | the rows the report serves, so the last frame agrees with it |

It is built on its own from the session's objects — `framework/autotrader/run_record/state_frame_recorder.py`,
the line `framework/types/api/state_frame_types.py` — and written through the run-stream appender
(`framework/reporting/io/run_stream_appender.py`). At each trading-day boundary the session seals the
day's frames into `days/<day>/io/state_frames.parquet` and the file starts afresh
([Day Records](run_stream_day_records.md)). The process's
memory rides on every frame of a live session. A replay compresses hours into seconds, and reading
the memory costs about a millisecond in a session-sized process, so a replay reads it at most once
per cadence interval of the monotonic clock and at its last frame.

**Served while it is written (#611).** `GET /api/v1/reports/runs/{run_id}/state-frames/latest`
reads the stream's two ends — its header, its first frame and its last complete line — never the
lines between (`framework/reporting/io/run_stream_union_reader.py`), across the sealed days and the
running file: right after a seal the latest frame is the sealed day's last. It serves each unit's
latest frame
with its age and `recording`: `current`, `stopped` (no frame for three cadences and no end
recorded) or `ended`. Nothing reaches into the running session; the record is the only thing
between the session and the API server. The consumer's side is the served document
[state frames](../consumer/state-frames.md).

## Files

- Producers: `framework/process/process_live_export.py` (sim), `framework/process/process_live_queue_helper.py` + `framework/batch/live_stats_coordinator.py` (sim status), `framework/autotrader/autotrader_display_exporter.py` (AutoTrader)
- Consumers: `system/ui/live_progress_display.py` (sim), `system/ui/autotrader_live_display.py` (AutoTrader)
- Failure reporting: `system/ui/render_failure_reporter.py` (all live screens, the import screen included)
- Model: `framework/types/live_types/live_core_snapshot_types.py`, `live_scenario_stats_types.py`, `framework/types/autotrader_types/autotrader_display_types.py`
- Serializer: `framework/utils/live_frame_serialization_utils.py`
- Tick-flow context: [simulation_vs_autotrader_flow.md](simulation_vs_autotrader_flow.md)
