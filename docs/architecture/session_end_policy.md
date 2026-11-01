# Session End — What a Run Leaves Behind (#492)

**What happens to resting orders and open positions when a run ends, and why it is two
decisions rather than one.** Layer b (architecture / flow) per #323.

| Item | Value |
|---|---|
| Config block | `session_end` in a profile · defaults in [app_config.json](../../configs/app_config.json) |
| Broker posture | `brokers[].session_end_orders` in [market_config.json](../../configs/market_config.json) |
| Policy resolution | [session_end_validator.py](../../python/framework/validators/session_end_validator.py) |
| Live cleanup | [live_trade_executor.py:4020](../../python/framework/trading_env/live/live_trade_executor.py#L4020) |
| Sim cleanup | [trade_simulator.py:1440](../../python/framework/trading_env/simulation/trade_simulator.py#L1440) |
| A stop's wait for orders on their way | [autotrader_tick_loop.py:760](../../python/framework/autotrader/autotrader_tick_loop.py#L760) |
| Its settings | `autotrader.shutdown` in [app_config.json](../../configs/app_config.json) |
| Accounting rule | [reporting_pipeline.md](reporting_pipeline.md) — *Realised vs valued* |
| Tests | [session_end_tests.md](../tests/autotrader/session_end_tests.md) |

---

## The map

Three questions decide what a run leaves behind: which order types can **rest** at all, what
the run end **does** with them, and what a **position** even is in this account model.

### 1 · Which order types can rest

`session_end.orders` governs RESTING orders. A MARKET order never rests.

| Order type | AutoTrader **live adapter** (Kraken spot) | AutoTrader **mock** | **Simulation** |
|---|---|---|---|
| MARKET | fills, never rests | fills (instant / delayed) | fills after latency |
| LIMIT | **rests at the venue** — in a dry run, in `DryRunOrderSimulator` | refused at pre-flight | **rests locally** |
| STOP | **rests at the venue** (#500) — in a dry run, in `DryRunOrderSimulator` | refused at pre-flight | **rests locally** |
| STOP_LIMIT | **rests at the venue** (#500) — in a dry run, in `DryRunOrderSimulator` | refused at pre-flight | **rests locally** |

The mock adapter declares market orders only (`MockBrokerAdapter.get_order_capabilities()`), so a
decision logic that requires a resting type is refused before a mock session starts.

**So `session_end.orders` touches three types in a live-adapter session, and the stop types are the ones that matter most.**
Kraken states it themselves: a `stop-loss-limit` "is not automatically linked to a specific
position" and has to be cancelled by hand once the position is gone. A stop left standing after the
position it protected was closed by another route is a naked order at the venue.

### 2 · What the run end does

| | resting orders | open positions |
|---|---|---|
| **Live adapter, `orders: cancel`** (default) | cancelled AT the venue (in a dry run: in the `DryRunOrderSimulator`, which stands where the venue would) + `EXPIRED` record **only where the venue CONFIRMED the cancel** — an unconfirmed one is reported into the session error pot and deliberately left unrecorded, because `EXPIRED` is a claim about the VENUE and we did not obtain one (#505). | **left open**, reported and valued |
| **Live adapter, `orders: leave`** | left at the venue, and **not** expired locally — an order that can still fill is not finished | left open |
| **Mock** | same code path, with nothing to cancel — no resting type passes pre-flight | left open |
| **Simulation** | always expired — there is no venue to leave them at, so `cancel_orders` is accepted only for the shared contract | left open |

An order still **on its way** when the run ends rests nowhere yet and is none of these: see
[Orders on their way](#orders-on-their-way--a-normal-stop-waits-for-them-630).

### 3 · What a position IS

| | LONG | SHORT | at the end |
|---|---|---|---|
| **Spot** | owning the base asset (a balance) | **no naked short** — a SELL without the holding is rejected with `INSUFFICIENT_FUNDS` ([abstract_trade_executor.py:1810](../../python/framework/trading_env/abstract_trade_executor.py#L1810)); a "SHORT" is only the sale of a holding | the asset stays in the account and the **position book remembers** the entry (#355) |
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
rather than a flatten at stop; MetaTrader leaves both positions and resting orders untouched
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
(#487). The wait for orders on their way does not change that: it hears a close the strategy already
sent, and nothing there sends one.

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

## Orders on their way — a normal stop waits for them (#630)

`session_end.orders` governs what rests. An order sent a moment ago rests nowhere: it is on its way,
and a stop used not to wait for it. The loop ended, the carry-over for the next session was written,
and the cleanup booked the order as unaccounted. Nothing read the answers after the loop, so one
that arrived, or had already arrived, was read by nobody. Reproduced on a mock venue in both account
models (2026-10-30), with the answer held back and with it already waiting:

```
                without the wait             with the wait
venue           FILLED 0.01 at 60001         FILLED 0.01 at 60001
our record      unaccounted, no position     executed, position 0.01, fee booked
balance         unchanged                    moved
log             a warning, no error          nothing
```

At spot the next session then started from a carry-over that said flat, read no balance and alerted
nothing: the strategy saw no position and could buy again.

### What waits, and for what

The wait sits in the loop's `run`, after its last pass and before the session-end event. That is the
last moment a hook can still hear a fill, and the loop owns the day boundary, the sealed days, the
log rotation and the carry-over watcher.

| How the session ends | Waits | Why |
|---|---|---|
| SIGTERM, a first Ctrl+C, a strategy's normal end | yes | a session-end request of normal severity |
| A hard stop | no | it asks at EMERGENCY severity and has a drain of its own |
| A crash in the loop | no | it leaves the loop by exception |
| A second Ctrl+C | no | it exits |
| A tick source that ran out | no | nobody asked for the end, so nothing waits. An order still on its way at the end of the input is booked unaccounted in the cleanup with an ERROR, which grades a replayed mock session exit 3 where the simulation expires the same order |

It waits for every order in the request processor's pipeline, where a market open and a market close
both live, and every resting order with an operation in flight or no reference yet. With nothing on
its way it costs no pass and no line. A resting order the venue confirmed is not on its way: that is
the policy's, above. Nor is an order at the resolution ceiling: nothing asks about it any more, so a
wait for it would only run out, and the cleanup books it unaccounted with an ERROR that says so.

**And once more after the session-end event.** A strategy may close on its way out, from
`on_session_end`. Such a close is waited for the same way, so its fill is booked before the cleanup,
and both waits keep to one bound: the second goes on from the time the first spent, and does not
run at all once the first ran out, because the worker stopped with it. The logic does not hear what
this second wait settles: the session-end event stays the last thing it is told. An open and its
close carry the same id, so the stop tells its orders apart by id and action.

### One wait pass

What an idle pass does for the orders, and nothing for the algo: the liveness stamp, so the monitor
stays quiet, the clock, the day boundary, the sealed days, the log rotation, `heartbeat()`, the
decision events, the carry-over watcher. No tick, no ghost pass, no signal inbox, no reconcile, no
state frame. No write is repeated, and a write whose answer did not come stays unknown.

- **Its clock is the last event plus the monotonic time elapsed.** The wall clock of an idle pass
  would seal one booking period per day up to today in a replayed mock session. A fill the wait
  books after a midnight belongs to the day it opened.
- **Its bound is read again on every pass,** because what a declared kill leaves shrinks as the wait
  runs. A pass can overrun it, because the reads inside `heartbeat()` are synchronous. The fill timeout
  does not act once a stop is under way: it counts from the send, so it would come first and book the
  order with a warning and exit 0, with the worker still sending what is queued.
- **New orders are not held back.** A hook may still send one, and the same bound then covers it.
  Holding them is the wind-down's gate, which is not built, so the display says `decisions stopped`,
  not `no new orders`.

### When the bound runs out

`give_up_on_awaited_orders` runs before the session-end event, so the logic hears the outcome. The
request worker stops first, so nothing queued goes out, and what it delivered is read once more.
Then the road the fill timeout takes: an order with a reference is asked about, because a final
answer settles it, an executed part included, and one still working is cancelled. What stays open
ends `unaccounted` with `end_reason` `session_end`, an ERROR per order on the session channel and
one alert, so the session exits 3. A resting order on its way stays for the cleanup, which cancels
or leaves it by the policy.

- **An executed part the venue named is booked.** Where its last answer shows part of the order
  executed and nobody asks again, that part is the order's fill, as for an order the venue ended
  after a part; the rest ends unaccounted, a step after the fill, and the ERROR names both. The
  closing message counts such an order as `filled in part`.
- **An order that never left is withdrawn.** A submit still queued when the worker stops, or one a
  hook sends after it stopped, never reached the venue. It ends `cancelled` by the framework with
  `end_reason` `session_end`, a WARNING says it never left this process, and the logic hears it as a
  cancel: nothing about it is unknown, so it is neither unaccounted nor an ERROR.

What the operator reads when that happens, and what to do, is in
[Unattended Operation](../autotrader/unattended_operation.md#a-stop--what-it-waits-for-and-the-kill-it-has-to-fit-under).

### The cleanup, and the carry-over written twice

`finish_remaining_orders` stops the worker, reads its answers and withdraws what it never sent first,
so a queued submit cannot go out after its order was booked. Its second phase, which books what is
left in the pipeline, serves only the ends that do not wait, and each such order gets an ERROR, where
it used to be a warning and exit 0. Under `orders: "leave"` a resting order with no reference cannot
be left standing by name: its submit went out unanswered, so it is booked unaccounted with an ERROR
rather than counted as open at the venue.

`_shutdown` writes the carry-over before the cleanup, because that is the state it describes, and
again after it, because a fill the cleanup found, on a read after a refused cancel, moved the book.
**At margin the carry-over holds no book:** such a fill reaches the report and not the next session,
which reads its positions from the venue (#209).

Under a declared kill every venue call of the cleanup, and each part of the end read, asks the
budget first. The read at the START is untouched: a session that has not begun has no kill to fit
under.

**Not closed by this:** a crash in the loop and a kill leave the state the table above shows.
Finding such a fill at the next boot by its client key is not built.

### What others do (read 2026-10-30)

Nautilus Trader waits a fixed time for residual events after a stop (10 s) and by default settles an
order it cannot resolve locally, as rejected. Hummingbot cancels its limit orders under a 20 s
constant without checking the result. Freqtrade leaves orders standing, with a cancel as an option.
systemd, Docker and Kubernetes send a signal and kill after a period, and none tells the process how
long it has. Nygard, *Release It!* (ch. 14), and Beyer et al., *Site Reliability Engineering* (ch.
20, the lame duck state), give the shape this follows: a named state, no new work, finish what is in
flight, bound it. Kleppmann, *Designing Data-Intensive Applications* (ch. 4 and 8): a request that
timed out has an unknown outcome, not a failed one, which is why it ends `unaccounted` and never
`rejected`.

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
| The config block | [autotrader_defaults_config_types.py:274](../../python/framework/types/config_types/autotrader_defaults_config_types.py#L274) |
| The broker posture | [market_config_types.py:174](../../python/framework/types/config_types/market_config_types.py#L174) · [market_config_manager.py:285](../../python/configuration/market_config_manager.py#L285) |
| Policy resolution + the three refusals | [session_end_validator.py:36](../../python/framework/validators/session_end_validator.py#L36) |
| The incoherent pair | [session_end_validator.py:80](../../python/framework/validators/session_end_validator.py#L80) |
| Resolved at startup, before anything is touched | [autotrader_main.py:614](../../python/framework/autotrader/autotrader_main.py#L614) |
| The shutdown call site | [autotrader_main.py:1447](../../python/framework/autotrader/autotrader_main.py#L1447) |
| The contract | [abstract_trade_executor.py:2889](../../python/framework/trading_env/abstract_trade_executor.py#L2889) |
| `check_clean_shutdown(expect_flat)` | [abstract_trade_executor.py:2918](../../python/framework/trading_env/abstract_trade_executor.py#L2918) |
| The open-position report row | [report_types.py:545](../../python/framework/types/api/report_types.py#L545) |
| The equity curve, one scale for both writers | [portfolio_manager.py:1380](../../python/framework/trading_env/portfolio_manager.py#L1380) |
| The adoption prompt that states this policy | [cold_start_adopter.py:1005](../../python/framework/autotrader/cold_start/cold_start_adopter.py#L1005) |
| A stop's wait and one pass of it | [autotrader_tick_loop.py:760](../../python/framework/autotrader/autotrader_tick_loop.py#L760) · [autotrader_tick_loop.py:824](../../python/framework/autotrader/autotrader_tick_loop.py#L824) |
| What it waits for | [live_trade_executor.py:3810](../../python/framework/trading_env/live/live_trade_executor.py#L3810) |
| A bound that runs out | [live_trade_executor.py:3894](../../python/framework/trading_env/live/live_trade_executor.py#L3894) |
| The budget, and the check at the start | [stop_budget.py:16](../../python/framework/autotrader/stop_budget.py#L16) · [stop_budget_validator.py:31](../../python/framework/validators/stop_budget_validator.py#L31) |

## Related

- [autotrader_runtime_model.md](../autotrader/autotrader_runtime_model.md) — the session lifecycle this sits in
- [architecture_execution_layer.md](architecture_execution_layer.md) — the cleanup mechanic itself
- [live_execution_architecture.md](live_execution_architecture.md) — cold start, and why the two are a pair
- [data_storage_layout.md](data_storage_layout.md) — why the carry-over is written before the cleanup,
  and again after it
- [unattended_operation.md](../autotrader/unattended_operation.md#a-stop--what-it-waits-for-and-the-kill-it-has-to-fit-under) — what a stop sets, shows and leaves to do
- [reporting_pipeline.md](reporting_pipeline.md) — realised vs valued, and the spot equity trap
