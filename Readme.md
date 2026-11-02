# FiniexTestingIDE

**Parameter-centric backtesting for algorithmic trading strategies**

> ⚠️ **No financial advice.** This software is for educational and research purposes only.

> **Version:** 1.5.0
> **Status:** Alpha
> **Versioning:** until 2.0, a minor version counts a development cycle and may break what came before it; semantic versioning applies from 2.0 on. The API's compatibility is its contract version, which every answer carries
> **Target:** Developers with Python experience who want to systematically backtest trading strategies

**New here?** Start with the [Introduction](docs/introduction_to_the_ide.md) — the two pipelines and
the kinds of run on one page — and keep the [Glossary](docs/glossary.md) at hand: each term in this
project means one thing.

---

## What's New in 1.5.0

- **GIPS-Aligned Results — Composites and a Daily Track Record** — The ledger keeps one booking period per trading day, anchored on the market's own day, so a backtest and a live session can be compared day by day. A run's accounts fold into one result the way the Global Investment Performance Standards fold a composite: what happened adds up, the ratios are recomputed from the added parts, and a reading of one account has no answer across them. Every figure declares which kind it is, and a live bot that would continue an open position under a changed composite is refused unless that one start accepts the change.
- **Unattended Live Operation** — A bot restarts where it left off. It adopts its own resting orders at the venue by a key it minted, trades only its declared share of the account, continues its drawdown curve and risk baseline only when a session of its own kind wrote them, and lets its decision logic answer for the boot situation. A kernel lock refuses a second start of the same bot, a start that fails on a passing fault exits so the supervisor tries again, and a normal stop waits, within a bound, for the orders still on their way.
- **The Venue Decides What an Order Can Carry** — A strategy declares the order features it uses, and a start refuses what its venue does not offer — a backtest too, unless its set declares a recorded exception. A declared stop or target is enforced in live as well, and a bot owns its exits.
- **Resolved by Asking, Never Retried** — One retry ladder and one give-up rule for every external connection. A write whose answer was lost is never sent twice: the venue is asked what became of it, and the live order path books what the venue answers, never a timer or a failed read.
- **Operator Alerts and Session Status** — Telegram messages per symptom and at each trading day's close, and a liveness ping that vouches for the tick loop, so a dead or hung session reaches the operator. A running session can be asked how it stands, and the dashboard survives a panel that fails.
- **A Record That Survives a Crash** — Every trading day of a session is sealed at its boundary, so a session that dies on day 29 keeps every day it sealed. Every run states who started it, with exactly which code and configuration, and what it is for; every order transition is a crash-safe stream; a deployment marks where its sessions ran different code; and the ledger and the deployment history are readable over the API.
- **The Security Study — a Fourth Live Certificate** — Five real-money sessions provoke the order guard's refusals and the circuit breaker's block and stop, a restart included, and the certificate says rule by rule how each is proven. The field study runs on the same engine and reads the live core's own record, and one check before the tag holds all release certificates to the release and its code.
- **Fees and Risk Limits You Can Trust Over Thirty Days** — A live fill is booked with the venue's own fee, the exit fee is booked too, and a fee rate comes from one answered source. One drawdown definition, measured on the per-tick equity curve, is shared by the circuit breaker and the reports and survives a restart, beside an absolute drawdown cap and a daily loss limit.
- **Data Fidelity** — Prices are stored as the double their decimal parses to, ticks recorded without a quote get one estimated from the trades' own sides, MT5 times follow the broker server's clock rule, and every warmup uses the last bars complete at its first tick. Every data file says which instance wrote it, the live tick source records every outage with the venue's trade ids around it, and the signal push stream recovers a missed envelope.

### Previous Releases

- **1.4.0** — SIGNAL workers (external, model-produced data as a first-class input), live signal transport, mandatory staleness contracts, signal reporting (offered vs. decided-on), run-outcome contract and exit codes, compute only what is consumed, unified release certificates
- **1.3.1** — Production-bot case study, unified reporting pipeline, trade analytics (MAE/MFE, R-multiple, expectancy), robustness validation (IS/OOS), parameter sweep, honest-backtest tooling (swap cost, broker-derived precision), restart-safe algo memory
- **1.3.0** — Live acceptance certificate (Field Study), reconciliation foundation, decision event channel, timer-driven loop clock & cadence, order-lifecycle hardening, audit telemetry + API performance monitor
- **1.2.3** — Live execution architecture refactor (symmetric sim/live pipelines, async HTTP dispatch), async modify/cancel/position-modify, broker trade record model (order ↔ executions pairing)
- **1.2.2** — FiniexAutoTrader Live Trading Pipeline, Live Console UI, Spot Trading Model, Order Guard, AwarenessChannel, REST API Foundation, Dual-Pipeline Parity Tests, User Algo Workspace
- **1.2.1** — Millisecond-based latency timing, inbound-only fill semantics, tick processing budget, generator profile system
- **1.2.0** — USER Namespace, trading core completion (all order types), tick data trimming, unified test runner, discovery cache
- **1.1.2** — STOP/STOP_LIMIT orders, active order preservation and reporting
- **1.1.1** — Live Trade Executor, MockBrokerAdapter, margin/multi-position/pending stats test suites
- **1.1** — Multi-market support (Kraken Spot + MT5), parameter validation, OBV worker

---

## What is FiniexTestingIDE?

FiniexTestingIDE is a high-performance backtesting and live trading framework for forex and crypto strategies. It processes real tick data, simulates realistic broker execution, and connects directly to real venues for production trading.

**Core capabilities:**
- ✅ Tick-by-tick backtesting with real market data
- ✅ Realistic trade simulation (spreads, latency, margin)
- ✅ Multi-scenario parallel execution
- ✅ Deterministic results (seeded randomness); every run records the code, configuration and broker configuration it used and what kind of data it read, so an old result stays a true record ([why](docs/introduction_to_the_ide.md#a-run-is-a-record-and-an-old-one-stays-true))
- ✅ Multi-market support (Forex via MT5, Crypto via Kraken)
- ✅ Live trading via FiniexAutoTrader (Kraken Spot, production-validated)
- ✅ Validated accuracy — comprehensive integration, black-box, and white-box test suites
- ✅ Validated performance — standardized benchmark certificate with regression detection
- ✅ Validated live execution — real-money end-to-end acceptance certificate (Field Study)
- ✅ External signal input — recorded, model-produced data (LLM sentiment) as a worker input, live or from an archive

---

## Features

### Data Pipeline
- **TickCollector (MQL5)** - Live tick collection from MT5 brokers
- **Parquet Storage** - Compressed, indexed tick data with quality metrics
- **Multi-Timeframe Bars** - Auto-rendered for every timeframe in `processing.render_timeframes` (M1 to D1 by default)
- **Gap Detection** - Weekend, holiday, and data quality analysis
- **Discovery Caching** - Unified cache system for all discoveries (gap reports, volatility profiles, extreme moves)

### Backtesting Engine
- **Parallel Execution** - ProcessPoolExecutor for multi-scenario runs
- **7-Phase Orchestration** - Validation → Loading → Execution → Reporting
- **Worker System** - Modular indicator computation (RSI, Bollinger, MACD, OBV, ...)
- **Decision Logic** - Pluggable trading strategies with clear separation
- **Parameter Validation** - Schema-based validation with strict/non-strict modes
- **User Algo Workspace** - Custom workers and decision logics loaded by file path from `user_algos/` (one class per file, found by introspection; `rescan()` reloads)

### FiniexAutoTrader
- **AutoTrader Pipeline** - Real-time tick loop connecting broker WebSocket → workers → decision logic → order execution
- **Kraken Spot Adapter** - WebSocket v2 tick source, REST warmup (OHLC bars), live account balance, order execution
- **Dry-Run Mode** - Every order is validated by the venue (`validate=true` on Kraken) and never placed; fills are simulated locally when the market reaches the order's price
- **Live Console UI** - Rich terminal dashboard: session health, portfolio, positions, orders, trade history, algo state
- **Spot Trading Model** - Dual-balance (quote + base asset), equity calculation, safety circuit breaker on equity
- **Order Guard** - Duplicate signal guard, SHORT protection in spot mode, rejection cooldown
- **AwarenessChannel** - Decision logic narration channel for live display integration
- **Clipping Monitor** - Detects when tick processing time exceeds tick arrival interval
- **Restart-Safe Operation** - On boot a bot adopts its own resting orders at the venue, continues its risk baseline and drawdown curve, refuses a second start of itself, and leaves a passing fault to its supervisor's retry
- **Operator Alerts** - Telegram messages per symptom and at each trading day's close, and a liveness ping that vouches for the tick loop

### Trade Simulation & Execution
- **Realistic Execution** - Inbound latency simulation with ms-timestamp fill detection (seeded)
- **Live Trade Executor** - Broker adapter communication with pending order tracking
- **Spread Calculation** - The bid/ask spread from tick data where it was recorded; where it was not, a spread estimated from the trades' own sides, reported as its own figure
- **Margin Management** - Position sizing with margin checks
- **Order Lifecycle** - PENDING → EXECUTED status tracking (Market, Limit, Stop, Stop-Limit)
- **Limit Orders** - Two-phase lifecycle: latency simulation → price trigger monitoring
- **Stop Orders** - Breakout entry with market fill (STOP) or limit conversion (STOP_LIMIT)
- **Multi-Broker Fees** - Spread-based (MT5, paid inside the fill price, no per-side fee) and maker/taker (Kraken, charged on every fill so a round trip pays twice; maker fee for limit fills)
- **Mock Testing** - MockBrokerAdapter for deterministic pipeline verification

### Signal Data (External / Model-Produced Input)
- **SIGNAL Worker Type** - Reads recorded, model-produced signals (LLM sentiment) as a first-class worker input, in both the backtesting and AutoTrader pipelines
- **No-Look-Ahead Merge** - Snapshots resolve by their receive timestamp, so a backtest cannot see a signal before it existed
- **Live Transport** - A live-adapter session starts empty and is filled by an arriving feed; the worker cannot tell a mounted archive from a live one
- **Staleness Contracts** - Every decision logic must declare its reaction when a signal or the market data goes quiet
- **Coverage & Decision Reporting** - What the archive could offer, what the strategy decided on, and when disturbances happened — reported separately

### Analysis Tools
- **Discovery System** - Unified analysis with Parquet-based caching (auto-invalidation on data change)
  - **Market Analysis** - ATR volatility, session activity, cross-instrument ranking
  - **Extreme Move Scanner** - Directional price movement detection (ATR-normalized)
  - **Data Coverage** - Gap detection and data quality assessment
- **Scenario Generation** - Automatic blocks (chronological) or high-volatility selection
- **Performance Profiling** - Operation-level breakdown, bottleneck detection
- **Robustness Validation** - Multi-window In-Sample/Out-of-Sample overfit guard (distribution, walk-forward efficiency)
- **Parameter Sweep** - Grid search over strategy parameters with objective ranking and per-parameter sensitivity
- **Trade Analytics** - Per-trade MAE/MFE excursion, R-multiple, and expectancy
- **Unified Reporting** - One result model rendered identically to console, file, CSV, and API

> See [Discovery System](docs/discovery_system.md) for architecture details.

→ See [CLI Tools Guide](docs/cli_tools_guide.md) for all available commands.

---

## Quick Start

```
1. Collect tick data    →  TickCollector (MT5)
2. Import to Parquet    →  📥 Import (Override)
3. Create your strategy →  Worker + Decision Logic + Config
4. Run backtest         →  🔬🧪 Run User Scenario Set (prompt)
```

→ See [Quickstart Guide](docs/user_guides/quickstart_guide.md) for step-by-step instructions.

---

## Configuration

Two-tier system: **default configs** (`configs/`, version controlled) and **user overrides** (`user_configs/`, gitignored). The system deep-merges user overrides — override only what you need, everything else stays at defaults.

→ See [Config Cascade Guide](docs/config_cascade_guide.md) for the full configuration system, directory structure, and scenario-level parameter overrides.

---

## Sample Data

A sample dataset is available for testing and learning (updated 2026-11-02, 5.56 GB):

**Download:** [download page](https://finiex-rag.duckdns.org/downloads/)

### Installation

Extract the ZIP contents to an empty `data/processed/` (delete an older package first):

```
data/processed/
├── mt5/
│   ├── ticks/
│   │   ├── AUDUSD/ ... USDJPY/
│   └── bars/
├── kraken_spot/
│   ├── ticks/
│   │   ├── ADAUSD/ ... XRPUSD/
│   └── bars/
└── signals/
    └── crypto_macro_sentiment/ ... forex_macro_sentiment_mock/
```

The package holds no index files and no discovery caches. Both are derived: each is built from the
data it describes on first use, so the first run takes a little longer.

### Dataset Overview

| Broker | Symbols | Time Range | Ticks |
|--------|---------|------------|-------|
| MT5 (Forex) | AUDUSD, EURGBP, EURUSD, GBPUSD, NZDUSD, USDCAD, USDCHF, USDJPY | Sep 2025 → Oct 2026 | ~262M |
| Kraken Spot | ADAUSD, BTCUSD, DASHUSD, DOTUSD, ETHEUR, ETHUSD, LTCUSD, SOLUSD, XRPUSD | Jan → Oct 2026 (DOTUSD from Sep) | ~50M |

**Total: ~312M ticks across 17 instruments (8 Forex pairs + 9 Crypto), with auto-rendered M1–D1 bars**

Only our own collection is in the package. Trade history a venue publishes, which the archive imports
beside it, is left out: it is the venue's to distribute, not ours.

The two brokers differ in more than their instruments: Kraken publishes a central order book
where every trade prints at a price, while MT5 forex is dealer-quoted and has no traded price
at all. That changes what a bar is made of, so it is declared per broker rather than assumed —
see [Market Model](docs/architecture/market_model.md) for the three axes that describe a venue
and which combinations the framework currently supports.

Sentiment signals are included as a separate data source: ~755k rows across crypto, crypto macro
and forex macro sentiment, covering Jan → Oct 2026 (synthetic backfill up to 2026-07-15,
collected from a live feed afterwards).

> ⚠️ **Data Disclaimer:** The provided dataset consists of historical tick and bar data
collected locally via MetaTrader 5 and the Kraken Spot API and processed into Parquet
format. The included sentiment signals are partly synthetic.

The data is provided strictly for research, backtesting and
educational purposes. It is not a licensed market data feed,
may contain gaps or inaccuracies, and must not be used for
live trading or commercial redistribution.

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        DATA FLOW                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  MQL5 TickCollector                                             │
│         ↓                                                       │
│  JSON Files (raw ticks)                                         │
│         ↓                                                       │
│  Import CLI (UTC conversion, quality metrics)                   │
│         ↓                                                       │
│  Parquet Files + Bar Rendering (M1→D1)                          │
│         ↓                                                       │
│  ┌───────────────────────────────────────────────────────────┐  │
│  │  BACKTESTING ENGINE                                       │  │
│  ├───────────────────────────────────────────────────────────┤  │
│  │  Workers (RSI, Bollinger, ...)  →  Indicator Values        │  │
│  │         ↓                                                 │  │
│  │  Decision Logic (AggressiveTrend, ...)  →  BUY/SELL       │  │
│  │         ↓                                                 │  │
│  │  Trade Simulator  →  Order Execution + P&L                │  │
│  └───────────────────────────────────────────────────────────┘  │
│         ↓                                                       │
│  Results (Trade History, Performance Metrics, Profiling)        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

→ See the [Execution Layer](docs/architecture/architecture_execution_layer.md) and [Live Execution](docs/architecture/live_execution_architecture.md) architecture docs for the full Sim/Live hybrid design.

---

## Quality Assurance

Comprehensive test suite covering integration tests, black-box tests, and white-box tests. All suites are runnable with the provided sample dataset.

```bash
python python/cli/test_runner_cli.py
```

Individual suites can be run separately: `pytest tests/<group>/<suite>/ -v`

Configuration: `configs/test_config.json` (excluded suites, fail-fast behavior). Each test suite has its own documentation in [`docs/tests/`](docs/tests/).

**Release Certificates.** Every release is gated by independent certificates, each written as a machine-checkable artifact with a 90-day validity and verified by its own test:

| Certificate | Validates |
|---|---|
| **Benchmark** | Throughput against registered system baselines (3-run median, stability and configuration guards) |
| **Live Adapter** | The broker API contract — order placement, limit lifecycle, and a real market round-trip |
| **Live Field Study** | The full live execution stack on a real account: every order type, modify/cancel, rejection battery, partial close, idle heartbeat |
| **Security Study** | The account's protective rules on a real account: the order guard's refusals, the circuit breaker's block and stop, and the risk baseline across a restart |
| **Signal Feed** | The external signal producer's contract against its running production instance |

All share one identity: the declared release is checked against the version the repository carries, and a certificate is refused if it would be taken from uncommitted work. Before the tag, one check holds the live certificates to the release and its code. Details in [`docs/architecture/release_certificates.md`](docs/architecture/release_certificates.md).

**Size of the codebase per release.** Physical lines as [`analyze_project_structure.py`](python/experiments/analyze_project_structure/analyze_project_structure.py) counts them: comments, docstrings and blank lines included. Production is `python/`, tests are `tests/`, MQL5 is the MetaTrader collector source. A row is never changed once its release ships.

| Release | Production | Tests | MQL5 | Total | Test : production |
|---|---|---|---|---|---|
| 1.5.0 | 188,313 | 130,646 | 1,584 | 320,543 | 0.69 : 1 |
| 1.4.0 | 95,196 | 44,347 | 1,183 | 140,726 | 0.47 : 1 |

---

## Documentation

See the [Documentation Index](docs/documentation_index.md) for a complete overview of all guides, architecture docs, data pipeline docs, and test suite documentation.

---

## Current Limitations

- **No native OCO/Iceberg/trailing-stop order types** - Market, Limit, Stop, and Stop-Limit are supported; extended order types are planned. Strategy-level trailing (moving a position's stop as price advances) is supported and demonstrated by the reference strategy.
- **No Broker-Side Partial-Fill Detection** - An order at a real venue is treated as pending until fully filled; a broker-reported *partial* fill (one order, multiple executions) is not yet surfaced as its own state. Partial position close (closing a fraction of an open position) is supported in both backtesting and AutoTrader sessions.
- **Live trading on spot only** - The live path is proven on Kraken Spot. The margin account model runs in backtests and against the mock venue; a live margin adapter (MT5) is planned
- **FiniexViewer in progress** - HTTP API available; browser UI in active development (see [FiniexViewer Setup](docs/user_guides/finiexviewer_setup.md))

> **Note on Multiple Positions:** Full multi-position support is implemented and validated by integration tests, and the reference strategy (`CORE/trend_channel_reference`) actively stacks several positions on a symbol. Holding multiple *symbols* against one shared capital pool — portfolio backtesting — is the next core-engine milestone. See `configs/scenario_sets/backtesting/multi_position_test.json` for a reference on how to build multi-position scenarios.

---

## Roadmap

**Horizon 1 — Foundation: Complete (V1.1–V1.2)**
- Trading simulation core with all order types (Market, Limit, Stop, Stop-Limit, Partial Close)
- USER Namespace — custom workers and decision logic outside framework code
- Multi-market data pipeline (MT5 Forex + Kraken Spot, 16 instruments)
- Unified test infrastructure and discovery cache management

**Horizon 2 — Live Trading: Released (V1.3)**
- FiniexAutoTrader pipeline with Kraken Spot, production-validated (V1.2.2)
- Live execution refactor with async dispatch and broker trade record model (V1.2.3)
- Live acceptance certificate (Field Study), reconciliation foundation, decision event channel, timer-driven loop cadence (V1.3.0)
- Production-bot case study, restart-safe algo memory, unified reporting, trade analytics, robustness validation, and parameter sweep (V1.3.1)
- The SIGNAL chain — external, model-produced data as a first-class worker input, with mandatory staleness contracts and a live transport (V1.4)
- Unattended live operation — what a strategy needs to run thirty days on a real account without a human watching: restart and state recovery, operator alerts, resolution of lost answers, and a security study that proves the guards with real money (V1.5)
- Next: the thirty-day real-money run and its parity proof against a backtest of the same period (V1.5.x), then hardening (V1.6), the portfolio backtest (V1.7) and an event-driven loop core (V1.8)

For the full vision, detailed roadmap, and feature path see **[Issue #138 — Vision & Roadmap](https://github.com/dc-deal/FiniexTestingIDE/issues/138)**.

---

## License

MIT License - see [LICENSE](LICENSE)

**Trademarks:** Finiex™ is property of Frank Krätzig - see [TRADEMARK.md](TRADEMARK.md)

## Author

**Frank Krätzig** — Software Developer & Data Engineer, Karlsruhe region

15 years of IT experience focused on data-driven backend solutions,
ETL pipelines, and performance optimization.

- LinkedIn: https://www.linkedin.com/in/frank-kraetzig
- GitHub: https://github.com/dc-deal