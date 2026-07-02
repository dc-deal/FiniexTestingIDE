# AutoTrader Architecture

## Overview

FiniexAutoTrader is the live trading runner — the live equivalent of the backtesting `process_tick_loop`. It connects tick sources through workers and decision logic to the `LiveTradeExecutor`, using the same algorithm classes as backtesting.

**Design constraint:** Workers and DecisionLogic must not know they are running live. Same classes, same interfaces. Only the runner and executor change.

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

This breaks the design constraint: Workers and DecisionLogic must be **identical** classes in backtesting and live. The queue stops the infection — Thread 1 can use `await websocket.recv()` internally (#232), Thread 2 only sees synchronous `queue.get()`.

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

Institutional systems (Bloomberg, Refinitiv, FIX protocol) always separate Market Data Gateway from Order Gateway — different latency requirements, protocols, and failure modes. Retail platforms (MT5, cTrader) bundle them in the UI but separate them internally.

### How They Connect

The config maps each independently, `autotrader_startup.py` wires them together:

```json
{
  "broker_type": "kraken_spot",        // → KrakenAdapter (via BrokerConfigFactory)
  "tick_source": { "type": "kraken" }  // → KrakenTickSource (via setup_tick_source)
}
```

`broker_type` is intentionally broader than "adapter" — it selects the full broker configuration (fees, symbol specs, market type, leverage) through `BrokerConfigFactory` and `market_config.json`. The adapter is one part of that. `tick_source.type` maps directly to a `TickSource` class.

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

`trading_env/` is the **framework layer** — shared between backtesting and AutoTrader. `autotrader/` is the **application layer** — AutoTrader only. Tick sources live in `autotrader/` because they are exclusively a live concern. Moving them into `trading_env/adapters/` would leak live-only components into the shared framework.

## Pipeline Architecture

```
    ┌─────────────────────┐
    │  AutoTraderConfig    │  ← configs/autotrader_profiles/backtesting/mock_session_test.json
    └─────────┬───────────┘
              │
    ┌─────────▼───────────┐
    │ autotrader_startup   │  ← creates all pipeline objects
    │ setup_pipeline()     │     (mirrors process_startup_preparation)
    └─────────┬───────────┘
              │ creates
              ▼
    ╔═════════════════════════════════════════════════════════╗
    ║  RUNTIME                                                ║
    ║                                                         ║
    ║  Thread 1              Thread 2 (Main — Algo Loop)      ║
    ║  ┌────────────┐        ┌─────────────────────────────┐  ║
    ║  │ TickSource │  queue │ 1. executor.on_tick()       │  ║
    ║  │ (mock or   │───────►│ 2. bar_controller           │  ║
    ║  │  websocket)│ Queue  │ 3. workers → decision       │  ║
    ║  └────────────┘        │ 4. decision_logic → executor│  ║
    ║                        │ 5. clipping_monitor.record()│  ║
    ║                        └─────────────────────────────┘  ║
    ║                          │            │                 ║
    ║              ┌───────────┘            │                 ║
    ║              ▼                        ▼                 ║
    ║  ┌──────────────────┐   ┌──────────────────────┐        ║
    ║  │ LiveTradeExecutor│   │ ClippingMonitor      │        ║
    ║  │ + MockAdapter    │   │ (per-tick timing)    │        ║
    ║  └──────────────────┘   └──────────────────────┘        ║
    ╚═════════════════════════════════════════════════════════╝
```

## Session Lifecycle

### Startup

1. Load `AutoTraderConfig` from JSON
2. `setup_pipeline()` creates all objects (11 phases, mirrors backtesting)
3. `setup_tick_source()` starts tick source thread
4. Enter tick loop

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
| **Normal** | Tick source exhausted, SIGTERM | Close positions, cancel orders, collect full stats |
| **Emergency** | SIGINT (Ctrl+C) | Immediate close, best-effort stats |

Signal handling: First Ctrl+C → normal shutdown. Second Ctrl+C within 3s → force exit.

## Configuration

Config file: `configs/autotrader_profiles/backtesting/mock_session_test.json` — own format, NOT scenario-set based.

```json
{
  "name": "btcusd_mock",
  "symbol": "BTCUSD",
  "broker_type": "kraken_spot",
  "adapter_type": "mock",
  "strategy_config": { ... },
  "account": { "balances": { "USD": 10000.0, "BTC": 0.0 } },
  "tick_source": { "type": "mock", "parquet_path": "..." },
  "sentiment_source": { "type": "mock", "data_sentiment_type": "crypto_sentiment" },
  "display": { "enabled": false }
}
```

Sections not listed here (`execution`, `clipping_monitor`, `order_guard`) inherit their values from `app_config.json::autotrader` — only specify them in the profile when overriding a default.

| Section | Purpose | Notes |
|---------|---------|-------|
| `name` | Session name | Used for log directory (`logs/autotrader/<name>/`) |
| `symbol` | Trading pair | Single symbol per session |
| `broker_type` | Broker identifier | Maps to MarketType via `market_config.json`; broker connection settings read from there too |
| `adapter_type` | `mock` or `live` | Mock: no credentials needed |
| `dry_run` | `true` / `false` / omit | Optional per-profile override of the global `market_config` dry_run. Omit = inherit the broker default. Setting it (especially `false` = live) overrides the global default for this profile only and logs a loud override warning at startup |
| `strategy_config` | Workers + DecisionLogic | Same format as scenario sets |
| `account` | Asset balances | Spot: `"balances": {"USD": X, "ETH": Y}`. Live: overridden by API fetch (#230) |
| `tick_source` | Data source config | Mock: parquet replay. Live: WebSocket (#232) |
| `sentiment_source` | Sentiment feed for SIGNAL workers | Mock only (requires mock tick source). Omit = no feed; mandatory when the strategy has a SIGNAL worker. See "Sentiment Feed (Mock)" |
| `execution` | Runtime parameters | Inherits from `app_config.autotrader.execution`; override per profile if needed |
| `clipping_monitor` | Timing config | Inherits from `app_config.autotrader.clipping_monitor`; strategy: `queue_all` or `drop_stale` |
| `display` | Dashboard config | Inherits `enabled: true`, `update_interval_ms: 300` — test profiles set `enabled: false` |
| `order_guard` | Pre-validation guard | Inherits from `app_config.autotrader.order_guard`; override per profile if needed |
| `safety` | Circuit breaker | Always profile-specific. Omit or set `enabled: false` to disable |

**Config cascade (2-level):**
```
configs/app_config.json::autotrader  ← Level 1 (global defaults for all sessions)
  ↓ deep_merge (profile wins)
autotrader_profiles/*.json           ← Level 2 (session-specific overrides)
```
`user_configs/app_config.json` can override the `autotrader` block too — same mechanism as all other app_config sections.

## Tick Source Abstraction

`AbstractTickSource` defines the interface. Implementations:

| Source | Status | Description |
|--------|--------|-------------|
| `MockTickSource` | ✅ Built | Parquet replay — ticks as fast as possible, optional `tick_delay_ms` for visual debugging |
| `KrakenTickSource` | ✅ Built (#232) | Kraken WS v2 trade channel, auto-reconnect |

### KrakenTickSource (#232)

Live tick stream from the Kraken WebSocket v2 trade channel. Runs `asyncio.run()` in a daemon thread (Threading model 8.a), pushes `TickData` to `queue.Queue`.

**Key features:**
- Endless reconnect with exponential backoff (1s → 60s cap)
- Connection-liveness monitoring: checks message silence every 30s, forces reconnect after 90s silence
- SSL via certifi (cross-platform: Linux Docker + Windows server)
- Single symbol per session (matches bot architecture)
- Concurrent asyncio tasks: `_receive_loop` + `_connection_monitor` via `asyncio.wait(FIRST_COMPLETED)`

**Data Consistency Principle:** KrakenTickSource uses the **same trade channel** as DataCollector, ensuring backtesting data matches live data format. `bid=ask=trade_price` (spread=0) — crypto fees are handled by `MakerTakerFee`, not by spread.

```
DataCollector            AutoTrader (live)
┌──────────┐            ┌──────────┐
│ Kraken   │            │ Kraken   │
│ WS v2    │            │ WS v2    │
│ trade ch │            │ trade ch │  ← Same channel, same data
└────┬─────┘            └────┬─────┘
     │                       │
     ▼                       ▼
JSON → Parquet           Queue → Algo
```

**Symbol mapping:** WS pair is derived from `SymbolSpec.base_currency`/`quote_currency` (e.g., `BTCUSD` → `BTC/USD`, `DASHUSD` → `DASH/USD`).

**Config** (all fields optional, defaults in `TickSourceConfig`):
```json
{
  "tick_source": {
    "type": "kraken",
    "ws_url": "wss://ws.kraken.com/v2",
    "reconnect_initial_delay_s": 1.0,
    "reconnect_max_delay_s": 60.0,
    "connection_check_interval_s": 30.0,
    "connection_dead_s": 90.0
  }
}
```

Minimal config (all defaults): `{"tick_source": {"type": "kraken"}}`.

## Sentiment Feed (Mock)

A profile whose strategy contains a SIGNAL worker (e.g. `CORE/llm_sentiment`) needs a
`sentiment_source` block — the sentiment analogue of `tick_source`:

```json
"sentiment_source": {
  "type": "mock",
  "data_sentiment_type": "crypto_sentiment"
}
```

Unlike ticks, sentiment does **not** drive the loop — it is passive lookup data. At startup
(`setup_sentiment_feed`, phase 6b in `setup_pipeline`) the archive is resolved via the signal
index against the mock tick parquet's time range, loaded through the shared projected reader,
and injected as a `SignalDataProvider` into each SIGNAL worker. On every tick pass the worker
resolves the newest snapshot with `collected_msc ≤ tick.timestamp` (as-of lookup, no second
thread or queue). Misconfiguration (SIGNAL worker without a feed, non-mock tick source, no
archive overlap) aborts at startup — never at the first tick. Details, validation matrix, and
the deliberate-outage override: signal data source doc (`docs/data_pipeline/signal_data_source.md`).

Real-time/live sentiment is a future event-path feature — `type: mock` is the only supported
value today.

**Live dashboard:** the ALGO STATE panel shows the feed (`📡 Feed: <label>`, flagged
`[STALE]` in yellow when the SIGNAL worker reports staleness) plus the worker's
`display=True` outputs (`sentiment`, `conf`, `signal`, `stale`).

## Live Console UI (#228)

Real-time dashboard rendered via `rich.live` in a dedicated display thread. Receives `AutoTraderDisplayStats` snapshots from the tick loop via `queue.Queue`, drains and renders every `update_interval_ms` (default 300ms).

### Layout — Responsive

Three layouts based on terminal width:

| Width | Layout | Panels shown |
|-------|--------|-------------|
| ≥ 160 cols | 3-column | All panels |
| ≥ 120 cols | 2-column | Session, Portfolio, Algo State, Connection, Positions, Orders, Trade History |
| < 120 cols | Single-column | Session, Portfolio, Positions, Orders only |

### Panels

| Panel | Section | Content |
|-------|---------|---------|
| SESSION | Left | Uptime, status, tick rate (`X.X/min (N total)`), trade count + win rate, mode |
| CONNECTION | Left | Stream health (WS message age), Last Tick (actual trade tick age), reconnect count, emitted tick rate |
| WORKER PERFORMANCE | Left | Per-worker avg processing time with bar chart (scale: 50ms = full bar). Hidden completely when `execution.performance_tracking.worker_decision_tracking` is `false` — see [performance_tracking_layers.md](../architecture/performance_tracking_layers.md) |
| PORTFOLIO | Center | Balance with quote equivalent (`≈ X.XX USD`), net P&L, W/L count |
| TICK PROCESSING | Center | Avg/max processing ms, p50/p95/p99 percentiles, clipping bar + ratio, queue depth |
| OPEN POSITIONS | Right | Live positions: ID, direction, lots, entry price, unrealized P&L |
| ORDERS | Right | Active limit/stop orders + pipeline (in-transit) count |
| TRADE HISTORY | Right | Last 8 completed trades (newest first): dir, lots, entry, exit, P&L, close reason |
| ALGO STATE | Right | Decision + confidence, config params with `display=True` (e.g., `rsi_os=30 env_l=0.30`), worker `display=True` outputs (e.g., `rsi`, `up`/`lo`) |

### Connection Panel — Two Distinct Clocks

`Stream` and `Last Tick` measure different things intentionally:

- **Stream** (`● connected / stale / dead`): based on `_last_message_time` — any WS message including Kraken heartbeats. Reflects connection health. Goes stale after 30s silence, dead after 90s.
- **Last Tick**: based on `_last_tick_time` — only actual trade messages that produced `TickData`. Can be minutes old in quiet markets while `Stream` stays green.

Both are polled directly from `AbstractTickSource` (GIL-safe primitive reads) — no queue transport needed.

### Display Stats Transport

After each tick, `autotrader_tick_loop._build_display_stats()` builds an `AutoTraderDisplayStats` snapshot and pushes it to the display queue (`put_nowait` — dropped if full, display uses last known state). The snapshot contains only primitives, lists, and dataclasses — safe for queue transport and future JSON serialization.

At shutdown, the tick loop drains stale snapshots from the queue and pushes one **final** snapshot via blocking `put(timeout=1.0)`. This guarantees the display shows the terminal pipeline state (post-close balances, final trade count) regardless of refresh timing. The display thread performs its own final drain after the loop exits, then renders the last frame before the `Live` context closes.

Symbol currencies (`base_currency`, `quote_currency`) are resolved once at startup via `SymbolSpec` from the broker adapter and passed through the display stats — no string-splitting heuristic.

In spot mode, `AutoTraderDisplayStats` carries `equity` (total portfolio value in account currency) and `spot_balances` (per-currency holdings). The PORTFOLIO panel branches on `trading_model` to render a dual-balance layout (spot) or the standard balance view (margin).

```
Tick Loop (Thread 2)                Display Thread (Thread 3)
────────────────────                ──────────────────────────
_build_display_stats()              while running:
  → AutoTraderDisplayStats            drain queue (up to 100)
  → display_q.put_nowait() ──────→    render(latest_stats)
                                      live.update(panel)
                                      sleep(update_interval_ms / 1000)
                                    ─── loop exits (_running=False) ───
on shutdown:                        final drain (remaining queue items)
  drain stale, push final ─────→   live.update(final render)
                                    Live context exits (static output)
```

### Config

```json
"display": {
  "enabled": true,
  "update_interval_ms": 300
}
```

| Key | Default | Description |
|-----|---------|-------------|
| `enabled` | `true` | Enable/disable the dashboard |
| `update_interval_ms` | `300` | Display refresh interval in milliseconds |

## Clipping Monitor (#197)

**Core question:** Can the algo process ticks fast enough, or is it falling behind the market?

Clipping occurs when tick processing time exceeds the inter-tick arrival interval — the next tick arrives before the current one is finished.

```
Tick N arrives        Tick N+1 arrives
    │                     │
    ├── processing_ms ────┤
    │                     │
    ├── tick_delta_ms ──► │
    │                     │
    If processing_ms > tick_delta_ms → CLIPPED (stale by the difference)
```

### Metrics

| Metric | What it measures | Why it matters |
|--------|-----------------|----------------|
| `processing_ms` | Algo time per tick (Workers + Decision + Execution) | Baseline — how fast are we? |
| `tick_delta_ms` | Market-side interval between consecutive ticks | How much time did we have? |
| `stale_ms` | Overshoot: `processing_ms - tick_delta_ms` | Severity — 1ms late vs. 500ms late |
| `queue_depth` | Ticks waiting in queue (`queue.qsize()`) | Growing queue = falling behind permanently |
| `clipping_ratio` | Fraction of clipped ticks over session | Overall health: 0.1% = fine, 30% = problem |

All metrics are tracked in two scopes: **session totals** (end-of-session summary) and **interval** (periodic report every N seconds, then reset). This shows *when* clipping occurs, not just *if*.

### Phases

| Phase | What | Status |
|-------|------|--------|
| 1 | Per-tick processing time (`perf_counter_ns`) | ✅ |
| 2 | Clipping detection (processing > tick delta) | ✅ |
| 3 | Counters (ticks_clipped, max_stale_ms, avg) | ✅ |
| 4 | Periodic reports (configurable interval) | ✅ |
| 5 | Queue depth monitoring (`queue.qsize()`) | ✅ |
| 6 | Strategy selection (queue_all / drop_stale) | ✅ Config, drop_stale execution in #232 |

## Safety Circuit Breaker

A soft-stop mechanism that blocks new position entries when configurable risk thresholds are exceeded. Existing open positions continue to run — SL, TP, and signal-based closes are not affected.

### Behavior

```
Tick rein
  → executor.on_tick()    ← SL/TP checks run (always, unaffected)
  → Workers → Decision    ← produces BUY / SELL / FLAT
  → [SAFETY CHECK]        ← evaluates thresholds against equity (spot) or balance (margin)
  → if blocked: decision.action = FLAT  ← override, no trade opened
  → execute_decision()
```

The check runs after every tick. If conditions clear (e.g. equity/balance recovers above threshold), the block is automatically lifted and trading resumes.

### Configuration

```json
"safety": {
  "enabled": true,
  "min_balance": 500.0,
  "min_equity": 9.0,
  "max_drawdown_pct": 20.0
}
```

| Field | Description |
|---|---|
| `enabled` | Master switch — omit or set `false` to disable entirely |
| `min_balance` | Block if `balance < min_balance` (margin mode, account currency) |
| `min_equity` | Block if `equity < min_equity` (spot mode, account currency) |
| `max_drawdown_pct` | Block if session drawdown > X%. Computed from balance (margin) or equity (spot) |

Both conditions are OR-combined — either alone triggers the block. Set to `0.0` to disable a specific condition while keeping the other active. Each mode uses its own min-threshold field (`min_balance` for margin, `min_equity` for spot).

### Display

SESSION panel shows safety state with mode indicator and headroom detail:

```
Safety:  ● ACTIVE  (spot)
         min_equity: 9.00 (now: 12.48)  |  dd: 0.1% / 20.0%
```

| Display | Meaning |
|---|---|
| `Safety: off` | Not configured (`enabled: false` or block omitted) |
| `Safety: ● ACTIVE (spot)` | Configured, spot mode, conditions not triggered |
| `Safety: ● ACTIVE (margin)` | Configured, margin mode, conditions not triggered |
| `Safety: ⛔ BLOCKED  min_equity (4.80 < 5.00)` | Triggered — reason shown inline |

Trigger and clear events are logged:
```
WARNING | ⛔ Safety circuit breaker triggered: min_equity (4.8000 < 5.0000)
INFO    | ✅ Safety circuit breaker cleared
```

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

## Acceptance Testing — Live Field Study (#332)

The Live Field Study is the live acceptance gate: an operator-driven, deterministic phase
sequence (`CORE/live_field_study/live_field_study`) that drives the full live pipeline
through every order type, modify/cancel path, rejection battery, partial close, and idle
heartbeat against real Kraken Spot at min-lot. It records the run as analysis-ready JSONL
(two planes — bot-observed via #348 + broker-truth via #151) and a post-run analyzer emits
a PASS/FAIL acceptance certificate (mirroring the benchmark / live-adapter certificates).

It reuses the existing `request_session_end` API (#348) for a clean exit, asserts the
account is flat before trading (`Reconciler.is_account_flat()`, #151), and self-aborts on
a budget (`max_session_cost_usd`) or wall-clock (`session_timeout_s`) breach.

Operator guide: [field_study_guide.md](../tests/live_field_study/field_study_guide.md).
The certificate is a release-gate item (see the Release Checklist).

## File Structure

```
python/framework/autotrader/
  autotrader_main.py             Runner: run(), shutdown, signal handling
  autotrader_tick_loop.py        Tick processing loop (main thread, hot path)
  autotrader_startup.py          Pipeline object creation (11 phases)
  autotrader_warmup_preparator.py  Warmup bar loading (mock: parquet, live: API)
  kraken_ohlc_bar_fetcher.py     Kraken OHLC bar fetch (public API, no auth)
  live_clipping_monitor.py       Per-tick timing, clipping detection (#197)
  reporting/
    autotrader_post_session_report.py   Console + file log summary
    autotrader_csv_file_report.py       Trade/order CSV export
  tick_sources/
    abstract_tick_source.py      AbstractTickSource ABC
    mock_tick_source.py          Parquet replay tick source
    kraken_tick_source.py        Kraken WS v2 live tick source (#232)
    kraken_tick_message_parser.py  WS JSON → TickData parser (#232)

python/configuration/autotrader/
  autotrader_config_loader.py          JSON → AutoTraderConfig
  abstract_broker_config_fetcher.py    ABC for live config fetchers
  kraken_config_fetcher.py             Kraken REST API fetch (symbol specs + balance)

python/framework/types/autotrader_types/
  autotrader_config_types.py     AutoTraderConfig, DisplayConfig, sub-configs
  autotrader_result_types.py     AutoTraderResult
  autotrader_display_types.py    AutoTraderDisplayStats, PositionSnapshot, TradeHistoryEntry (#228)
  clipping_monitor_types.py      ClippingReport, ClippingSessionSummary

python/system/ui/
  autotrader_live_display.py     Live console dashboard (#228, rich.live, responsive layout)

python/cli/
  autotrader_cli.py              CLI: run --config
  broker_config_cli.py           CLI: sync — fetch + cache broker configs for dynamic brokers

configs/autotrader_profiles/
  ethusd_live.json               Live trading config (ETHUSD, Kraken API)
  solusd_live.json               Live trading config (SOLUSD, Kraken API)
  dotusd_live.json               Live trading config (DOTUSD — no index data, data-independence proof)
  backtesting/
    mock_session_test.json       Full mock session test (BTCUSD parquet replay)
    trade_lifecycle_test.json  Trade lifecycle test (BTCUSD, 15K ticks)
    btcusd_mock_safety.json    Safety circuit breaker test (aggressive thresholds)

configs/credentials/
  kraken_credentials.json        Mock/default credentials (tracked)
```

## Usage

```bash
# CLI
python python/cli/autotrader_cli.py run --config configs/autotrader_profiles/backtesting/mock_session_test.json

# VS Code launch.json
# 🤖 AutoTrader: BTCUSD Mock
```

## Logging

Three `ScenarioLogger` instances per session, each with a distinct purpose:

| Logger | File | Purpose | Console |
|--------|------|---------|---------|
| Global | `autotrader_global.log` | Startup phases, shutdown, cross-cutting errors | Direct `print()` during startup |
| Session | `session_logs/autotrader_session_YYYYMMDD.log` | Per-tick processing, decisions, orders | Buffered (cleared before summary) |
| Summary | `autotrader_summary.log` | Post-session report, statistics | Flushed to console at end |

- Directory: `logs/autotrader/<name>/<session_timestamp>/`
- Separate from backtesting logs (`logs/scenario_sets/`)
- Session log **rotates daily** at midnight UTC — prevents unbounded file growth on 24/7 sessions

```
logs/autotrader/btcusd_mock/20260328_105127/
  autotrader_global.log           Startup, shutdown, errors
  autotrader_summary.log          Post-session summary
  session_logs/
    autotrader_session_20260328.log  Day 1 tick processing
    autotrader_session_20260329.log  Day 2 (if session spans midnight)
  events.csv                      Long-format trade-event stream — one row per
                                  event (ORDER_SUBMIT / CLOSE_SUBMIT / FILL /
                                  POSITION_OPEN / POSITION_CLOSE / ORDER_REJECT).
                                  See trade_execution_visibility.md for schema.
```

### Warning/Error Summary

At session end, warning and error counts from the session logger buffer are included in the post-session summary. This gives a quick health indicator without scrolling through session logs.

## Live Broker Config Acquisition (#230)

For `adapter_type='live'`, AutoTrader fetches broker config and account balance from the Kraken REST API at startup instead of relying solely on static JSON.

### Startup Flow (Live Mode)

```
create_broker_config(config, logger)   (autotrader_broker_config_setup.py)
  → config_mode=DYNAMIC (from market_config.json)
  → entry = MarketConfigManager().get_broker_entry(broker_type)
  → KrakenConfigFetcher(entry.credentials_file, entry.broker_transport.api_base_url)
  → fetch_broker_config_with_cache(symbol, broker_type)
       cache < 7 days old  → use silently, no API call
       cache 7–30 days     → try GET /0/public/AssetPairs; on failure: warn + use cache
       cache > 30 days     → try GET /0/public/AssetPairs; on failure: strong stale warning + use cache
       no cache at all     → GET /0/public/AssetPairs; on failure: hard error (first run)
  → POST /0/private/Balance → account balance (overrides profile balances)
  → BrokerConfigFactory.from_serialized_dict(config_dict)
  → adapter.enable_live(credentials_file, dry_run, transport)  ← Tier 3 activation
  → return BrokerConfig with live-enabled KrakenAdapter
```

**Cache location:** `data/runtime/brokers/<broker_type>/` (gitignored, auto-refreshed weekly).  
**Static seed:** `configs/brokers/kraken/kraken_spot_broker_config.json` — git-tracked, used by `config_mode=static` brokers and backtesting. Never auto-overwritten.  
**Balance fetch failure** is **fatal** — a 0.0 balance in live mode is dangerous.

**Mock mode**: Completely unchanged. No API calls, no credentials needed, `enable_live()` never called.

### Account Currency & Balance Semantics

The `account.balances` dict in the AutoTrader profile determines which currencies are fetched from Kraken and how P&L is denominated internally. The account currency is derived at startup from the balances keys matched against the symbol's base/quote currencies (quote currency preferred). An optional `account_currency` override allows explicit control.

**Rules:**
- At least one key in `account.balances` must match either the **base** or **quote** currency of the traded symbol.
- All currencies listed in `account.balances` are fetched from Kraken at startup — profile values are placeholders.
- Cross-currency accounts (e.g., `balances: {"EUR": 100}` with `SOLUSD`) are not supported and raise a `NotImplementedError` at startup.

**Account currency derivation (in order):**
1. Explicit `account.account_currency` if set → used as-is
2. Quote currency of symbol if present in balances → e.g., USD for ETHUSD
3. Base currency of symbol if present in balances → e.g., ETH for ETHUSD
4. First key in balances (fallback)

**Supported configurations for Spot trading:**

| `account.balances` | `account_currency` | Symbol | Meaning |
|---|---|---|---|
| `{"USD": 100}` | (omitted) | `SOLUSD` | P&L in USD — recommended for multi-pair setups |
| `{"SOL": 0, "USD": 100}` | (omitted) | `SOLUSD` | Dual-balance, P&L in USD (quote, default) |
| `{"ETH": 0, "USD": 50}` | `"ETH"` | `ETHUSD` | Dual-balance, P&L in ETH (explicit override) |

**Recommendation:** Use `"USD"` as account currency for all spot pairs. USD is the quote currency across all Kraken USD pairs — one balance covers all symbols, P&L is always in USD (consistent with backtesting), and no per-symbol currency management is needed. Use `account_currency` override only when explicitly needed (e.g., testing P&L in base currency).

**What happens after trades:** If a BUY fills, the base asset increases and quote decreases (and vice versa for SELL). The AutoTrader only tracks the configured currency — the other side accumulates silently on the Kraken account. This is expected Spot behavior. The Reconciliation Layer (#151) will address cross-session position awareness.

### Broker Connection Settings

Broker-specific live settings are stored in `market_config.json` alongside the broker entry — not in the AutoTrader profile:

```
Profile (ethusd_live.json)           ← Algorithm config (strategy, workers, symbol)
  "broker_type": "kraken_spot"
        |
market_config.json → kraken_spot     ← Broker connection config
  "credentials_file", "dry_run", "broker_transport.{api_base_url, rate_limit_interval_s, ...}"
        |
Credentials (kraken_credentials.json) ← Only API keys
```

To override connection settings (e.g., `dry_run: false` for live trading), create `user_configs/market_config.json` with the changed fields. See [Kraken Adapter Setup Guide](../user_guides/adapter/setup_kraken_adapter.md) for full configuration details.

### Credentials Cascade

Credentials follow the project-wide `configs/` → `user_configs/` override pattern:

1. `user_configs/credentials/kraken_credentials.json` — user override (gitignored, real keys)
2. `configs/credentials/kraken_credentials.json` — tracked default (mock values)

The `credentials_file` in broker settings is just the filename (e.g., `"kraken_credentials.json"`). The cascade is resolved automatically.

### API Authentication

Private Kraken endpoints use HMAC-SHA512 signing: `API-Sign = base64(HMAC-SHA512(url_path + SHA256(nonce + post_data), base64_decode(api_secret)))`. The `nonce` is an increasing integer (millisecond timestamp).

### Fee Handling

Fees are **hardcoded** at the default Kraken tier (maker 0.16%, taker 0.26%) rather than fetched from the API. Kraken fee tiers depend on 30-day rolling trading volume, which changes constantly. Static defaults are safer for risk management.

## KrakenAdapter Tier 3 — Live Order Execution (#133 Step 3)

Tier 3 adds real Kraken REST API order execution to `KrakenAdapter`. Methods are activated by calling `enable_live(credentials_file, dry_run, transport)` — without it, the adapter works in Tier 1+2 mode (backtesting only). `transport` is a `BrokerTransportConfig` (api_base_url, rate_limit_interval_s, request_timeout_s, poll_interval_ms).

### Adapter Tiers

| Tier | Scope | Requires Credentials | Used By |
|------|-------|---------------------|---------|
| 1 | Config validation, broker/symbol specs | No | Backtesting + AutoTrader |
| 2 | Order creation (MarketOrder, LimitOrder, etc.) | No | Backtesting + AutoTrader |
| 3 | Live execution (AddOrder, QueryOrders, CancelOrder, AmendOrder) | Yes | AutoTrader (live mode) |

### Tier 3 API Mapping

| Method | Kraken Endpoint | Key Parameters |
|--------|----------------|----------------|
| `execute_order()` | `POST /0/private/AddOrder` | pair, type, ordertype, volume, price, validate |
| `check_order_status()` | `POST /0/private/QueryOrders` | txid |
| `cancel_order()` | `POST /0/private/CancelOrder` | txid |
| `modify_order()` | `POST /0/private/AmendOrder` | txid, limit_price |

### Dry-Run Mode

Kraken Spot has no testnet/sandbox. Dry-run uses Kraken's native `validate=true` parameter on AddOrder — Kraken validates the order (pair, volume, balance, permissions) but **does not execute it**.

Controlled by `dry_run` in `market_config.json` for the broker type (default: `true` — safe by default). Override in `user_configs/market_config.json` to go live. Console shows `Mode: DRY RUN (validate only)` or `Mode: LIVE TRADING` at startup.

Dry-run behavior:
- `execute_order()`: sends `validate=true`, returns synthetic `DRYRUN-NNNNNN` broker_ref
- `check_order_status()` / `cancel_order()` / `modify_order()`: return synthetic responses (order doesn't exist at broker)

### AmendOrder — In-Place Modify

Kraken's `AmendOrder` amends the order **in place** — the txid (and any client order id) stay the same, so there is no cancel-replace and no broker_ref swap. `_parse_modify_response` returns the unchanged `broker_ref`; the response carries an `amend_id` for auditing. The `update_broker_ref(old, new)` swap path remains as a defensive net for brokers that *do* return a new ref on modify, but it is not exercised by Kraken.

### Rate Limiting

Configurable via `broker_transport.rate_limit_interval_s` in broker settings (default: 1.0s). Simple time-based throttle — minimum interval between private API calls. Conservative but safe for personal use.

Enforced inside the adapter's `_enforce_rate_limit()` (called from every private HTTP call). Because all broker I/O is funneled through a single worker thread, this gate also serializes async polling against submits/edits/cancels — no risk of two private API calls landing under the rate window.

### Polling Cadence (#320)

Active LIMIT orders are polled asynchronously through the same worker-thread pattern as submit/edit/cancel/trades_query. `LiveTradeExecutor._process_active_orders` is a non-blocking scheduler: for each `_active_limit_orders` entry it either skips (no broker_ref yet, in-flight query, or inside throttle window) or enqueues a `QueryJob` to the worker. The response is consumed on the main thread via `drain_inbox` → `_handle_query_response`.

The scheduler runs on the tick path (`on_tick`) **and** on the idle heartbeat (`heartbeat()`, #360) — so the fill/cancel-confirm query fires during a quiet stretch too, not only when a real tick arrives. The per-order throttle (`poll_interval_ms`) still gates the actual broker I/O, so a faster heartbeat does not multiply API calls.

Three gates on the scheduler, all silent skips:

| Gate | Reason |
|------|--------|
| `broker_ref is None` | Submit still in flight at the broker — wait for `_handle_limit_submit_response` |
| `pending.in_flight_query is True` | A previous QueryJob has not returned yet |
| `now_ms - pending.last_polled_at_ms < poll_interval_ms` | Inside the per-order throttle window |

Pathological "stuck in-flight" cases (worker dead, network hung) are caught by the existing `check_timeouts()` mechanism — when `pending.timeout_at` passes, the order is rejected via `_handle_timeout`.

`_handle_query_response` ALWAYS clears `pending.in_flight_query` (the query is resolved either way), then applies a stale-broker_ref guard before any state mutation. The guard was built for the legacy EditOrder flip (a QueryJob dispatched before the swap returned the OLD ref while `pending.broker_ref` already held the NEW one). With in-place `AmendOrder` the txid is stable across a modify, so the guard no longer fires in normal Kraken flow; it stays as a defensive net (e.g. brokers that cancel-replace). State mutations are skipped on stale; the next throttle cycle fires a fresh QueryJob against the current ref.

`poll_interval_ms` is per-broker via `BrokerTransportConfig` (default 5000 ms). Tuning guidance: 5000 ms (default — Kraken-friendly), 1000 ms (scalping), 500 ms (only with rate-limit headroom verified). MARKET-order polling in `_process_pending_orders` stays sync — low frequency, no rate pressure.

### Drift Audit (#327)

After every EXECUTED outcome the `DriftAuditor` (wired in `autotrader_main.py` when `drift_audit.enabled=True`) captures a snapshot of the synthetic state (`pending.cumulative_fee` / `cumulative_avg_price` / `cumulative_filled_lots`) and fires a one-shot `submit_trades_query_async()` against the broker. When the per-execution `TradesQueryResponse` arrives via `drain_inbox`, the executor's `_handle_trades_response` fan-outs to all registered `_trades_response_consumers` — including DriftAuditor — which then compares snapshot vs. broker truth across FEE / VOLUME / PRICE dimensions, logs drift events above their thresholds, and surfaces counters in the SESSION panel `Audit:` line.

Strict read-only — no state mutation, no portfolio adjustment. Correction is deferred to the future Reconciliation Layer (#151). Detailed architecture: [architecture/drift_audit.md](../architecture/drift_audit.md).

The listener signature `add_order_outcome_listener(callback)` was extended to `Callable[[OrderDirection, OrderResult, Optional[PendingOrder]], None]` to give consumers the pending reference at outcome time. OrderGuard's adapter accepts the new arg and ignores it. Pre-submit rejections (no PendingOrder yet) pass `pending=None` explicitly at the single relevant call site (`_record_async_rejection` in `live_trade_executor.py`).

### Symbol Mapping

Standard symbols (e.g., `BTCUSD`) are mapped to Kraken pair names (e.g., `XBTUSD`) for order API calls via the `kraken_pair_name` field in the broker config JSON (static seed or runtime cache). If the field is absent, the symbol key is used as-is.

## Live Warmup (#231)

Workers need warmup bars before producing meaningful signals. Without warmup, a worker with `{"M5": 14}` needs 70 minutes of live ticks before its first valid RSI. The warmup system pre-loads historical bars at startup.

### Two Paths

| Aspect | Mock (parquet) | Live (API) |
|--------|----------------|------------|
| **Source** | Pre-rendered bar parquet via `BarsIndexManager` | Kraken `GET /0/public/OHLC` |
| **Reference time** | First tick timestamp from parquet file | `datetime.now(UTC)` |
| **Network** | No | Yes (public, no auth) |
| **Extensibility** | Static data | ABC pattern → MT5 (#209) |

### Flow

```
Phase 9 in setup_pipeline():

  1. calculate_scenario_requirements(workers)
     → warmup_by_timeframe = {"M5": 20, "M30": 20}

  2. Reference timestamp:
     Mock: first tick from parquet → 2026-01-24T14:19:46Z
     Live: now()

  3. Load bars:
     Mock: BarsIndexManager → parquet → filter before ref_ts → tail(count)
     Live: KrakenOhlcBarFetcher → GET /0/public/OHLC → Bar objects

  4. Validate: warn if fewer bars than required

  5. bar_renderer.initialize_historical_bars() per timeframe
     → Workers have full history from tick 1
```

### Direct Injection

AutoTrader is single-process. Backtesting uses `inject_warmup_bars()` with bar dicts for subprocess transport (pickle, CoW). AutoTrader bypasses this — creates `Bar` objects directly and calls `bar_renderer.initialize_historical_bars()`. No serialization round-trip.

### Kraken OHLC API

```
GET /0/public/OHLC?pair=XBTUSD&interval=5&since=<unix_ts>
→ [[time, open, high, low, close, vwap, volume, count], ...]
```

Public endpoint, no auth. Intervals: 1 (M1), 5 (M5), 15 (M15), 30 (M30), 60 (H1), 240 (H4), 1440 (D1). Returns up to 720 bars. Last bar is in-progress (dropped).

### Data Independence

AutoTrader live sessions are **fully decoupled from backtesting data**. The tick/bar index (`BarsIndexManager`, `TickIndexManager`) is never accessed during live operation:

- **Tick data:** Comes from WebSocket (live) or parquet replay (mock) — not from the tick index
- **Warmup bars:** Fetched from broker REST API (live) or pre-rendered parquet (mock)
- **Symbol specs:** Loaded from broker config (`configs/brokers/`), not from imported data

This means a broker/symbol can run live **without any backtesting data in the index**. The only requirement is a broker entry in `market_config.json` and a matching OHLC bar fetcher for warmup.

## Roadmap

| Step | Issue | Description | Status |
|------|-------|-------------|--------|
| 1a-α | #229 | Skeleton + Mock Pipeline | ✅ |
| 1a-β | #230 | Live Broker Config (Kraken API) | ✅ |
| 1b | #231 | Live Warmup (KrakenOhlcBarFetcher) | ✅ |
| 3 | #133 | KrakenAdapter Tier 3 (execution, dry-run, broker settings) | ✅ |
| 4 | #133 | Active Order Lifecycle Lifting | ✅ |
| 2 | #232 | Kraken Tick Source (WebSocket v2) | ✅ |
| — | #228 | Live Console UI (rich.live, responsive layout) | ✅ |
| — | #252 | Broker Config Dual-Use Separation (static seed + runtime cache, config_mode, hash ID) | ✅ |
