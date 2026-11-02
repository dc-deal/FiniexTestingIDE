# AutoTrader Data Intake

Everything the bot decides on arrives from outside the process, and every one of those inputs can
go quiet without saying so. A feed that stops sending looks exactly like a market that stopped
moving, and a bot that cannot tell them apart trades on a price from twenty minutes ago.

This document is what comes IN: the tick source abstraction and its Kraken implementation, the
sentiment feed, the staleness contract that declares an input blind, and how an AutoTrader session
gets the bars it needs before its first decision.

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
- Endless reconnect with exponential backoff (1s → 60s cap, spread by jitter). The backoff starts
  over only after a connection has DELIVERED for `backoff_reset_after_s` (default 10 s), measured
  from its subscription to its last message — a far side that accepts connections and drops them
  again is dialled less and less often, instead of about once a second (#609). The venue's front
  refuses an address after about 150 connection attempts in ten minutes.
- Connection-liveness monitoring on the monotonic clock: checks the silence every second and
  aborts the connection after 10 s without a message. Every message counts, heartbeats included,
  and Kraken sends a heartbeat about once a second on every connection — measured on 2026-10-20
  by `python/experiments/venue_probes/probe_kraken_ws_silence.py`, the longest pause on a healthy
  connection 1.15 s — so 10 s of silence is a dead connection, never a quiet market.
- A socket whose trade subscription is refused or times out, a silent one and a stopped one are all
  aborted at once, without a close handshake, so none stays open at the venue's front; a refused
  quote subscription only degrades the quote. Stopping the session wakes the loop wherever it
  waits, a backoff pause included.
- SSL via certifi (cross-platform: Linux Docker + Windows server). The address must be `wss://`;
  `ws://` is accepted only to the local machine.
- Single symbol per session (matches bot architecture)
- Concurrent asyncio tasks: `_receive_loop` + `_connection_monitor` via `asyncio.wait(FIRST_COMPLETED)`
- Two subscriptions on one connection, matched by the channel Kraken names in each acknowledgement

**Data Consistency Principle:** the two sides agree in shape because they read the **same
channels**, and which channels those are changed with collector format 1.6.0.

Up to 1.5.0 both read only the trade channel. An execution happens at one price, so every tick
carried `bid == ask == last` and the zero spread was correct rather than missing. From 1.6.0 the
collector also reads `ticker` and stamps each trade with the quote it executed against;
`KrakenTickSource` does the same, which is what keeps an archived tick and a live one the same
shape (#520 step B). It is switchable per profile — `tick_source.quote_channel_enabled`, on by
default — and its failure is degraded rather than fatal: trades keep flowing without a quote, and
the age reported beside the spread says so by growing.

A tick's spread is therefore a property of the **format version it came from**, never of the venue.
A backtest window spanning the rollout sees both regimes.

**A backtest and a mock session part here, on purpose.** A backtest gives a tick recorded without
a quote the quote the spread estimated for its previous day implies (`modelled_spread_factor`, see
[Discovery System](../discovery_system.md)); a mock session does not. It rehearses the live path,
whose venue always sends a quote, and its executor books every crossed spread as measured — so it
replays such ticks as recorded, bid equal to ask. On a window from before format 1.6.0 a mock
session therefore crosses no spread where a backtest of the same window crosses a modelled one.

What this does *not* change is the strategy's price. `last` is the traded price on both sides, and
bars, indicators and decisions read `tick.price` — see [Market Model](../architecture/market_model.md).
The quote moves `mid`, and with it the valuation plane: equity, drawdown, the mark price and the
slippage baseline. The cost model stays `MakerTakerFee` — the spread is now crossed as well as
charged, not a second fee.

The same channels make an archived tick and a live one the same shape, not the same content. Each
side sees trades only while its own connection holds, so around a reconnect one of them can miss
trades the other received; where a file carries the venue's trade ids, the tick index counts the
trades that file missed (`trade_id_gaps`), and only a file missing enough of them to count as a
hole is declared `missing_trades` ([Data Provenance](../architecture/data_provenance.md), *What a
file LACKS*). A session keeps no copy of the ticks it consumed, and a backtest of its window
reads the collector's archive instead — so a difference between the two is first an input
difference, and only then a fill difference ([A run is a record, and an old one stays
true](../introduction_to_the_ide.md#a-run-is-a-record-and-an-old-one-stays-true)).

**The session records what its own connection missed (#609).** Every outage of the tick stream —
from the loss of a connection to the first trade after it — is one line of
`io/tick_stream_events.jsonl`: when it was noticed, how it was detected (`silence`,
`closed_by_venue`, `connection_error`, `ping_timeout`, `error`), how long the lost connection had
delivered, the connection attempts until the stream came back, and the venue's public trade ids
around the gap. The ids between the two, exclusive, bound the trades the session never saw — an
upper bound, because the venue allocates some ids that never become public trades; which of them
were trades is to be read from the venue's own record after the run, by the comparison #512
plans. A burst of connections that close before anything arrives is one outage with several
attempts, and a trade that arrives ahead of its subscription's confirmation is used once the
subscription stands and dropped with it if it fails. An outage the session ends in is written at
the end with what is known — the last id before it, no first trade after it.

```json
{"record_kind":"outage","outage_no":3,"symbol":"ETHUSD","lost_at":"2026-11-02T03:14:07.512000+00:00","detected_by":"silence","delivered_for_s":5821.4,"attempts":2,"restored_at":"2026-11-02T03:14:10.204000+00:00","outage_s":2.692,"last_public_trade_id":107927359,"last_public_trade_time":"2026-11-02T03:13:56.881000+00:00","first_public_trade_id":107927373,"first_public_trade_time":"2026-11-02T03:14:11.020000+00:00","data_gap_s":14.139}
```

The ids stay on this record: the tick a worker sees carries none, so nothing here reaches the
decision plane or needs an archive counterpart. A live session never fetches the missed range —
a trade read back over the venue's REST route has no quote and no receipt time — and the
comparison of a run, planned in #512 and not built yet, is to read the range from the venue after
the run. The same file lists the trades the parser refused (`refused_public_trade`, below).

**A trade without a trustworthy venue time is refused, never repaired.** A missing or unreadable
venue time, a time without a UTC offset, a price or a quantity that is not a finite positive
number, or an entry that is not in the shape the venue documents: the trade becomes no tick, a
line in the record says why, and the session log says so once per kind and sums them up at the
end. The record lists the first 100 refusals of each reason and counts the rest, and its last
line carries the totals (`refused_public_trade_totals`) — a venue that changed its format would
otherwise add one line per trade for as long as the session runs. The local clock never stands
in for the event time — it is the receipt time, `collected_msc`.

```
DataCollector            AutoTrader (live adapter)
┌──────────┐            ┌──────────┐
│ Kraken   │            │ Kraken   │
│ WS v2    │            │ WS v2    │
│ trade ch │            │ trade ch │  ← Same channel, same shape
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
    "backoff_reset_after_s": 10.0,
    "connection_check_interval_s": 1.0,
    "connection_dead_s": 10.0
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
window) aborts at startup — a session has nothing to exclude, so it refuses to start — never at
the first tick. Details: signal data source doc (`docs/data_pipeline/signal_data_source.md`).

A live-adapter session mounts no `scenario_settings` window: its SIGNAL workers are fed by the
producer's push stream (`sentiment_config.json::stream`, #468), which extends the series from its
first frame on — at boot only the archive slice it connects from is read. The `scenario_settings`
feed above is the mounted path, and it is the mock session's.

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
(`get_reconnect_count()` delta) are surfaced as pot warnings too — one per outage the stream came
back from, never one per connection attempt (#609). The CONNECTION panel's
"Last Tick" line carries a contract-driven `[STALE]` tag.

**Boundaries:** sim never evaluates this (replay gaps are DATA — weekend/holiday); the planned
`stale_data_stress` windows drive the same surface deterministically in backtests (see
`docs/stress_test.md`). Since #438 a mock session also expresses `stale_data_stress`
(`scenario_settings.stress_test_config`) — the SIGNAL data-plane carve (a stale sentiment window);
the tick status-plane carve stays sim-only (→ #444). The mock market-data outage drill is
`tick_source.freeze_after_ticks` + `freeze_duration_s` (one deliberate mid-replay silence). Venue tick sources today are crypto/24-7 —
the forex weekend gate (don't flag market closure as stale) lands with the MT5 adapter via
MarketClock. On #375 the evaluation trigger moves onto the event timeline; the contract
surface (status, hook, guard reason, config) carries over unchanged. Authoring guidance:
`docs/user_guides/outage_handling_guide.md`.

## Warmup (#231)

Workers need warmup bars before producing meaningful signals. Without warmup, a worker with `{"M5": 14}` needs 70 minutes of live ticks before its first valid RSI. The warmup system pre-loads historical bars at startup — the last N bars **complete at the session's first tick**, in both paths, so a mock session, a live session and a backtest of the same window start from the same history. The rule, the seam and the guard: [Warmup History](../architecture/warmup_history.md) (#588). A session writes down the history it started from, `io/warmup_history.json` in its run directory, because a live-adapter session's comes from the venue and could not be read again later ([Warmup History](../architecture/warmup_history.md), *What a session records*).

### Two Paths

| Aspect | Mock session (archive) | Live-adapter session (API) |
|--------|----------------|------------|
| **Source** | The bars the shared mount prepared — the same bars a backtest of the window warms up on | Kraken `GET /0/public/OHLC` |
| **Selected for** | The first tick the session replays | The instant of the boot read — then the first tick, where a period ended in between (the seam) |
| **Network** | No | Yes (public, no auth) |
| **Extensibility** | Static data | ABC pattern → MT5 (#209) |
| **No bar at all for a timeframe** | refuse to start | refuse to start |
| **Fewer bars, the source holds no more** | warn and continue | warn and continue |
| **The venue could not be read** | — | **refuse to start** (#473) |

### Why a live-adapter session refuses rather than warns (#473)

The fetch has a retry ladder, and a give-up produces an empty result rather than an
exception — so the refusal is raised one level up, by the check that knows *which* timeframe
could not be read and can say so.

It refuses because **there is no contract for an empty indicator history.** A stale signal
and a stale market feed each have one (#434 / #436): the input declares itself unusable and
the algo's mandatory hook decides. A worker with 0 of 200 bars declares nothing — it still
emits a number, that number is wrong, and nothing marks it wrong. Trading on it is not a
reduced run, it is a different one.

```
❌ Warmup requirement unmet: the broker's bar history could not be read for H1, and there is
   no staleness contract for an empty indicator history — refusing to start rather than
   trading on unreliable worker output.
```

A history that is merely SHORT because the source holds no more — the archive's first bars, the
venue's reach of 720 intervals — warns in both paths and starts. A timeframe with not a single bar
refuses in both, and so does a requirement beyond `execution.bar_max_history`: the renderer keeps
at most that many bars per timeframe and would trim a larger history at injection without a word.
For a mock session the mount has already judged the warmup the way it judges a backtest's, so a
window a backtest would refuse is refused here too.

### Flow

```
Phase 9 in setup_pipeline():

  1. calculate_scenario_requirements(workers)
     → warmup_by_timeframe = {"M5": 20, "M30": 20}

  2. Capacity: every requirement fits execution.bar_max_history — else refuse

  3. Load the history:
     Mock:         the package the shared mount prepared (SharedDataPreparator.prepare_bars:
                   the last N bars complete at the first tick) → WarmupHistory.from_package
     Live adapter: WarmupHistory.from_venue — KrakenOhlcBarFetcher at the instant of the read

  4. Judge it: an unreadable venue or an empty timeframe refuses, a short one warns

  5. WarmupHistory.inject_into(bar controller)
     → BarRenderingController.inject_warmup_bars checks every bar, then sets the history
     → the seam is armed: at the first tick, before it is rendered, every timeframe whose
       newest bar ends before that tick's period opens is read again for the tick (live only)
```

### When the first tick cannot close the seam

Rare, and shown as a refusal rather than a crash. When the first trade arrives within about two
seconds of a period's end that also lies after the boot read, the venue may not have committed that
period yet; reading it again then still lacks the bar, and the session stops before that tick's
workers and decision run:

```
============================================================
  ⛔ REFUSED AT THE FIRST TICK — its workers and decision did not run
  ETHUSD M1: the venue has not yet committed the period before the first tick (…); its newest
  committed bar opens at …. Refusing before the workers and the decision run on that tick rather
  than trading on a history with a missing bar — the venue commits a period within about two
  seconds of its end, so a restart a few seconds later closes it.
  No order was submitted in this session.
============================================================
```

The last line is counted, not assumed. A heartbeat pass before the first tick may have run the
decision and sent an order; the line then reads
`⚠️ Before the refusal: 1 order(s) submitted in this session — check what the venue holds before a
restart.`

The same block appears when the venue cannot be read again at that moment. The session log
carries the reason, the summary reads `Shutdown: emergency (refused at the first tick)`, and the
exit code is `75`, so a supervisor starts it again ([Runtime Model](autotrader_runtime_model.md), *Session
outcome and exit code*).

### Injection

Both paths go through `BarRenderingController.inject_warmup_bars` — the route a backtest's
subprocess uses too. It refuses a bar that is not complete at the instant the history was
selected for, a repeated period and one out of order, as a defect; a mock session gets its bars
from the mount's bar dicts through `deserialize_bars_batch`, a live-adapter session from the
venue's answer.

### Kraken OHLC API

```
GET /0/public/OHLC?pair=XBTUSD&interval=5&since=<unix_ts>
→ {"XBTUSD": [[time, open, high, low, close, vwap, volume, count], ...], "last": <unix_ts>}
```

Public endpoint, no auth. Intervals: 1 (M1), 5 (M5), 15 (M15), 30 (M30), 60 (H1), 240 (H4), 1440
(D1). Returns up to 720 intervals. Each row is stamped with its period's opening time, and `last`
names the newest period the venue has committed — the row after it is the period still forming. A
row is taken only when its period has ended by the instant of the read AND the venue has committed
it. Measured on ETHUSD M1, a period was committed about 0.8 s after it ended on 2026-10-12 and
between 1.5 and 2.2 s after it on 2026-10-13.

Kraken also publishes a row for an interval nobody traded — a flat bar — where the archive has no bar
at all. On a thinly traded symbol (DASHUSD: 187 of its last 721 M5 rows, measured 2026-10-13) the
session warns once per timeframe that its warmup holds such bars; see [Warmup
History](../architecture/warmup_history.md), *Thinly traded symbols*.

### Data Independence

Live-adapter sessions are **fully decoupled from backtesting data**. The tick/bar index
(`BarsIndexManager`, `TickIndexManager`) is never accessed during a live-adapter session:

- **Tick data:** comes from the venue's WebSocket — not from the tick index
- **Warmup bars:** fetched from the broker REST API
- **Symbol specs:** from the broker configuration, not from imported data. For a dynamic broker
  they come from the machine-local runtime cache (`data/runtime/brokers/`), which the session
  refreshes from the venue once the symbol's entry is 7 days old, with the fee structure from the
  tracked seed in `configs/brokers/`; for a static broker the session reads its tracked seed
  whole. The session freezes what it assembled as `broker_config_id` — see [Live Broker Config
  Acquisition](autotrader_venue_integration.md#live-broker-config-acquisition-230)

This means a broker/symbol can run a live-adapter session **without any backtesting data in the
index**. The only requirement is a broker entry in `market_config.json` and a matching OHLC bar
fetcher for warmup.

A mock session is the opposite by design: it replays the index-resolved `scenario_settings` window
through the shared `MountPreparer` — the same index and validation stack a backtest uses — and
takes its warmup bars from that same preparation rather than reading a bar file of its own. It
trades with the tracked seed's symbol specifications whatever the broker's config mode, where a
backtest of a dynamic broker trades with the runtime cache's — so on a dynamic broker the two can
trade under different minimums.
