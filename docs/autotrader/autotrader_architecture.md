# AutoTrader Architecture

The AutoTrader is the live trading runner, and the claim the whole project rests on is that it
runs the SAME algorithm classes as the backtest. A worker that could tell it was running live
would break the parity proof before the first tick — so the runner and the executor change, and
nothing above them does.

This document is the entry point: what the thing is, how a tick travels through it, where every
file lives, and which document answers which question. **It is deliberately short.** The detail
sits in the six documents below, because a single file of fifteen hundred lines is one nobody
reads to the end.

FiniexAutoTrader is the live equivalent of the backtesting `process_tick_loop`. It connects tick
sources through workers and decision logic to the `LiveTradeExecutor`, using the same algorithm
classes as backtesting.

**Design constraint:** Workers and DecisionLogic must not know they are running live. Same
classes, same interfaces. Only the runner and the executor change.

## From profile to running threads

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

## Where to read next

| Document | Answers |
|---|---|
| [autotrader_runtime_model.md](autotrader_runtime_model.md) | which thread does what, why the tick source and the broker adapter are separate, how a session starts / runs / ends, what survives a restart |
| [autotrader_configuration.md](autotrader_configuration.md) | the profile cascade, the deployment identity a restarted bot inherits, the two fingerprints |
| [autotrader_data_intake.md](autotrader_data_intake.md) | tick sources, the sentiment feed, the staleness contract, live warmup |
| [autotrader_capital_and_safety.md](autotrader_capital_and_safety.md) | what the bot may spend, committed funds, whose account it is, protective levels, the circuit breaker |
| [autotrader_venue_integration.md](autotrader_venue_integration.md) | broker config acquisition, the Kraken execution tier, polling, the drift audit, the connection ladder |
| [autotrader_observability.md](autotrader_observability.md) | the live console, the clipping monitor, the three log channels |

Outside this folder, and owned there rather than here: the execution layer
([architecture_execution_layer.md](../architecture/architecture_execution_layer.md)), the live
order path ([live_execution_architecture.md](../architecture/live_execution_architecture.md)),
the protective-level contract ([protective_levels.md](../architecture/protective_levels.md)),
the session-end policy ([session_end_policy.md](../architecture/session_end_policy.md)) and the
external-connection policy
([external_connection_policy.md](../architecture/external_connection_policy.md)).

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
  autotrader_startup.py          Pipeline object creation, phase by phase
  autotrader_pipeline_bundle.py  What setup_pipeline hands back (named, not positional)
  autotrader_logger_bundle.py    The session's three log channels + run identity
  autotrader_warmup_preparator.py  Warmup bar loading (mock: parquet, live: API)
  kraken_ohlc_bar_fetcher.py     Kraken OHLC bar fetch (public API, no auth)
  live_clipping_monitor.py       Per-tick timing, clipping detection (#197)
  session_log_retention.py       Prunes rotated daily session logs (#357)
  reporting/
    autotrader_post_session_report.py   Console + file log summary
    autotrader_csv_file_report.py       Trade/order CSV export
  tick_sources/
    abstract_tick_source.py      AbstractTickSource ABC
    mock_tick_source.py          Scenario base-data replay (#438) tick source
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

configs/autotrader_profiles/          One folder per PURPOSE — the parent holds no profile
  production/                        the ones that trade for real, unattended
    ethusd_live.json                 ETHUSD, Kraken API
    solusd_live.json                 SOLUSD, Kraken API
    dashusd_live.json                DASHUSD, Kraken API
    dotusd_live.json                 DOTUSD — binds no signal source, a data-independence proof
  observation/                       real feed, dry_run pinned true, nothing reaches the venue
  field_study/                       the real-money acceptance test (#332)
  backtesting/                       mock replay, one per test suite
    mock_session_test.json           Full mock session test (BTCUSD parquet replay)
    trade_lifecycle_test.json        Trade lifecycle test (BTCUSD, 15K ticks)
    btcusd_mock_safety.json          Safety circuit breaker test (aggressive thresholds)

configs/credentials/
  kraken_credentials.json        Mock/default credentials (tracked)
```

## Session logs — daily rotation and what is kept

The tick loop rotates its session log at the market's own trading-day boundary
(`framework/utils/trading_day_anchor.py`), so a
long-running session writes `session_logs/autotrader_session_YYYYMMDD.log` once per day rather
than one file that grows for a month. The rotation is driven by BOTH event sources — a feed
that goes quiet across the boundary still rotates, because the heartbeat advances the canonical
clock and calls the same check.

**What the rotation used to leave behind is now pruned on every rotation** (#357). The window
is `file_logging.session_logs.retention_days` in `app_config.json`, 30 by default, and `0`
keeps everything. Three things it will never do: touch the active day's file, touch a file
whose name is not `autotrader_session_YYYYMMDD.log`, or delete without saying so — a removal
reaches the session channel naming the files, where the post-session summary picks it up.

The age comes from the file NAME, not its mtime. The name is the trading day the file holds;
the mtime is only the last time something was written into it, and asking the filesystem costs
a `stat` per file — about 2.1 ms on this bridged mount — to answer a question the name already
answers.

**This is the one retention rule that fires by itself, and it is the exception.** The run TREE
is pruned by `run_index_cli.py prune`, which the operator triggers and which deletes nothing
without `--apply`. The difference is who is present: these files belong to a session that is
still running, unattended, where there is nobody to ask.

## Running a session

```bash
# CLI
python python/cli/autotrader_cli.py run --config configs/autotrader_profiles/backtesting/mock_session_test.json

# VS Code launch.json
# 🤖 AutoTrader: BTCUSD Mock
```

A session that sends real orders refuses to start while its code is not committed, in this
repository or in the one its strategy comes from — and while that code is edited during the
start. `--allow-dirty` is the recorded way through the first for a deliberate test from a working
tree, never through the second — see
[Run Origin and Code Identity](../architecture/run_origin_and_code_identity.md#real-orders-from-uncommitted-code).

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
