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
    │  AutoTraderConfig    │  ← configs/autotrader_profiles/backtesting/btcusd_mock.json
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

### Shutdown

Two modes:

| Mode | Trigger | Behavior |
|------|---------|----------|
| **Normal** | Tick source exhausted, SIGTERM | Close positions, cancel orders, collect full stats |
| **Emergency** | SIGINT (Ctrl+C) | Immediate close, best-effort stats |

Signal handling: First Ctrl+C → normal shutdown. Second Ctrl+C within 3s → force exit.

## Configuration

Config file: `configs/autotrader_profiles/backtesting/btcusd_mock.json` — own format, NOT scenario-set based.

```json
{
  "name": "btcusd_mock",
  "symbol": "BTCUSD",
  "broker_type": "kraken_spot",
  "broker_config_path": "configs/brokers/kraken/kraken_spot_broker_config.json",
  "adapter_type": "mock",
  "broker_settings": "kraken_spot.json",
  "strategy_config": { ... },
  "account": { "balances": { "USD": 10000.0, "BTC": 0.0 } },
  "tick_source": { "type": "mock", "parquet_path": "...", "mode": "replay" },
  "execution": { "parallel_workers": false, "bar_max_history": 1000 },
  "clipping_monitor": { "report_interval_s": 60.0, "strategy": "queue_all" }
}
```

| Section | Purpose | Notes |
|---------|---------|-------|
| `name` | Session name | Used for log directory (`logs/autotrader/<name>/`) |
| `symbol` | Trading pair | Single symbol per session |
| `broker_type` | Broker identifier | Maps to MarketType via `market_config.json` |
| `broker_config_path` | Broker JSON | Fees, symbol specs, leverage |
| `adapter_type` | `mock` or `live` | Mock: no credentials needed |
| `broker_settings` | Broker settings filename | Cascade: `user_configs/broker_settings/` → `configs/broker_settings/` |
| `strategy_config` | Workers + DecisionLogic | Same format as scenario sets |
| `account` | Asset balances | Spot: `"balances": {"USD": X, "ETH": Y}`. Live: overridden by API fetch (#230) |
| `tick_source` | Data source config | Mock: parquet replay. Live: WebSocket (#232) |
| `execution` | Runtime parameters | Standalone — no cascade from app_config |
| `clipping_monitor` | Timing config | Strategy: `queue_all` or `drop_stale` |
| `display` | Dashboard config | `enabled`, `update_interval_ms` (default 300ms) |
| `safety` | Circuit breaker | Optional. Omit or set `enabled: false` to disable |

**No config cascade.** Unlike backtesting (app_config → scenario_set → scenario), AutoTrader uses a flat, standalone config. One session, one config.

## Tick Source Abstraction

`AbstractTickSource` defines the interface. Implementations:

| Source | Status | Description |
|--------|--------|-------------|
| `MockTickSource` | ✅ Built | Parquet replay (replay / realtime modes) |
| `KrakenTickSource` | ✅ Built (#232) | Kraken WS v2 trade channel, auto-reconnect |

MockTickSource modes:
- **replay** (default): Ticks as fast as possible — functional testing
- **realtime**: `time.sleep(delta)` between ticks — clipping behavior testing

### KrakenTickSource (#232)

Live tick stream from the Kraken WebSocket v2 trade channel. Runs `asyncio.run()` in a daemon thread (Threading model 8.a), pushes `TickData` to `queue.Queue`.

**Key features:**
- Endless reconnect with exponential backoff (1s → 60s cap)
- Heartbeat monitoring: checks message silence every 30s, forces reconnect after 90s silence
- SSL via certifi (cross-platform: Linux Docker + Windows server)
- Single symbol per session (matches bot architecture)
- Concurrent asyncio tasks: `_receive_loop` + `_heartbeat_monitor` via `asyncio.wait(FIRST_COMPLETED)`

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

**Symbol mapping:** `symbol_to_ws_pair` in broker settings (`kraken_spot.json`) maps internal symbols to Kraken WS format (e.g., `BTCUSD` → `BTC/USD`). Fallback: slash-insert at position 3.

**Config** (all fields optional, defaults in `TickSourceConfig`):
```json
{
  "tick_source": {
    "type": "kraken",
    "ws_url": "wss://ws.kraken.com/v2",
    "reconnect_initial_delay_s": 1.0,
    "reconnect_max_delay_s": 60.0,
    "heartbeat_interval_s": 30.0,
    "heartbeat_dead_s": 90.0
  }
}
```

Minimal config (all defaults): `{"tick_source": {"type": "kraken"}}`.

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
| WORKER PERFORMANCE | Left | Per-worker avg processing time with bar chart (scale: 50ms = full bar) |
| PORTFOLIO | Center | Balance with quote equivalent (`≈ X.XX USD`), net P&L, W/L count |
| TICK PROCESSING | Center | Avg/max processing ms, p50/p95/p99 percentiles, clipping bar + ratio, queue depth |
| OPEN POSITIONS | Right | Live positions: ID, direction, lots, entry price, unrealized P&L |
| ORDERS | Right | Active limit/stop orders + pipeline (in-transit) count |
| TRADE HISTORY | Right | Last 8 completed trades (newest first): dir, lots, entry, exit, P&L, close reason |
| ALGO STATE | Right | Worker `display=True` outputs (e.g., `rsi_value`, `upper`/`lower`), last decision + confidence |

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
  → [SAFETY CHECK]        ← evaluates thresholds against current balance
  → if blocked: decision.action = FLAT  ← override, no trade opened
  → execute_decision()
```

The check runs after every tick. If conditions clear (e.g. balance recovers above `min_balance`), the block is automatically lifted and trading resumes.

### Configuration

```json
"safety": {
  "enabled": true,
  "min_balance": 9.0,
  "max_drawdown_pct": 20.0
}
```

| Field | Description |
|---|---|
| `enabled` | Master switch — omit or set `false` to disable entirely |
| `min_balance` | Block if `current_balance < min_balance` (account currency) |
| `max_drawdown_pct` | Block if session loss > X% of `initial_balance` |

Both conditions are OR-combined — either alone triggers the block. Set to `0.0` to disable a specific condition while keeping the other active.

### Display

SESSION panel shows safety state:

| Display | Meaning |
|---|---|
| `Safety: off` | Not configured (`enabled: false` or block omitted) |
| `Safety: ● ACTIVE` | Configured and conditions not triggered |
| `Safety: ⛔ BLOCKED  min_balance (12.29 < 15.00)` | Triggered — reason shown inline |

Trigger and clear events are logged:
```
WARNING | ⛔ Safety circuit breaker triggered: min_balance (12.2930 < 15.0000)
INFO    | ✅ Safety circuit breaker cleared
```

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

configs/autotrader_profiles/
  ethusd_live.json               Live trading config (ETHUSD, Kraken API)
  solusd_live.json               Live trading config (SOLUSD, Kraken API)
  dotusd_live.json               Live trading config (DOTUSD — no index data, data-independence proof)
  backtesting/
    btcusd_mock.json             Default mock config (BTCUSD parquet replay)

configs/broker_settings/
  kraken_spot.json               Tracked defaults (dry_run, API URL, credentials ref)

configs/credentials/
  kraken_credentials.json        Mock/default credentials (tracked)
```

## Usage

```bash
# CLI
python python/cli/autotrader_cli.py run --config configs/autotrader_profiles/backtesting/btcusd_mock.json

# VS Code launch.json
# 🤖 AutoTrader: BTCUSD Mock (replay)
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
  autotrader_trades.csv           Completed trades (P&L, fees)
  autotrader_orders.csv           All order results (fills, rejections)
```

### Warning/Error Summary

At session end, warning and error counts from the session logger buffer are included in the post-session summary. This gives a quick health indicator without scrolling through session logs.

## Live Broker Config Acquisition (#230)

For `adapter_type='live'`, AutoTrader fetches broker config and account balance from the Kraken REST API at startup instead of relying solely on static JSON.

### Startup Flow (Live Mode)

```
_create_broker_config(config, logger)
  → adapter_type='live' detected
  → _load_broker_settings('kraken_spot.json')  ← cascade: user_configs/ → configs/
  → KrakenConfigFetcher(credentials_file, api_base_url)
  → GET /0/public/AssetPairs → symbol specs (tick_size, volume_min/max, digits)
  → POST /0/private/Balance → account balance (overrides profile balances)
  → BrokerConfigFactory.from_serialized_dict(config_dict)
  → adapter.enable_live(broker_settings)  ← Tier 3 activation
  → return BrokerConfig with live-enabled KrakenAdapter
```

**Fallback**: If the public API call fails, symbol specs fall back to the static JSON (`broker_config_path`). Balance fetch failure is **fatal** — a 0.0 balance in live mode is dangerous.

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

### Broker Settings Layer

Broker-specific live settings are separated from algorithm config (AutoTrader profile) and credentials:

```
Profile (ethusd_live.json)          ← Algorithm config (strategy, workers, symbol)
  "broker_settings": "kraken_spot.json"
        |
Broker Settings (kraken_spot.json)  ← Broker-specific live config
  "credentials_file", "dry_run", "api_base_url", "rate_limit_interval_s"
        |
Credentials (kraken_credentials.json) ← Only API keys
```

**Cascade:** `user_configs/broker_settings/` → `configs/broker_settings/` (same pattern as credentials). See [Kraken Adapter Setup Guide](../user_guides/adapter/setup_kraken_adapter.md) for full configuration details.

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

Tier 3 adds real Kraken REST API order execution to `KrakenAdapter`. Methods are activated by calling `enable_live(broker_settings)` — without it, the adapter works in Tier 1+2 mode (backtesting only).

### Adapter Tiers

| Tier | Scope | Requires Credentials | Used By |
|------|-------|---------------------|---------|
| 1 | Config validation, broker/symbol specs | No | Backtesting + AutoTrader |
| 2 | Order creation (MarketOrder, LimitOrder, etc.) | No | Backtesting + AutoTrader |
| 3 | Live execution (AddOrder, QueryOrders, CancelOrder, EditOrder) | Yes | AutoTrader (live mode) |

### Tier 3 API Mapping

| Method | Kraken Endpoint | Key Parameters |
|--------|----------------|----------------|
| `execute_order()` | `POST /0/private/AddOrder` | pair, type, ordertype, volume, price, validate |
| `check_order_status()` | `POST /0/private/QueryOrders` | txid |
| `cancel_order()` | `POST /0/private/CancelOrder` | txid |
| `modify_order()` | `POST /0/private/EditOrder` | txid, price |

### Dry-Run Mode

Kraken Spot has no testnet/sandbox. Dry-run uses Kraken's native `validate=true` parameter on AddOrder — Kraken validates the order (pair, volume, balance, permissions) but **does not execute it**.

Controlled by `dry_run` in broker settings (default: `true` — safe by default). Console shows `Mode: DRY RUN (validate only)` or `Mode: LIVE TRADING` at startup.

Dry-run behavior:
- `execute_order()`: sends `validate=true`, returns synthetic `DRYRUN-NNNNNN` broker_ref
- `check_order_status()` / `cancel_order()` / `modify_order()`: return synthetic responses (order doesn't exist at broker)

### EditOrder and Broker Reference Swap

Kraken's EditOrder replaces the order entirely — the old txid becomes invalid and a **new txid** is returned. `LiveOrderTracker.update_broker_ref()` swaps the reference in the tracking index. `LiveTradeExecutor.modify_limit_order()` triggers this automatically when the returned broker_ref differs from the original.

### Rate Limiting

Configurable via `rate_limit_interval_s` in broker settings (default: 1.0s). Simple time-based throttle — minimum interval between private API calls. Conservative but safe for personal use.

### Symbol Mapping

Standard symbols (e.g., `BTCUSD`) are mapped to Kraken pair names (e.g., `XXBTZUSD`) for order API calls. Two resolution paths:
1. `kraken_pair_name` from live-fetched config (set by `KrakenConfigFetcher`)
2. Static `SYMBOL_TO_KRAKEN_PAIR` fallback dict (for backtesting with static JSON)

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

This means a broker/symbol can run live **without any backtesting data in the index**. The only requirement is a broker entry in `market_config.json` (for `broker_config_path` resolution) and a matching OHLC bar fetcher for warmup.

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
