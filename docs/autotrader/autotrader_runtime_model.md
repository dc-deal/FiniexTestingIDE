# AutoTrader Runtime Model

A live session is two threads, one queue and one ordered lifecycle, and getting any of the three
wrong costs money rather than a test. A tick that arrives while the algorithm is still deciding
must not be dropped; a shutdown that skips a phase leaves an order at the venue that nobody owns;
a restart that forgets what the previous session held reads its own holding as flat.

This document is the runtime shape: which thread does what, why the tick source and the broker
adapter are two things, how a session starts, runs and ends, and what survives a restart.

**Not here:** what the bot may spend and what stops it — `autotrader_capital_and_safety.md`. How a
tick gets in — `autotrader_data_intake.md`. What the session prints — `autotrader_observability.md`.

## Threading Model (8.a)

Synchronous algo processing in the main thread. Tick source in a separate thread. Display in a third thread. Communication via `queue.Queue` (stdlib, thread-safe).

```
Thread 1 (Tick Source):       Thread 2 (Main — Algo):          Thread 3 (Display):
──────────────────────       ────────────────────────          ──────────────────────
while running:               while running:                    while running:
  tick = source.next_tick()    tick = queue.get(timeout=1)       stats = display_q.drain()
  tick_q.put(tick) ────────→                                     layout = render(stats)
                               executor.on_tick(tick)             live.update(layout)
                               bars = process_tick(tick)          sleep(0.3s)
                               decision = orchestrate(...)
                               execute_decision(...)           Connection stats polled
                               clipping_monitor.record(...)    directly from tick_source
                               display_q.put(stats) ──────→   (GIL-safe primitive reads)
```

**Why sync in main thread?** Workers and DecisionLogic are not async-safe. The queue pattern avoids "async infection" — the tick source handles I/O, the algo loop stays synchronous.

### Queue Performance

`queue.Queue.put()` → `queue.get()` latency: **~1-5 µs**. BTCUSD tick interval: ~5-50 ms. The queue is ~1000x faster than the tick rate — no bottleneck. Both threads sleep efficiently when idle (Lock + Condition, no busy-wait).

### Why Not Async? — "Async Infection"

If the tick source were `async` in the same thread, every downstream caller would need to become `async` too:

```python
async def run_tick_loop():
    tick = await websocket.recv()             # async!
    result = await worker.process_tick(tick)   # Worker must become async!
    decision = await logic.execute(result)     # Logic must become async!
    await executor.send_order(decision)        # Executor must become async!
```

This breaks the design constraint: Workers and DecisionLogic must be **identical** classes in
backtesting and live. The queue stops the infection — Thread 1 can use `await websocket.recv()`
internally (#232), Thread 2 only sees synchronous `queue.get()`.

## Tick Sources vs Broker Adapters — Separation of Concerns

Tick sources and broker adapters are **intentionally separate abstractions**, even though both connect to the same exchange (e.g., Kraken). This is an explicit design decision, not an accident of file layout.

### Why They Are Separate

| Aspect | Broker Adapter | Tick Source |
|--------|---------------|------------|
| **Responsibility** | Order execution, symbol specs, fees, margin | Continuous tick delivery |
| **Protocol** | REST API (request/response) | WebSocket (continuous stream) |
| **Threading** | Synchronous, called from main thread | Own daemon thread, pushes to queue |
| **Lifecycle** | On-demand (called when executor needs it) | Permanent (runs entire session) |
| **Auth** | Required for orders (API key/secret) | Often public (market data) |
| **Error handling** | Exception → OrderResult.REJECTED | Reconnect loop with backoff |
| **Used by** | Backtesting + AutoTrader | AutoTrader only |

### Independent Combinability

Keeping them separate enables mix-and-match testing:

| Tick Source | Adapter | Use Case |
|---|---|---|
| `MockTickSource` | `MockBrokerAdapter` | Full pipeline test (no external deps) |
| `MockTickSource` | `KrakenAdapter` | Test order execution with replay data |
| `KrakenTickSource` | `MockBrokerAdapter` | Test WebSocket feed without real orders |
| `KrakenTickSource` | `KrakenAdapter` | Production live trading |

Merging them into one class would lose this combinability.

### Industry Reference

Institutional systems (Bloomberg, Refinitiv, FIX protocol) always separate Market Data Gateway from
Order Gateway — different latency requirements, protocols, and failure modes. Retail platforms (MT5,
cTrader) bundle them in the UI but separate them internally.

### How They Connect

The config maps each independently, `autotrader_startup.py` wires them together:

```json
{
  "broker_type": "kraken_spot",        // → KrakenAdapter (via BrokerConfigFactory)
  "tick_source": { "type": "kraken" }  // → KrakenTickSource (via setup_tick_source)
}
```

`broker_type` is intentionally broader than "adapter" — it selects the full broker configuration
(fees, symbol specs, market type, leverage) through `BrokerConfigFactory` and `market_config.json`.
The adapter is one part of that. `tick_source.type` maps directly to a `TickSource` class.

### Directory Structure Rationale

```
python/framework/
  trading_env/              ← Execution layer (backtesting + live)
    adapters/               ← Broker ops — used by BOTH contexts
    live/                   ← LiveTradeExecutor — AutoTrader only
    simulation/             ← TradeSimulator — backtesting only
  autotrader/               ← Live runner application
    reporting/              ← Session reports (console, CSV) — AutoTrader only
    tick_sources/           ← Data feeds — AutoTrader only
```

`trading_env/` is the **framework layer** — shared between backtesting and AutoTrader. `autotrader/`
is the **application layer** — AutoTrader only. Tick sources live in `autotrader/` because they are
exclusively a live concern. Moving them into `trading_env/adapters/` would leak live-only components
into the shared framework.

## Session Lifecycle

### Startup

1. Load `AutoTraderConfig` from JSON
2. `setup_pipeline()` creates all objects (mirrors backtesting) and returns them as one
   `AutotraderPipelineBundle`
3. `setup_tick_source()` starts tick source thread
4. Enter tick loop

Between steps 2 and 3, `_validate_startup()` refuses what must not start at all — one session,
nothing to exclude, so it aborts. Among its refusals: a session that would send REAL
orders from uncommitted code, and one whose code changed between the capture of its code identity
and the pipeline loading it. `--allow-dirty` lets a deliberate test through the first, recorded in
the run header and reported as a Tier-1 warning, and never through the second — see
[Run Origin and Code Identity](../architecture/run_origin_and_code_identity.md#real-orders-from-uncommitted-code).

### Tick Loop

Each tick follows the same 5-step path as backtesting:

1. **Broker Path** — `executor.on_tick(tick)` — pending order processing, price updates
2. **Bar Rendering** — `bar_controller.process_tick(tick)` — aggregate ticks into OHLC bars
3. **Bar History** — `bar_controller.get_all_bar_history()` — retrieve history for workers
4. **Worker + Decision** — `orchestrator.process_tick()` → decision
5. **Order Execution** — `decision_logic.execute_decision()` → orders via executor

After each tick: `clipping_monitor.record_tick()` measures processing time.

When the tick queue times out (no tick within `heartbeat_interval_ms`, default
1000 ms), the loop fires a **timer event** instead of falling through silently —
the single main-loop consumer runs the cadence work without a second thread (#360):

1. `executor.set_current_time(now)` — inject the wall-clock so the canonical clock
   advances during idle (phase/op timeouts track real elapsed time, not a frozen tick).
2. `executor.heartbeat()` — drain async worker responses (submit, edit, cancel,
   query, trades), process order timeouts, **and re-poll active orders** so the
   fill/cancel-confirm query fires during idle (not only on a real tick).
3. `reconciler.reconcile()` if due — broker truth-pull on the timer too (was tick-only),
   self-throttled by `min_interval_seconds`.
4. `orchestrator.process_heartbeat()` — a **decision ghost-pass**: for a logic that
   opts in via `wants_heartbeat()`, the decision runs with `tick=None` and the cached
   worker results (workers do not recompute) so it can advance internal state, react to
   drained events, and issue follow-up orders. No tick state is mutated (no `_tick_counter`
   bump, no portfolio mark-dirty, no bar render).

It also pushes a *pulse* display frame so the dashboard shows `💓 N s since last tick`
instead of freezing. See "Polling Cadence" below.

**Canonical clock (#360):** `get_current_time()` returns a loop-injected time — set from
the tick timestamp in `on_tick`, and from the wall-clock on the heartbeat. The loop owns
the between-tick time source, so the clock never freezes to the last tick. This is the one
place wall-clock is read in live (decision logic / workers only call `get_current_time()`,
§9). In sim the injected time is the simulated tick time (reproducible).

### Shutdown

Two modes:

| Mode | Trigger | Behavior |
|------|---------|----------|
| **Normal** | Tick source exhausted, SIGTERM | Finish orders per policy, collect full stats |
| **Emergency** | SIGINT (Ctrl+C), a startup or tick-loop exception, or an EMERGENCY session-end escalation (#348) | Same cleanup, best-effort stats |

Signal handling: first Ctrl+C requests shutdown; a second within 3s forces exit.

The mode is a **label**, not a behaviour: both run the same cleanup. Emergency flattening —
the case where liquidating IS right — belongs to the safety baseline (#356) and is
deliberately not folded in here, because one code path answering both "the session is over"
and "something went wrong" is exactly the confusion the policy below exists to end. Note also
that an operator Ctrl+C arrives as `emergency`, so a rule keyed on the mode would fire on
every manual stop.

### Session End (#492)

What the session does with resting orders and open positions when it ends is **two**
decisions, not one — and until #492 it did a third thing that was neither: it closed
positions in our book only, reporting an exit that never reached the venue.

```json
"session_end": { "orders": "cancel" | "leave", "positions": "close" | "leave" }
```

Defaults `orders: "cancel"` · `positions: "leave"`. `positions: "close"` is declared and
refuses at startup until #487 makes a real close resolvable.

**Full treatment — the order-type map, both pipelines, spot against margin, the incoherent
pair with cold start, and what the report shows: [session_end_policy.md](../architecture/session_end_policy.md).**

### Session outcome and exit code (#372)

`shutdown_mode` alone does not say whether the run failed — an operator Ctrl+C and a
safety-triggered escalation both arrive as `emergency`, and `emergency_reason` is empty for both.
`AutoTraderResult.operator_interrupted` (set only by the SIGINT handler) is what separates them,
and `get_outcome()` grades the session from there:

| Outcome | Exit | When |
|---|---|---|
| `SUCCESS` | `0` | normal shutdown, or an operator Ctrl+C — no errors logged |
| `CRASHED` | `1` | an uncaught exception reached the CLI |
| `FAILED` | `2` | `emergency` that the operator did not initiate — startup abort, tick-loop crash, safety escalation |
| `FINISHED_WITH_ERRORS` | `3` | the session ended without an emergency, but errors were logged |

Full taxonomy: [Warnings & Errors — Tier Taxonomy](../architecture/warnings_errors_tiers.md).

## State Persistence (#354)

Restart-safe algo memory (Category B): an algo's own internal state — counters, regime
flags, "already entered today", risk high-water-marks — snapshotted to disk and restored on
restart. Live-only; opt-in per algo via `AbstractDecisionLogic.uses_state_persistence()`; mock
auto-disabled. The store mirrors the Reconciler's optional-component shape (config gate +
`isinstance(LiveTradeExecutor)` + algo opt-in).

`AlgoStateStore` (`python/framework/persistence/algo_state_store.py`) writes atomic JSON
(temp file + `os.replace`) keyed by `<profile>_<symbol>` under `data/runtime/session_state/`
(stable across runs). Envelope: `{schema_version, saved_at_utc, profile, symbol, snapshot}`. The
store is decoupled — it knows only a JSON dict plus the bot identity; orchestration
(restore / snapshot / freshness gate) lives in `AutotraderMain`.

Lifecycle: restore runs after warmup and before the first decision; saves fire on a hybrid
cadence (every N ticks OR M seconds) from the tick loop — both the per-tick and the idle
heartbeat branch — plus a final save on shutdown. An empty snapshot writes no file; a
mid-session save failure is logged (error pot) but never aborts the session.

Two load-time policies. **Corrupt** (`on_corrupt`: `warn_reset` / `fail`) handles an unreadable
or wrong-schema file. **Stale** (`on_stale`: `warn_reset` / `halt`) handles a snapshot older than
`max_age_trading_days` — weekend-aware via the `MarketCalendar` (Forex skips weekends; crypto
counts calendar days). The coarse age guard runs first; an algo can refine it via
`accepts_restored_state(snapshot, ctx)`.

A pre-flight (the first member of the algo pre-flight check family,
`python/framework/validators/algo_state_preflight.py`) asserts the snapshot is JSON-serializable.
In live it runs at boot → hard `STARTUP FAILED`. In Simulation it runs centrally in the batch
`RequirementsCollector` (Phase 3, cached per distinct decision logic) → a non-serializable
snapshot marks the scenario invalid and excludes it before data loading, so a broken algo
surfaces once, not as N failed runs.

Authoring guide: `docs/user_guides/algo_state_persistence_guide.md`.
