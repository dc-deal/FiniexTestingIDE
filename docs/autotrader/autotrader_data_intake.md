# AutoTrader Data Intake

Everything the bot decides on arrives from outside the process, and every one of those inputs can
go quiet without saying so. A feed that stops sending looks exactly like a market that stopped
moving, and a bot that cannot tell them apart trades on a price from twenty minutes ago.

This document is what comes IN: the tick source abstraction and its Kraken implementation, the
sentiment feed, the staleness contract that declares an input blind, and how a live session gets
the bars it needs before its first decision.

**Not here:** how the archive is built — `docs/data_pipeline/`. What the executor does with a
tick — `docs/architecture/architecture_execution_layer.md`. Which venue answers —
`autotrader_venue_integration.md`.

## Tick Source Abstraction

`AbstractTickSource` defines the interface. Implementations:

| Source | Status | Description |
|--------|--------|-------------|
| `MockTickSource` | ✅ Built | Scenario base-data replay (#438) — ticks as fast as possible, optional `tick_delay_ms` for visual debugging |
| `KrakenTickSource` | ✅ Built (#232, #520) | Kraken WS v2 trade + ticker channel, auto-reconnect |

### KrakenTickSource (#232)

Live tick stream from the Kraken WebSocket v2 trade channel, with the ticker channel beside it supplying the quote each trade executed against. Runs `asyncio.run()` in a daemon thread (Threading model 8.a), pushes `TickData` to `queue.Queue`.

**Key features:**
- Endless reconnect with exponential backoff (1s → 60s cap)
- Connection-liveness monitoring: checks message silence every 30s, forces reconnect after 90s silence
- SSL via certifi (cross-platform: Linux Docker + Windows server)
- Single symbol per session (matches bot architecture)
- Concurrent asyncio tasks: `_receive_loop` + `_connection_monitor` via `asyncio.wait(FIRST_COMPLETED)`
- Two subscriptions on one connection, matched by the channel Kraken names in each acknowledgement

**Data Consistency Principle:** the two sides agree because they read the **same channels**, and
which channels those are changed with collector format 1.6.0.

Up to 1.5.0 both read only the trade channel. An execution happens at one price, so every tick
carried `bid == ask == last` and the zero spread was correct rather than missing. From 1.6.0 the
collector also reads `ticker` and stamps each trade with the quote it executed against;
`KrakenTickSource` does the same, which is what keeps an archived tick and a live one the same
shape (#520 step B). It is switchable per profile — `tick_source.quote_channel_enabled`, on by
default — and its failure is degraded rather than fatal: trades keep flowing without a quote, and
the age reported beside the spread says so by growing.

A tick's spread is therefore a property of the **format version it came from**, never of the venue.
A backtest window spanning the rollout sees both regimes.

What this does *not* change is the strategy's price. `last` is the traded price on both sides, and
bars, indicators and decisions read `tick.price` — see [Market Model](../architecture/market_model.md).
The quote moves `mid`, and with it the valuation plane: equity, drawdown, the mark price and the
slippage baseline. The cost model stays `MakerTakerFee` — the spread is now crossed as well as
charged, not a second fee.

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

A profile whose strategy contains a SIGNAL worker (e.g. `CORE/llm_sentiment`) declares the feed
through `scenario_settings.data_sentiment_type` — the same field a simulation scenario uses:

```json
"scenario_settings": {
  "data_sentiment_type": "crypto_sentiment",
  "start_date": "2026-04-27T05:26:21+00:00",
  "max_ticks": 20000,
  "balances": { "USD": 10000.0, "BTC": 0.0 }
}
```

Unlike ticks, sentiment does **not** drive the loop — it is passive lookup data. It is prepared
alongside the ticks by the shared `MountPreparer` (#438): the archive is resolved via the signal
index against the scenario window, carried in the data package as a `SignalSeries`, and injected
as a `SignalDataProvider` into each SIGNAL worker (`inject_signal_providers`, phase 6b in
`setup_pipeline` — the same function the sim subprocess uses). On every tick pass the worker
resolves the newest snapshot with `collected_msc ≤ tick.timestamp` (as-of lookup, no second
thread or queue). Misconfiguration (SIGNAL worker without a feed, no archive overlap for the
window) aborts at startup (§35) — never at the first tick. Details: signal data source doc
(`docs/data_pipeline/signal_data_source.md`).

Real-time/live sentiment is a future event-path feature (#375) — a mock `scenario_settings` feed
is the only supported path today.

**Live dashboard:** the ALGO STATE panel shows the feed (`📡 Feed: <label>`, flagged
`[STALE]` in yellow when the SIGNAL worker reports staleness) plus the worker's
`display=True` outputs (`sentiment`, `conf`, `signal`, `stale`).

## Market-Data Staleness Contract (#436)

The tick-stream sibling of the per-worker SIGNAL contract: when no real tick arrives for
`execution.market_data_stale_after_s` wall seconds (default 300; `0` disables), the session-level
market data counts as stale. Evaluated on the EXISTING idle heartbeat (no extra timing
mechanism) — the wall-clock is used only as a DURATION measurement; episode records are
stamped from the canonical clock.

**On the fresh→stale flip (edge, once per episode):**
1. `MarketDataStatus` set on the executor — readable any time via
   `trading_api.get_market_data_status()` (`seconds_since_last_tick` keeps growing while
   silent → the escalation input for heartbeat-driven logics).
2. Pot warning `⚠️ Market data stale since …` (reaches the session summary).
3. `on_market_data_stale(status)` dispatched — a **mandatory override for EVERY decision
   logic** (startup-validated in both pipelines; an explicit `pass` is a conscious answer).
4. The OrderGuard rejects NEW entries while stale (`STALE_MARKET_DATA`,
   `order_guard.block_stale_market_data`, default on); closes/cancels stay allowed.

**Recovery** = the next real tick: fresh status, edge reset, and a from–to episode span into
the pot (`✅ Market data recovered: stale 12:03:10 → 12:05:23 (2m 13s)`) — the v0 stale
protocol; the aggregated stability table is #433 scope. Transport reconnects
(`get_reconnect_count()` delta) are surfaced as pot warnings too. The CONNECTION panel's
"Last Tick" line carries a contract-driven `[STALE]` tag.

**Boundaries:** sim never evaluates this (replay gaps are DATA — weekend/holiday); the planned
`stale_data_stress` windows drive the same surface deterministically in backtests (see
`docs/stress_test.md`). Since #438 the AutoTrader-mock also expresses `stale_data_stress`
(`scenario_settings.stress_test_config`) — the SIGNAL data-plane carve (a stale sentiment window);
the tick status-plane carve stays sim-only (→ #444). The mock market-data outage drill is
`tick_source.freeze_after_ticks` + `freeze_duration_s` (one deliberate mid-replay silence). Live sources today are crypto/24-7 —
the forex weekend gate (don't flag market closure as stale) lands with the MT5 adapter via
MarketClock. On #375 the evaluation trigger moves onto the event timeline; the contract
surface (status, hook, guard reason, config) carries over unchanged. Authoring guidance:
`docs/user_guides/live_outage_handling_guide.md`.

## Live Warmup (#231)

Workers need warmup bars before producing meaningful signals. Without warmup, a worker with `{"M5": 14}` needs 70 minutes of live ticks before its first valid RSI. The warmup system pre-loads historical bars at startup.

### Two Paths

| Aspect | Mock (parquet) | Live (API) |
|--------|----------------|------------|
| **Source** | Pre-rendered bar parquet via `BarsIndexManager` | Kraken `GET /0/public/OHLC` |
| **Reference time** | First tick timestamp from parquet file | `datetime.now(UTC)` |
| **Network** | No | Yes (public, no auth) |
| **Extensibility** | Static data | ABC pattern → MT5 (#209) |
| **On a short read** | warn and continue | **refuse to start** (#473) |

### Why live refuses rather than warns (#473)

The fetch has a retry ladder, and a give-up produces an empty result rather than an
exception — so the refusal is raised one level up, by the validation that knows *how many*
bars are missing on *which* timeframe and can say so.

It refuses because **there is no contract for an empty indicator history.** A stale signal
and a stale market feed each have one (#434 / #436): the input declares itself unusable and
the algo's mandatory hook decides. A worker with 0 of 200 bars declares nothing — it still
emits a number, that number is wrong, and nothing marks it wrong. Trading on it is not a
reduced run, it is a different one.

```
❌ Warmup requirement unmet: H1: 0/200. The broker's bar history could not be read, and
   there is no staleness contract for an empty indicator history — refusing to start
   rather than trading on unreliable worker output.
```

The mock path keeps warning: it reads a local archive, a short window is a data question
the operator can see, and refusing would block replay runs that deliberately start near
the edge of their data.

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

  4. Validate: mock warns if fewer bars than required — LIVE REFUSES (#473)

  5. bar_renderer.initialize_historical_bars() per timeframe
     → Workers have full history from tick 1
```

### Direct Injection

AutoTrader is single-process. Backtesting uses `inject_warmup_bars()` with bar dicts for subprocess
transport (pickle, CoW). AutoTrader bypasses this — creates `Bar` objects directly and calls
`bar_renderer.initialize_historical_bars()`. No serialization round-trip.

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
