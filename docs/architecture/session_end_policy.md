# Session End — What a Run Leaves Behind (#492)

**What happens to resting orders and open positions when a run ends, and why it is two
decisions rather than one.** Layer b (architecture / flow) per #323.

| Item | Value |
|---|---|
| Config block | `session_end` in a profile · defaults in [app_config.json](../../configs/app_config.json) |
| Broker posture | `brokers[].session_end_orders` in [market_config.json](../../configs/market_config.json) |
| Policy resolution | [session_end_validator.py](../../python/framework/validators/session_end_validator.py) |
| Live cleanup | [live_trade_executor.py:1910](../../python/framework/trading_env/live/live_trade_executor.py#L1910) |
| Sim cleanup | [trade_simulator.py:1302](../../python/framework/trading_env/simulation/trade_simulator.py#L1302) |
| Accounting rule | [reporting_pipeline.md](reporting_pipeline.md) — *Realised vs valued* |
| Tests | [session_end_tests.md](../tests/autotrader/session_end_tests.md) |

---

## The map

Three questions decide what a run leaves behind: which order types can **rest** at all, what
the run end **does** with them, and what a **position** even is in this account model.

### 1 · Which order types can rest

`session_end.orders` governs RESTING orders. A MARKET order never rests.

| Order type | AutoTrader **live** (Kraken spot) | AutoTrader **mock** | **Simulation** |
|---|---|---|---|
| MARKET | fills, never rests | fills (instant / delayed) | fills after latency |
| LIMIT | **rests at the venue** | **rests locally** | **rests locally** |
| STOP | **rests at the venue** (#500) | **rests locally** | **rests locally** |
| STOP_LIMIT | **rests at the venue** (#500) | **rests locally** | **rests locally** |

**So `session_end.orders` touches three types in live, and the stop types are the ones that matter most.** Kraken states it themselves: a `stop-loss-limit` "is not automatically linked to a specific position" and has to be cancelled by hand once the position is gone. A stop left standing after the position it protected was closed by another route is a naked order at the venue.

### 2 · What the run end does

| | resting orders | open positions |
|---|---|---|
| **Live, `orders: cancel`** (default) | cancelled AT the venue + `EXPIRED` record **only where the venue CONFIRMED the cancel** — an unconfirmed one is reported into the session error pot and deliberately left unrecorded, because `EXPIRED` is a claim about the VENUE and we did not obtain one (#505). **A protective order (#503) is exempt entirely** | **left open**, reported and valued |
| **Live, `orders: leave`** | left at the venue, and **not** expired locally — an order that can still fill is not finished | left open |
| **Mock** | same code path; the cancel reaches the mock adapter | left open |
| **Simulation** | always expired — there is no venue to leave them at, so `cancel_orders` is accepted only for the shared contract | left open |

### 2b · The one order the policy does not govern (#503)

A **protective order** — the STOP the framework places at the venue for a declared `stop_loss` —
is **left standing at session end whatever `session_end.orders` says**, and logged as such.

The reason is the pair a session can actually start with. `orders: cancel` is the default and
`positions: leave` is the only position policy, so the shipped combination cancels every resting
order and keeps every position. Applied to a protective order that is the exact inversion of its
purpose: the protection would be withdrawn at precisely the moment the bot stops looking, leaving
an open position with a level enforced by a process that has ended.

The warning above still holds and is the other half of it: a stop left standing after its position
is gone is a naked order at the venue. That is why a close by any other route cancels the
protective order FIRST and waits for the confirmation before closing — the orphan is prevented
where it would be created, not cleaned up at the end.

### 3 · What a position IS

| | LONG | SHORT | at the end |
|---|---|---|---|
| **Spot** | owning the base asset (a balance) | **no naked short** — a SELL without the holding is rejected with `INSUFFICIENT_FUNDS` ([abstract_trade_executor.py:791](../../python/framework/trading_env/abstract_trade_executor.py#L791)); a "SHORT" is only the sale of a holding | the asset stays in the account and the **position book remembers** the entry (#355) |
| **Margin** | a real position at the broker | a real position at the broker | the position stays in the market; MT5 returns it on the next boot (#209) |

That last row is why the position book exists only at spot: at margin you **ask** the broker,
while at spot a balance is not a position and nobody but us knows what we paid for it.

---

## Two decisions, not one

What a session does with what it still holds is **two** questions with very different
weight, and one setting used to answer both:

| | Cost of doing it |
|---|---|
| Cancel a resting order | nothing — only a missed fill |
| Close an open position | realises P&L, pays spread and fee |

```json
"session_end": { "orders": "cancel" | "leave", "positions": "close" | "leave" }
```

Defaults: `orders: "cancel"` · `positions: "leave"`.

### Why the third state had to go

Until #492 the cleanup did neither of the two for positions — it closed them **in our book
only**. It built a synthetic close order and filled it locally, so:

```
session end, one open spot position
 ├─ resting orders  cancelled            → reaches the venue
 ├─ open position   "direct-closed"      → OUR BOOK ONLY
 └─ result          portfolio flat, P&L booked as if sold at bid,
                    the coin still sitting in the account
```

At spot it also moved `base → quote` in the balance ledger, so the summary printed
`BTC 0.0` and a raised USD balance while the coin was at the broker. The exit was marked
(`close_reason='scenario_end'`) but fully **counted** — its P&L entered net profit, the win
rate, the profit factor and the cross-run ledger.

### What the established systems do

No professional system sells because a process ended. For **orders** there is a standard for
exactly this — *Time in Force* (`DAY`/`GTC`/`IOC`) plus venue-side Cancel-on-Disconnect — and
for **positions** there deliberately is none: a position belongs to the ACCOUNT, not to the
process. Even the kill switch that market-access rules require cancels orders and blocks new
ones; it does not flatten. nautilus_trader answers a restart with reconciliation at start
rather than a flatten at stop; MetaTrader leaves both positions and pending orders untouched
when an EA stops; LEAN, backtrader and zipline all mark an open position to market at the end
of a backtest and never record it as a closed trade.

The accounting norm behind all of them: **realised and unrealised P&L are separated, and a
position open at the period end is valued, never recorded as a completed trade.**

### Where flattening does belong

It exists — intraday mandates with "no overnight exposure" — but the rule sits elsewhere:

```
market norm     "flat by 15:55"  = a STRATEGY rule against MARKET TIME,
                  real orders during the session, in liquidity, supervised
not the norm    a sale triggered by the process ending
```

A market order fired by a `SIGTERM` in a thin moment is what risk controls exist to prevent,
and it fires while the operator is closing the window. A forex strategy that needs "not over
the weekend" puts that in its decision logic during the session. For crypto spot there is no
session at all (24/7), and "flat" is not even an account state — holding the coin *is* the
position.

### `positions: "close"` is declared, not built

It refuses at startup, naming #487. A close that really reaches the venue is an
**asynchronous** order: `close_position()` registers a pending close and enqueues a job, and
the fill arrives on the *next tick*. At session end the tick source is already stopped and
the request worker dies in the same cleanup — so a real close needs a synchronous drain with
a timeout plus an answer for "the timeout expired", which is the unresolved-write resolution
(#487).

### The incoherent pair

Giving the operator control is right; letting the settings be incoherent is not. One pair
fires at 03:14 and is invisible while writing the config:

```
session_end.orders       = "leave"      → orders remain at the venue
cold_start.adoption_mode = "operator_confirm"   (and nobody declared present)
→ the next boot REFUSES, because it cannot confirm what it found
→ orders sit at a venue and the bot meant to manage them will not start
```

`session_end_validator.py` refuses that combination at startup with both settings named —
**unless** the decision logic has an `on_cold_start` that accounts for its inherited orders
(#493), which lifts exactly that refusal. It also refuses `orders: "leave"` with cold start
disabled, where nothing would ever adopt them back.

### The loosening asymmetry

`orders: "leave"` is the LOOSENING value — afterwards orders sit at a venue with nobody
watching — and a profile is the most easily copied file in the project. A profile may
therefore always TIGHTEN to `cancel` and may choose `leave` only when the broker's own
posture allows it (`market_config.json::brokers[].session_end_orders`). Attempting to loosen
against it raises rather than being silently ignored, the same way `dry_run` does. The
`positions` axis carries no such gate: `leave` is the default and the market norm, so there
is nothing to loosen.

### What the report shows

The portfolio section carries the positions that stayed open, each with its mark
(`last_price`, `unrealized_pnl`) or an explicit *not valued* where no tick ever arrived, plus
`final_equity` beside the realised `net_profit` — and the policy the session ran under, so a
position left standing cannot be mistaken for one that went missing. Trade statistics count
**completed** trades only.

It appears in the **headline**, not only in the portfolio section: the closing block prints
`Still open: N position(s) (… unrealised) · policy cancel/leave` beside the balance, and the
balance itself is labelled `realised`. The headline is what an operator reads first, so a
figure there that describes a flat account which is not flat is the one place this must not
happen. The sim executive block carries the same pair (`Open at end` / `Final Equity`).

`check_clean_shutdown` knows the policy: a position the policy allows to stay is a note; one
that survives where flatness was expected is still an ERROR and still called *orphaned*.
Without that, every clean session end under `positions: "leave"` would have graded
`FINISHED_WITH_ERRORS` and exited 3.

### Why there is no warning for it

An open position at session end is the **declared normal outcome**, so it is deliberately not
a Tier-1 warning — a warning that fires in normal operation stops being read
([warnings_errors_tiers.md](warnings_errors_tiers.md)). The three cases are covered by three
different instruments, and only one of them is an advisory:

| Case | Instrument |
|---|---|
| left open **by policy** | reported and valued, in the headline and the portfolio section |
| survives where flatness was **expected** | `check_clean_shutdown(expect_flat=True)` → **ERROR** |
| the **carry-over write failed** while holding it | **ERROR** in the session channel, naming how many positions the successor will not know about |

The third is the only one where the run is not what it appears to be — and it is an error
rather than a warning, which is stronger and needs no second channel.

---

## The stop branch — closed 2026-09-07 (#500)

This section used to record a known limit, and it is worth keeping as a record of the shape rather
than deleting: the LIVE cleanup reached `_active_stop_orders` only by accident. The shared expiry
helper always covered both lists, but the live path called it INSIDE the branch that handles resting
LIMIT orders — and the guard on both branches was the limit list's own truthiness. So a stop resting
with an empty limit list was neither cancelled at the venue nor expired locally nor logged, and one
resting beside a limit order was expired as a side effect rather than by decision.

It was documented as harmless because the live executor refused STOP and STOP_LIMIT outright. That
was true of the SUBMIT path and false of the situation: boot adoption (#355) files a venue-reported
resting order into the shadow, and its own type filter admits STOP and STOP_LIMIT deliberately. So
the list could already fill without any submit.

Both halves are fixed. `finish_remaining_orders` builds one resting list from both worlds and its
guard reads that union, so the policy applies to a stop exactly as it does to a limit — cancelled at
the venue, or left standing and not expired locally either. The lesson generalises past this branch:
a guard written on one of two collections is a guard that reports nothing when only the other one is
populated.

## Code anchors

| What | Where |
|---|---|
| The config block | [autotrader_defaults_config_types.py:188](../../python/framework/types/config_types/autotrader_defaults_config_types.py#L188) |
| The broker posture | [market_config_types.py:107](../../python/framework/types/config_types/market_config_types.py#L107) · [market_config_manager.py:216](../../python/configuration/market_config_manager.py#L216) |
| Policy resolution + the three refusals | [session_end_validator.py:36](../../python/framework/validators/session_end_validator.py#L36) |
| The incoherent pair | [session_end_validator.py:80](../../python/framework/validators/session_end_validator.py#L80) |
| Resolved at startup, before anything is touched | [autotrader_main.py:347](../../python/framework/autotrader/autotrader_main.py#L347) |
| The shutdown call site | [autotrader_main.py:615](../../python/framework/autotrader/autotrader_main.py#L615) |
| The contract | [abstract_trade_executor.py:1726](../../python/framework/trading_env/abstract_trade_executor.py#L1726) |
| `check_clean_shutdown(expect_flat)` | [abstract_trade_executor.py:1756](../../python/framework/trading_env/abstract_trade_executor.py#L1756) |
| The open-position report row | [report_types.py:164](../../python/framework/types/api/report_types.py#L164) |
| The equity curve, one scale for both writers | [portfolio_manager.py:1138](../../python/framework/trading_env/portfolio_manager.py#L1138) |
| The adoption prompt that states this policy | [cold_start_adopter.py:811](../../python/framework/autotrader/cold_start_adopter.py#L811) |

## Related

- [autotrader_architecture.md](../autotrader/autotrader_architecture.md) — the session lifecycle this sits in
- [architecture_execution_layer.md](architecture_execution_layer.md) — the cleanup mechanic itself
- [live_execution_architecture.md](live_execution_architecture.md) — cold start, and why the two are a pair
- [data_storage_layout.md](data_storage_layout.md) — why the carry-over is written before the cleanup
- [reporting_pipeline.md](reporting_pipeline.md) — realised vs valued, and the spot equity trap
