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
- **AutoTrader mock (wall-clock real):** `tick_source.freeze_after_ticks` +
  `freeze_duration_s` pause the replay feeder mid-session — the REAL heartbeat
  measurement path flips, your hook fires, the guard blocks, recovery follows.
- Reference implementations: `CORE/hybrid_sentiment_reference` (hold + surface),
  `CORE/backtesting/backtesting_outage_probe` (the test probe asserting the
  whole chain).

## Footnote: What Is Deliberately NOT Covered

**There is no "worker delivered nothing" outage type.** The worker contract
guarantees a result per declared instance on every pass: indicators compute
from bars, SIGNAL workers always answer with the last snapshot + the `is_stale`
envelope. A worker that fails to produce a result (e.g. a division by zero on
corrupt-but-typed ticks) is a BUG, not an outage — the framework lets it crash
(sim: the scenario fails, the batch continues; live: emergency shutdown with a
prominent cause banner). We error in that case, by design: degrading around
bugs would hide them.
