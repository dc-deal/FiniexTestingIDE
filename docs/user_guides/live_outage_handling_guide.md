# Handling Connection & Feed Outages in Live Trading

How do I react when my bot goes blind? This guide covers the two staleness
domains, the instruments the framework hands you, and the questions only YOU
can answer. The framework's stance: **it enforces that the outage question is
answered — it never answers it for you.**

---

## The Two Staleness Domains

| | SIGNAL feed stale (#434) | Market data stale (#436) |
|---|---|---|
| What died | ONE worker's external feed (e.g. LLM sentiment) | The tick stream itself — the session is blind |
| Scope | Per-worker | Session-level (hits every worker + decision) |
| Detection | Snapshot age vs. `max_staleness_minutes` (tick clock, deterministic) | No real tick for `market_data_stale_after_s` wall seconds (live idle heartbeat) |
| Readable state | `WorkerResult.is_stale` (envelope — delivered with EVERY result, cannot be filtered away) | `trading_api.get_market_data_status()` (`is_stale`, `stale_since`, `seconds_since_last_tick`, `reconnect_count`) |
| Wake-up call | `on_signal_stale(worker_name, source)` — **mandatory when a SIGNAL worker is consumed** | `on_market_data_stale(status)` — **mandatory for EVERY decision logic** |
| Fires | Edge-triggered: once per fresh→stale episode | Edge-triggered: once per episode; recovery = ticks resuming |
| In backtests | Real — driven by the data (archive gaps/ends) or a planned `stale_data_stress` window | Never — replay gaps are data. Only a planned `stale_data_stress` window dispatches it |
| Framework floor | Your fusion logic degrades (e.g. indicator-only mode) | OrderGuard rejects NEW entries while stale (`STALE_MARKET_DATA`); closes/cancels stay allowed |

Indicators cannot be stale relative to their bars — but their bars can be stale
relative to the market. That is why the market-data contract is session-level
and mandatory for everyone.

## Your Instruments

1. **The mandatory hooks — the wake-up call.** They fire ONCE per episode, before
   the decision computes, so you can react in the same pass. An explicit `pass`
   is a valid, conscious answer — but it is YOUR written line, reviewed with your
   strategy. Startup validation rejects a decision logic without the override
   (both pipelines: sim-validated = live-ready).
2. **The readable state — the escalation instrument.** One hook call cannot
   answer "…and what if it is STILL gone after an hour?". The state can:
   `get_market_data_status().seconds_since_last_tick` keeps growing while the
   feed is silent, and `WorkerResult.is_stale` arrives with every result.
3. **`wants_heartbeat()` — acting WITHOUT ticks.** During a market-data outage
   there are no `compute_tick` calls. A logic that opts into the heartbeat
   (`wants_heartbeat() → True`) keeps getting `compute_heartbeat` passes
   (~every 500 ms live) and can escalate on its own timescale.
4. **The OrderGuard floor.** `order_guard.block_stale_market_data` (default
   `true`) rejects new entries while market data is stale — even a `pass`-author
   never opens a position on blind data. Closes and cancels are deliberately
   unaffected (risk-reducing actions stay available).
5. **`request_session_end(reason, severity)`** — the ordered retreat when your
   escalation ladder runs out.
6. **The CONNECTION panel's signal block — the one instrument that is NOT for the
   algo.** Everything above tells your *strategy* what happened; this tells *you*.
   It answers a question the staleness contract cannot: `is_stale` says the signal
   is **old**, the transport block says whether anything is still **arriving**.
   Those come apart in both directions — a healthy transport with a stale signal is
   a quiet producer, while a dead transport with a fresh signal is a session about
   to go blind without noticing. On a multi-week run that distinction is the
   difference between "the market is quiet" and "my feed died an hour ago".

## The Live Signal Transport (#141 Part 2a)

A session gets its signal series in one of two ways, and the panel says which:

| Mode | Where snapshots come from | Panel |
|---|---|---|
| **mounted** | the archive, loaded once at session start | `Signal Feed: mounted (no transport)` |
| **live** | a transport filling the series while the session runs | `Signal Feed: ● live epoch 1 seq 4914` |

A mounted session is a replay: it decides on whatever the archive held at boot and
never learns anything new. That is correct for a backtest or a mock run and wrong
for a bot meant to trade on current sentiment — which is why the panel names the
mode rather than leaving it to be inferred.

**Configuration** lives in `configs/sentiment_config.json`, with your endpoint and
token in `user_configs/`. The tracked file is the source registry (cadence,
staleness default, whether the producer runs continuously); it is the signal side's
mirror of `market_config.json`, and a scenario points at a source with
`data_sentiment_type` exactly as it points at a broker with `broker_type`.

**Arrivals do not wait for a tick.** The inbox is drained on both loop paths, so an
envelope that lands between two ticks reaches the decision in the next pass — on a
quiet instrument the difference is minutes. An arrival that ends an outage also
closes the outage record at the arrival moment, not at the next tick.

**What the tape shows, and what it deliberately does not.** Transport facts only:
connect, an arrival with its position and trigger, a degraded producer, a transport
failure. Never signal values — those have their own panel. One label is worth
knowing about because it reads like a signal and is not:

> `seq 37 · breaking pass` means the **pass** ran out of band, not that a breaking
> signal arrived. Measured on the real archive: roughly two thirds of
> breaking-triggered passes carry no `is_breaking` row at all. The trigger is a
> suspicion raised at ingest; `is_breaking` is the verdict after evaluation. React
> to the verdict.

**A degraded producer is not an outage.** When the producer cannot serve from its
store it says so explicitly, and the transport backs off rather than hammering it.
That shows as `degraded` with a count — distinct from `error`, which is the
transport itself failing.

## The Escalation Ladder (example)

```python
def wants_heartbeat(self) -> bool:
    return True

def on_market_data_stale(self, status: MarketDataStatus) -> None:
    # Wake-up call: acknowledge, surface, start the clock
    self.logger.warning('market blind — entries are guard-blocked, watching age')
    self.emit_event('market data stale', AwarenessLevel.NOTICE, 'market_data_stale')

def compute_heartbeat(self, worker_results):
    status = self.trading_api.get_market_data_status()
    if not status.is_stale:
        return None
    silent_min = status.seconds_since_last_tick / 60.0
    if silent_min > 60:                          # 1h blind → ordered retreat
        self.trading_api.request_session_end('market data dead > 1h')
    elif silent_min > 30:                        # 30 min → reduce exposure
        return self._build_flatten_decision()
    return None                                  # < 30 min → wait it out
```

## The Questions Only You Can Answer

There is no correct default — every answer is wrong for SOME strategy:

- **Flat / reset everything?** Right for tight scalpers; wrong for a swing bot
  that would realize a spread loss over a 2-minute blip.
- **Wait and hope?** Fine — until "…and after an hour, still nothing!?".
  Pair waiting with a timeout (the ladder above).
- **"I have no positions, I don't care."** Almost — you still must not OPEN
  anything while blind. The guard floor covers exactly this one.
- **Cancel resting orders?** Broker-side orders execute without your feed.
  Whether that is protection (SL still fires) or risk (entry fills you cannot
  see) is strategy-specific.
- **Deliberately ignore?** Legal — write `pass` and own it.

## Configuration

| Setting | Home | Default | Meaning |
|---|---|---|---|
| `execution.market_data_stale_after_s` | `app_config.json` → profile `execution` block | `300.0` | No real tick for this many wall seconds → session stale. `0` disables. Tune per pair (a quiet altcoin pauses longer than BTCUSD) |
| `order_guard.block_stale_market_data` | `app_config.json` → profile `order_guard` block | `true` | The entry-block floor |
| `max_staleness_minutes` | per SIGNAL worker (`strategy_config.workers`) | `30` | Snapshot age above which the worker's envelope flags stale |
| `tick_source.connection_check_interval_s` / `connection_dead_s` | profile `tick_source` block | `30` / `90` | TRANSPORT repair knobs (forced WS reconnect) — distinct from the data-quality contract above |

## Drilling Your Reaction (before it happens live)

- **Backtest (deterministic):** planned stale windows via
  `stress_test_config.stale_data_stress` — events block DATA SOURCES the
  scenario binds: carve a signal source (`data_source` = the scenario's
  `data_sentiment_type`) or blind the tick source (`data_source` = its
  `data_broker_type`) at exact timestamps. See the
  [Stress Test System](../stress_test.md).
- **AutoTrader mock — two drills (#438):** for the **market-data** side,
  `tick_source.freeze_after_ticks` + `freeze_duration_s` pause the replay feeder mid-session
  (wall-clock real) — the REAL heartbeat measurement path flips, `on_market_data_stale` fires,
  the guard blocks, recovery follows. For the **signal** side, a
  `scenario_settings.stress_test_config.stale_data_stress` event carves a window out of the
  sentiment series (the same deterministic data-plane carve the sim uses) → the worker goes
  `is_stale` and `on_signal_stale` fires. (The tick status-plane carve stays sim-only → #444.)
- Reference implementations: `CORE/hybrid_sentiment_reference` (hold + surface),
  `CORE/backtesting/backtesting_outage_probe` (the test probe asserting the
  whole chain).

## How an Episode Is Recorded (live)

One observer records the disturbance episodes of the tick stream, and it is the SAME unit in
both pipelines (`MarketDataEpisodeTracker`) — live it simply gets fed from the two event sources
the loop already has:

```
Tick arrives (real feed)                    Heartbeat (no tick, every ~500 ms)
────────────────────────                    ──────────────────────────────────
executor.on_tick(tick)                      _evaluate_market_data_staleness()
_end_market_stale_episode()                   │  age = now − last real tick
  → status fresh, edge reset                  │  > execution.market_data_stale_after_s ?
       │                                      ▼
tracker.on_tick(...)                        tracker.on_heartbeat(...)
  · counts this tick fresh/stale              · OPENS the episode
  · CLOSES the open episode                   · stale_from = the last tick still seen fresh
  · writes the recovery span                  · wall anchor = that tick's wall time
    into the §35 pot                          · counts nothing (no tick happened)
```

The outage is therefore **detected on the heartbeat but dated back to the last healthy tick** —
otherwise every episode would start `market_data_stale_after_s` (default 300 s) too late.

What differs from a simulation or mock run:

| | Live | Mock / Simulation |
|---|---|---|
| Trigger | a real feed outage | a planned window / the freeze drill |
| Origin column | always `live-real` — a real source declares no injection and a live session has no planned windows | `stress-injected` (label from the driver or the join) |
| Time axes | canonical clock **=** wall clock, so span and duration agree | the canonical clock is bimodal in a mock replay (replay tick time vs wall heartbeat) |
| Counting basis | every processed tick (live has no clipping gate — clipping is only measured) | non-clipped algo ticks only |
| Signal domain | not yet present — a live session has no signal series (that is #375); only tick episodes are recorded | both domains |

**During the session** the only live indicators are the `[STALE]` tag on the CONNECTION panel and
the pot warnings in `autotrader_session.log`; the table itself is written at session end. For a
long-running session the in-run snapshot (#392) is what makes it visible earlier.

## Reading What Happened Afterwards

Every run — drill or real — closes with a **📉 FEED STABILITY** section (both pipelines,
rendered only when an episode occurred): one row per source across both domains, with the
stale time, the episode count, the fresh/stale tick counters, and each episode's span.

```
📉 FEED STABILITY
   Source                      Domain      Stale time   Episodes   Origin
   crypto_sentiment_mock       signal          40m 1s          1   🧪 stress-injected
   kraken_spot                 tick                1s          1   🧪 stress-injected
```

Two things to know when reading it:

- **The spans are what the run experienced, not what a drill planned.** A configured
  60-minute signal window shows as ~40 minutes of staleness because the worker's
  `max_staleness_minutes` has to elapse first; a window reaching past the run end shows
  as never recovered (`→ run end`). That difference is the point of the record.
- **The counters and the spans answer different questions.** `72.3% fresh` says how much
  of the run was decided on good data; the spans say whether that was one long outage or
  many short ones — the same ratio, two very different situations to react to.

Above ten episodes per source the console collapses the list to
`N episodes — full list in feed_stability.json`: a session running for weeks accumulates many
short outages, and an unbounded list would bury the per-source summary. Nothing is lost — the
same figures are persisted in full as `io/feed_stability.json` and served by
`GET /api/v1/reports/runs/{run_id}/feed-stability`.

## Footnote: What Is Deliberately NOT Covered

**There is no "worker delivered nothing" outage type.** The worker contract
guarantees a result per declared instance on every pass: indicators compute
from bars, SIGNAL workers always answer with the last snapshot + the `is_stale`
envelope. A worker that fails to produce a result (e.g. a division by zero on
corrupt-but-typed ticks) is a BUG, not an outage — the framework lets it crash
(sim: the scenario fails, the batch continues; live: emergency shutdown with a
prominent cause banner). We error in that case, by design: degrading around
bugs would hide them.
