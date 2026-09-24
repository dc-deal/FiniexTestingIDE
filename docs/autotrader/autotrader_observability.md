# AutoTrader Observability

An unattended session runs for thirty days with nobody watching, and the only question that
matters afterwards is whether what it recorded is enough to explain what it did. A log that
omits the operator-relevant channel, or a display that hides a stalled connection, turns a
diagnosable incident into a guess.

This document is what a session SHOWS and what it WRITES: the live console, the per-tick timing
monitor that says whether the loop kept up, and the three log channels with their distinct
purposes.

**Not here:** the end-of-run report — `docs/architecture/reporting_pipeline.md`. Which log file to
open for which question — that table lives with the diagnostic sources. The warning and error
tiers — `docs/architecture/warnings_errors_tiers.md`.

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

After each tick, `autotrader_tick_loop._build_display_stats()` builds an `AutoTraderDisplayStats`
snapshot and pushes it to the display queue (`put_nowait` — dropped if full, display uses last known
state). The snapshot contains only primitives, lists, and dataclasses — safe for queue transport and
future JSON serialization.

At shutdown, the tick loop drains stale snapshots from the queue and pushes one **final** snapshot
via blocking `put(timeout=1.0)`. This guarantees the display shows the terminal pipeline state
(post-close balances, final trade count) regardless of refresh timing. The display thread performs
its own final drain after the loop exits, then renders the last frame before the `Live` context
closes.

Symbol currencies (`base_currency`, `quote_currency`) are resolved once at startup via `SymbolSpec` from the broker adapter and passed through the display stats — no string-splitting heuristic.

In spot mode, `AutoTraderDisplayStats` carries `equity` (total portfolio value in account currency)
and `spot_balances` (per-currency holdings). The PORTFOLIO panel branches on `trading_model` to
render a dual-balance layout (spot) or the standard balance view (margin).

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

### The verdict — `clipping_monitor.warn_above_ratio`

The console prints the metrics; whether they are *bad* is decided by
`SessionPostRunValidator._check_clipping`, which raises a **Tier-1 advisory** when `clipping_ratio`
exceeds the configured share (default `0.05` in `app_config.json::autotrader.clipping_monitor`). A
ratio can never exceed `1.0`, so that value disables the advisory.

This is the **only** performance verdict a live session makes, and the reason is worth stating: the
ratio is measured against *real tick arrival*, so it is grounded in what actually happened. A
per-component millisecond threshold is not — 1.2 ms is fine at 50 ms between ticks and fatal at 2 ms
— and an earlier check that tried it was removed as misinformation (see
[Warnings & Errors — Tier Taxonomy](../architecture/warnings_errors_tiers.md)). Where exactly the
line sits is a policy question, which is why it lives in config rather than in a constant.

The sim has no counterpart: it judges clipping against a *configured* `tick_processing_budget_ms` (the tick-budget advisories), while a live session has only what it observed.

### Phases

| Phase | What | Status |
|-------|------|--------|
| 1 | Per-tick processing time (`perf_counter_ns`) | ✅ |
| 2 | Clipping detection (processing > tick delta) | ✅ |
| 3 | Counters (ticks_clipped, max_stale_ms, avg) | ✅ |
| 4 | Periodic reports (configurable interval) | ✅ |
| 5 | Queue depth monitoring (`queue.qsize()`) | ✅ |
| 6 | Strategy selection (queue_all / drop_stale) | ✅ Config, drop_stale execution in #232 |

## Logging

Three `ScenarioLogger` instances per session, each with a distinct purpose:

| Logger | File | Purpose | Console |
|--------|------|---------|---------|
| Global | `autotrader_global.log` | Startup phases, shutdown, cross-cutting errors | Direct `print()` during startup |
| Session | `session_logs/autotrader_session_YYYYMMDD.log` | Per-tick processing, decisions, orders | Buffered (cleared before summary) |
| Summary | `autotrader_summary.log` | Post-session report, statistics | Flushed to console at end |

- Directory: `runs/live/<name>/<run_id>/` (from `file_logging.run_logs.live`)
- Separate from simulation runs (`runs/simulation/`); `logs/` holds only `global.log`
- Session log **rotates daily** at this market's trading-day boundary (`trading_day_anchor`
  — the swap rollover for forex, midnight UTC for crypto), and rotated days older than
  `file_logging.session_logs.retention_days` are pruned with it (#357). The rule and its
  guarantees live in [autotrader_architecture.md](autotrader_architecture.md) §Session logs

```
runs/live/btcusd_mock/20260328_105127_a1b2c3d4/
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

### The event-time column — and why a mock session shows two dates

The session log carries **two** times per line: the elapsed bracket is OBSERVATION time (how far
into the session we were), the column after the level is EVENT time — the canonical clock, pulled
through an injected `clock_fn`. §9's `ts_init` / `ts_event` pair, rendered.

```
[  1s  26ms] DEBUG    | 2026-01-24 14:19:46.420 | NEW MAX: rsi_fast    0.20ms
[  0s 214ms] INFO     |                       — | 📂 Loading broker config
```

Only the session log carries the column: it is the tick-by-tick record. `autotrader_global.log`
and `autotrader_summary.log` describe the session from outside a moment in it, so they do not.
The filler appears before the executor exists — there is no session time yet to state, and §9
forbids substituting wall-clock for it.

**In a mock replay session the column can show two different dates, and that is not a defect.**
The canonical clock is bimodal there: a tick sets it to the tick's own (replayed) timestamp, while
the idle heartbeat sets it to wall-clock so phase and operation timeouts keep tracking real elapsed
time. A replay of January data on an August afternoon therefore stamps tick lines with January and
idle-heartbeat lines with August. The property predates the column — the column only makes it
visible. It does not arise in live trading, where both sources are the same clock, and it is
harmless in replay because nothing decides on the log. Sessions driven at a low `--delay` rarely go
idle at all and show only replay dates.

### Warning/Error Summary

At session end, warning and error counts from the session logger buffer are included in the post-session summary. This gives a quick health indicator without scrolling through session logs.
