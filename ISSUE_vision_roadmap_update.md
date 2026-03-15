# Vision & Roadmap — Issue #138 Update

## Description

Draft content for updating GitHub Issue #138. Replace the current issue body with the content below.
Changes: V1.1/V1.2 feature paths removed (completed, details in README), Horizon 1 marked complete, V1.3 tracks unchanged.

---

## Content (copy below this line into Issue #138)

---

> **Disclaimer:** This is a public roadmap for transparency. We do not guarantee this will be the final roadmap — issues, issue order, features, and priorities may change at any time based on new findings, technical constraints, or shifting priorities.

---

## Vision

FiniexTestingIDE is a **parameter-centric testing IDE for quantitative algorithmic trading**. The core idea: build, test, and iteratively refine trading algorithms against real market data — with full control over every parameter, full reproducibility of every run, and clear insight into why a strategy succeeds or fails.

The system follows one architectural principle across all development horizons:

> **Execution is deterministic and reproducible. AI and intelligence features operate outside the execution loop — never inside it.**

This means: the backtesting engine, the workers, and the decision logic always produce the same result given the same inputs. Intelligence features (parameter suggestions, market analysis, regime detection) inform the *setup* of a run and the *analysis* of results, but they never interfere with trade decisions during execution.

---

## Three Horizons

### Horizon 1 — Foundation (V1.1 → V1.2): Complete

Trading simulation core, multi-market support, USER namespace, and unified test infrastructure. 

### Horizon 2 — Live Trading (V1.2 → V1.3)

Bridge the gap from backtesting to live execution via FiniexAutoTrader.

**What this includes:**
- AutoTrader foundation — adapting the core engine for live broker connections
- Production-grade trading bot — a real bot running real money (initially personal use)
- Dogfooding phase — using the system ourselves to discover what's missing, what's painful, and what the IDE actually needs from a practitioner's perspective

**Why it matters:** A backtesting framework that never touches live markets remains theoretical. The dogfooding phase is critical — it will reveal requirements that no amount of upfront design can anticipate. Findings from this phase directly shape V2.0.

### Horizon 3 — Intelligence Layer (V2.0+)

AI-augmented analysis and optimization *around* the deterministic core.

**What this includes:**
- **Parameter Sweep / Optimization** — Systematic search across parameter space instead of manual trial-and-error. Find robust parameter regions, not fragile "optimal" points.
- **Algo Diagnostics** — Post-run analysis: Why did scenario X fail? Which parameters have leverage? Where is the strategy structurally weak?
- **AI as Pre-Run Advisor** — Before a run: suggest scenarios based on recent market events, recommend parameter adjustments based on market characteristics, flag potential issues.
- **AI as Post-Run Analyst** — After a run: identify patterns across failing scenarios, correlate performance with market conditions, suggest targeted overrides.
- **Regime Detection** — A specialized worker that classifies market phases (trending, ranging, volatile, quiet). Decision logic adapts interpretation based on detected regime.
- **AI Worker Types** — API workers (HTTP-based, e.g. sentiment endpoints) and EVENT workers (WebSocket, live news feeds) as inputs alongside traditional indicators.
- **Historical LLM Signals** — FiniexDataCollector periodically queries an LLM for structured sentiment analysis and stores the results alongside tick data. During backtesting, a dedicated worker reads these pre-collected signals as historical data — making AI-augmented runs fully deterministic and reproducible. The signal worker can be toggled on/off, enabling direct A/B comparison of "with AI" vs "pure indicator" strategies. Data collection has already started (see [FiniexDataCollector #6](https://github.com/dc-deal/FiniexDataCollector/issues/6)).

**Why it matters:** The manual workflow of run → analyze → tweak → re-run is the core of a parameter-centric IDE. The intelligence layer accelerates this loop without replacing human judgment. The long-term vision: from many individual runs, extract robust parameter regions and understand *under which conditions* an algorithm performs — and when it doesn't.

### UX Layer (Planned, No Timeline)

A web-based frontend for interactive scenario building, real-time run monitoring, parameter panels, and visual analysis. This is planned but not a near-term priority. All current workflows are fully functional via CLI.

---

## Roadmap

| Version | Focus | Status |
|---------|-------|--------|
| **V1.1** | Multi-market, parameter validation, OBV, Parquet indexes | Complete |
| **V1.2** | Trading simulation core, USER namespace, extended orders | Complete |
| **V1.3** | AutoTrader foundation, production bot, dogfooding | In Progress |
| **V2.0+** | Intelligence layer, parameter optimization, AI advisory | Future |

---

## Feature Path (V1.3)

V1.3 is organized into three tracks: **Infrastructure**, **Live Pipeline**, and **Data Pipeline**. The tracks are largely independent — they converge at the AutoTrader (#133) where everything comes together.

### Track 1: Infrastructure & Tooling

Prerequisites and improvements that support both live and backtesting workflows.

- [ ] #21    REPL Shell — Server Ready Infrastructure — Memory Manager
- [ ] #175   Import Pipeline Modernization
- [ ] #29    Notebooks: deprecated
- [ ] #30    CI Pipeline for Code Guidelines Enforcement
- [ ] #195   Tick Density Discovery & Cache

### Track 2: Live Pipeline (critical path to #133)

The core live trading path. Order matters — each step builds on the previous.

```
  #198 + #197          Tick processing budget & clipping monitoring
       |               (understanding timing constraints)
       v
  #144                 Tick-Based Latency -> Millisecond-Based Timing
       |               (prerequisite for real-time execution)
       v
  #133 Step 1a         FiniexAutoTrader (runner)
       |
  #133 Step 1b         Live Warmup (BrokerHistoricalDataAPI)
       |               (workers need warmup bars before first signal)
       v
  #133 Step 2          Kraken Tick Source (WebSocket/REST)
       v
  #133 Step 3          KrakenAdapter Tier 3 (live execution methods)
       v
  #133 Step 4          Active Order Lifecycle Lifting
       v
  #151                 Reconciliation Layer
       v
  #167                 Broker Parameter Auto-Alignment
       v
  #137                 Switch off performance logging in production
       v
  #118                 Production-Grade Trading Bot
```

### Track 3: Data Pipeline (independent, not blocking live)

Data quality and backfill capabilities. Runs in parallel to Track 2.

```
  #127                 Gap-based Restore / Backfill for Tick Data
       |               (uses BrokerHistoricalDataAPI.fetch_ticks()
       |                — shared interface with #133 Step 1b)
       |
       +-- soft dep --> #132  Bar Importer Append Feature (Post-V1.3)
                              (without append, re-render after backfill is slow
                               — functional but not performant)
```

**Cross-reference:** #133 Step 1b (Live Warmup) and #127 (Gaps Filler) share the `BrokerHistoricalDataAPI` interface but operate in fundamentally different modes:
- **Live Warmup:** `fetch_bars()` — on-the-fly, in-memory, not persisted
- **Gaps Filler:** `fetch_ticks()` — persistent, written to parquet via import pipeline

These two never conflict. The shared layer is the broker API access (configured via `market_config.json`), not the downstream pipeline.

### Track 4: Signal & Intelligence Preparation

- [ ] #141   SIGNAL Worker Type & LLM Sentiment Integration

**V1.3 release after #133. The release requires a new benchmark test investigation and a valid JSON result file, complete docs check.**

---

## Related Issues (Post-V1.3)

These issues are recognized but not actively scheduled. They become relevant after the dogfooding phase reveals concrete requirements.

- [ ] #164   Extended Order Types — Pipeline Implementation
- [ ] #31    Algo Diagnostics & Parameter Discovery
- [ ] #32    Parameter Optimization System
- [ ] #128   Worker Data-Feature Requirements — Volume Data on Demand
- [ ] #130   Algorithm Templates for Generator & Future UX
- [ ] #28    IP-Protected Blackbox System — User Algos Worker / Decision
- [ ] #143   Order Book Simulation for Crypto Markets
- [ ] #136   Pydantic Migration for Configuration Validation
- [ ] #132   Bar Importer Performance — Append Feature
- [ ] #131   Generator Warmup Time Recognition
- [ ] #120   Data Pipeline Automation for FiniexDataCollector

---

**Before releasing: run full test suite, benchmark investigation, and complete docs review.**
