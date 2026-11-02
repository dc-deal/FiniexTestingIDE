# AutoTrader Capital and Safety

This is the half of the AutoTrader where a defect costs money directly. What the bot may spend,
what the venue has already reserved against that, who enforces a stop when the process is not
running, and what ends a session that is losing.

The one sentence that organises all of it: **a venue reserves at PLACEMENT, our balances move at
FILL.** Everything below follows from the gap between those two instants.

**Not here:** how an order reaches the venue — `docs/architecture/live_execution_architecture.md`.
What a venue holds for a position — `docs/architecture/venue_order_features.md`. The session-end
policy — `docs/architecture/session_end_policy.md`.

## Capital — what the bot may spend (#489)

The framework hands the bot the account's balances and lets it trade against them. Two things
sit between "the account holds this" and "the bot may spend this", and both are on the SHARED
executor, so simulation and the AutoTrader answer them identically.

### Committed funds — a venue reserves at PLACEMENT, our balances move at FILL

`PortfolioManager` moves a balance when an order FILLS. A venue holds the asset the moment the
order is PLACED. Without a committed figure the funds check therefore reads a balance that two
unfilled orders can each spend in full:

```
account            1000.00 USD
resting LIMIT BUY   0.012 BTC @ 49000  →  claims 588.94 USD (notional + maker fee)
market BUY          0.010 BTC @ 50001  →  needs  501.31 USD

before #489   both pass — neither knows about the other's reserve, and the VENUE
              refuses the second one for insufficient funds
after  #489   the second is refused at submission:
              available 411.06 = balance 1000.00 − committed 588.94
```

`AbstractTradeExecutor.get_committed_funds(currency, exclude_order_id=None)` derives the claim
on demand from the executor's own resting orders plus the ones still in transit
(`get_pipeline_orders()` — the list form of `has_pipeline_orders()`, because an order in transit
already claims funds). Nothing is stored: the claim is a running fact about the order book, and
a cache would need invalidating on every fill, cancel and modify.

It mirrors the fill's OUTFLOW exactly, because an inflow needs no reserve:

| Unfilled order | Spends | Amount |
|---|---|---|
| OPEN LONG | quote | `lots × price + fee` |
| OPEN SHORT | base | `lots` |
| CLOSE of a LONG | base | `close_lots` |
| CLOSE of a SHORT | quote | `close_lots × price + fee` |

Fee and tick value come from the same helpers the fill uses (`_create_entry_fee` /
`_create_exit_fee`, `_calculate_tick_value`), so a reserve and the eventual charge cannot
drift apart. That holds because a close is a MARKET order today and therefore always a taker;
an exit resting as a LIMIT would be reserved at one rate and charged at another unless the
value comes from the closing order. The price is
the one the order will actually pay — a LIMIT its own, a STOP its trigger, a MARKET in transit
the current ask — and an order nothing can price yet reserves **nothing** rather than a guessed
number.

**Two check sites, both net of committed.** The fill-time check passes `exclude_order_id`,
because the order being filled is still in its own collection while the fill runs and counting
it there would reserve it twice. The submission-time check runs in `open_order()` after the
lot-size and tradeable validations — a malformed order must be rejected as malformed, not as
underfunded — and its refusal is **returned**, not announced: `_notify_outcome` fires only from
the asynchronous resolution paths, so a bot learns of a submission refusal from the
`OrderResult` it already holds, in the same call. A decision logic that reads only
`status == PENDING` would miss it.

**MARGIN is out of scope and it is not a mirror.** Free margin ignores unfilled orders too, but
a CLOSE order RELEASES margin instead of spending it, so reusing the table above would
double-charge every in-flight close. Filed with its three decisions in #209.

### Minimum-order sufficiency — a boot refusal

A bot whose account can fund no order at all has nothing to do, and it says so at boot rather
than at its first signal. `check_account_sufficiency` (`framework/validators/capital_validator.py`)
refuses when the account can NEITHER buy NOR sell one `volume_min`:

```
volume_min 0.00005 BTC · price 104350.00
  → a minimum BUY costs 5.22 USD    · a minimum SELL needs 0.00005 BTC
  → refuse only when quote < 5.22 AND base < 0.00005
```

The AND is deliberate: a spot bot may legitimately start holding only the BASE asset and open by
selling — the Field Study funds both sides on purpose — so an empty quote balance alone is not a
reason to refuse.

A venue's minimum order VALUE (`cost_min`, Kraken's `costmin`) raises the smallest order to
`cost_min / price` wherever that is larger than `volume_min`, on both sides. It needs a price, so
without one the sell side is judged on `volume_min` alone.

**Where it sits, and why not earlier.** The check runs after warmup (Phase 9), because that is
the first point a price exists: no tick has arrived during startup, and the adapter contract
carries no price read at all. The reference price is the newest warmup bar close. A strategy
with no bar workers produces no bars, and then the BUY side cannot be judged — that is **said**
into the session channel rather than swallowed, because a check that silently does not run reads
exactly like one that ran and passed.

### Whose account is it — a declaration, not an inference

Every account-level risk limit (#356 baseline, #314 drawdown cap) measures against account
equity. That is only the *bot's own* denominator if the account is only the bot's — and a
framework cannot find that out by asking: a balance carries no owner tag, so "these coins are
mine" is a belief no venue query settles. An ORDER is different, and that asymmetry is the whole
model: it carries the client order id this bot minted, so ownership is a fact (#355 adopts on
it).

Why it matters is a denominator, and one example shows it. Two actors trade ETHUSD on one
account, each having brought 1000 USD; the bot's `max_drawdown_pct` is 20 %, measured against
the account:

```
account 2000 USD                        bot's limit: −20 % of 2000 = −400

the OTHER actor loses 500 of their 1000  → account 2000 → 1500 = −25 %   breaker FIRES
                                            although the bot did nothing
the BOT loses 300 of its own 1000 (−30 %) → account 2000 → 1700 = −15 %   breaker SILENT
                                            although it should have fired
```

Wrong in both directions, because the denominator is not the bot's. Other assets on the account
— DASH, EUR, anything the bot does not trade — are irrelevant to this; what matters is a second
actor on the **same pair**.

So exclusivity is **declared**:

```json
"capital": { "exclusive_account": false }
```

Default `false`, because the shared account is what every existing profile assumes and because
the safe direction for a premise nobody stated is to assume less. Declaring `true` turns the
premise into something the boot can **check** — ordinary fail-fast engineering on a stated
contract, which is honestly describable in a run record.

What the check does is graded by **consequence**, not by category:

| What the venue reports | Response | Why |
|---|---|---|
| An order with no key of ours, **on the instrument this bot trades** | **ERROR into the session pot** — the session starts | At spot both exposures merge into one balance no tag can split, so the bot cannot size an order against what is actually its own, and every risk limit measures a foreign denominator. The loudest case, and still not a refusal — see below |
| An order that is not ours **on another instrument** | Tier-1 warning, session starts | It means the DECLARATION is wrong, which is worth an alert but is a lesser weight: the exposures do not merge |
| An order of **our own key shape** whose session we cannot place | Error, no judgement | On an exclusive account nobody else uses our key format, so it is more likely OURS. Refusing forever would leave the operator no way out, which is the reason already written into the boot step |

**Nothing here refuses the boot, and that is deliberate.** A refusal is not the safe outcome:

- It leaves a state that is not "flat and safe" but **exposed and unmanaged**. This bot may hold
  a spot position and a resting order, and a refusal abandons both at the venue with nothing
  reconciling them — and its stop and target live in the very process that declined to start.
- It can **deadlock**: two sibling bots on one account each see the other's order, both refuse,
  neither runs. The condition is symmetric and nothing outside resolves it.
- It can **loop**: a refused boot leaves the carry-over untouched and exits non-zero, so a
  supervisor relaunches into the same refusal.

**What the declaration does TODAY — observation only.** Nothing in trading changes when it is
set: the bot spends the whole account either way, exactly as before. What changes is that the
boot now *checks* the premise and *says* when it does not hold. The declaration becomes
load-bearing when the account-level limits (#314, #356) are built — they measure against the
account, and this flag is what makes "the account's drawdown" mean "the bot's drawdown".

**How a break is detected.** At boot, the cold-start step pulls the venue's open-order list — it
is account-wide — and asks of every resting order: does it carry a client order id of *our* shape?
Balances cannot answer who owns them; orders can. An order with no key of ours is therefore
*proof* of a second actor, not a suspicion. The check only runs when exclusivity was declared,
and it can only see an actor who happens to have an order resting at that moment.

**What follows.** The ERROR lands in the session pot, so it reaches the end-of-session
summary — and the run is graded `FINISHED_WITH_ERRORS`, **exit code 3**, which a supervisor,
a cron job or alerting (#235) can read. The message names the foreign order's venue reference
and both ways out: cancel it at the venue, or set the declaration to `false` and accept that the
limits measure a shared denominator. **The bot does not stop trading** — it keeps running against
a denominator it now knows is wrong. That gap is #499's.

**#499** is the answer that withholds new RISK instead of refusing to run — start, restore the
book, adopt what is ours, keep reconciling and watching the equity, place nothing new until an
operator clears it. It enters #349's `HALT_TRADING` state rather than defining a second one, so
it waits for that mechanism. How the operator *clears* it is not designed yet: #349 states that
resuming requires explicit confirmation, not through which channel — and for an unattended run
that channel is #235's.

**Why the market has no template for this.** No established framework refuses to boot over an
unrecognised order — and not out of laxity: each of them removed the precondition instead.
nautilus puts every strategy in one node with one portfolio, so a sibling's order is a
colleague's; freqtrade and Hummingbot declare a capital share; LEAN asserts the account is the
algorithm's and adopts what it finds; institutions isolate at the venue. A single-symbol,
single-process bot on one spot account has none of those four escapes, which is what makes a
declared precondition defensible here — and why the semantics are argued on their own terms
rather than borrowed.

## Stops and targets — the logic's own

A session holds no stop or target: Kraken's API takes none for a position, and the framework does not
rebuild what a venue lacks. A logic keeps its levels itself and closes when the market reaches one, so
a position is unprotected while the process is not running. The contract, and what a run refuses, are
in [venue_order_features.md](../architecture/venue_order_features.md).

## Safety Circuit Breaker

A soft-stop mechanism: while a configured risk threshold is exceeded, the tick loop replaces the decision's ACTION with FLAT. Whatever a strategy sends only on a BUY or SELL action is suppressed — an entry, and also a close or a reduction it expresses as an action. What a strategy sends from its own position management whatever the action — a close, a stop amend, a cancel, a re-price — still goes out, an order already resting can still fill, and the a logic's own exits keep working. The breaker closes nothing itself. A strategy that reduces by deciding SELL therefore keeps its full exposure for as long as the block holds.

### Behavior

```
Tick rein
  → executor.on_tick()    ← prices, and the answers of the orders in flight
  → Workers → Decision    ← produces BUY / SELL / FLAT
  → [SAFETY CHECK]        ← evaluates thresholds against the account value (spot: the balances, holdings at mid; margin: balance + unrealised P&L)
  → if blocked: decision.action = FLAT  ← override: no order from the action, an action-driven close included
  → execute_decision()
```

The check runs after every tick. If conditions clear (e.g. the account value recovers above the threshold), the block is automatically lifted and trading resumes.

### Configuration

```json
"safety": {
  "enabled": true,
  "min_balance": 500.0,
  "min_equity": 9.0,
  "max_drawdown_pct": 20.0,
  "max_drawdown_abs": 0.0,
  "max_daily_loss_abs": 0.0,
  "max_daily_loss_pct": 0.0,
  "baseline_mode": "fixed",
  "persist_baseline": true,
  "emergency_flatten_enabled": false,
  "max_drawdown_pct_hard": 0.0,
  "max_drawdown_abs_hard": 0.0,
  "spot_liquidate_to_quote": false
}
```

Every threshold defaults to `0.0` / `false` — an existing profile needs no migration, and a limit
set to `0.0` is off while its siblings keep working.

**The soft block** — replaces the decision's action with FLAT, an action-driven close included, and closes nothing itself:

| Field | Description |
|---|---|
| `enabled` | Master switch — omit or set `false` to disable entirely |
| `min_balance` | Block if the ACCOUNT VALUE falls below this floor (margin profiles write this key) |
| `min_equity` | The same floor, spelled for spot profiles |
| `max_drawdown_pct` | Block if the session drawdown exceeds this share of the risk baseline |
| `max_drawdown_abs` | The same limit as an amount (#314). Independent — either can fire first: a percentage auto-scales with the account, an absolute floor is what stops that percentage from meaning a dangerously large number once the account has grown |
| `max_daily_loss_abs` | Block if the loss since the day's start exceeds this amount (#314) |
| `max_daily_loss_pct` | The same daily limit as a share of the day-start value |

Since #356 both floors denominate the SAME quantity — the account value — and the account model
only decides which key a profile writes. On margin it used to be settled cash, which by
construction moves only on realised P&L, so an open loss could not reach the floor at all.

**The baseline** — the denominator every drawdown figure is measured against:

| Field | Description |
|---|---|
| `baseline_mode` | `fixed` holds the value at deployment; `high_water_mark` trails the peak and therefore RATCHETS — profit tightens the limit, which is the prop-firm convention and a deliberate choice rather than a default |
| `persist_baseline` | Whether it survives a restart. `true` is the point of #356: a restart mid-drawdown would otherwise re-anchor at the drawn-down value and forget the accumulated loss. `false` is the deliberate escape for a profile that wants a fresh reference on every start |

**The hard stop** — CLOSES what is open, then ends the session. From the moment it fires it also
blocks new entries, with or without a soft threshold configured: its closes take ticks to drain,
and an entry in between would keep the book from ever reaching flat.

| Field | Description |
|---|---|
| `emergency_flatten_enabled` | Master switch. Default `false`, because turning it on changes what happens to real money |
| `max_drawdown_pct_hard` | Drawdown share at which everything is closed |
| `max_drawdown_abs_hard` | The same as an amount |
| `spot_liquidate_to_quote` | Whether the hard stop also SELLS a spot holding. Default `false`, and the asymmetry is real rather than timid: a margin position can lose more than the account holds, so liquidating it is the point; a spot holding cannot, so selling it converts an unrealised loss into a realised one — a trading decision, not a safety one |

All conditions are OR-combined — any one alone triggers, and every condition that fired is named
in the reason, so a blocked session says whether one limit was touched or three were blown through.

### Where the daily loss limit's day ends

The day of `max_daily_loss_abs` and `max_daily_loss_pct` is the trading day of the booking periods
(#476): it ends at its boundary — midnight UTC for crypto, the swap rollover for forex — in the pass
that seals the day's booking period. The new day starts from the account value the seal read at the
boundary, so whatever the first tick of the day does counts against the new day.

The day that ended is filed with the losses its ticks showed **and** with the seal's reading. The
limit is evaluated on ticks, and a fill an idle heartbeat drains after the day's last tick moves the
account where no tick evaluates it — the next tick already belongs to the new day. Without the
seal's reading, that fill's cost would stand in neither day's record. It only completes the record:
`limit_hit` says a limit FIRED, and nothing fires for a day that has ended.

How others cut the day, read 2026-10-26 from their own documentation and source code:

| System | When the limit is evaluated | The new day's reference |
|---|---|---|
| [FTMO](https://ftmo.com/en/trading-objectives/) | equity, commissions and swaps included, "cannot drop [below] at any time" — how it checks is not described | the balance recorded at 00:00 CE(S)T |
| [NinjaTrader Prop](https://prop.ninjatrader.com/platform/risk-settings/) | "in real time", realised and unrealised losses | the day's opening balance |
| [Topstep](https://help.topstep.com/en/articles/8284204-what-is-the-maximum-loss-limit) | its maximum loss "in real time throughout the session"; its daily limit "during the trading day", which starts at 5 PM CT | not stated for the daily limit |
| [The5ers](https://the5ers.com/faqs/2-step-plan-rules-specifications/) | not stated | the previous day's closing balance or equity, whichever is higher |
| QuantConnect LEAN (commit f3d841e6) | its risk models on each slice of data | no daily reference |
| freqtrade (commit f81a494e) | its drawdown protection when a trade closes | a rolling window of closed trades |
| nautilus_trader (v2.0.0rc8) | its risk engine has no daily loss limit | — |

None of them describes a separate evaluation at the reset: a rule held "at any time" needs none.
This layer evaluates on ticks, as LEAN evaluates on data, and needs one at the one instant where the
next tick belongs to the next day. Evaluating on every fill, as freqtrade does, would also cover a
heartbeat's fill during the day; there the next tick covers it already, and it would change when a
block can fire. One difference is kept on purpose: this layer starts the day from the account value,
equity, where FTMO starts it from the balance — an open loss carried over midnight counts against
FTMO's new day and not against ours.

### Display

SESSION panel shows safety state with mode indicator and headroom detail:

```
Safety:  ● ACTIVE  (spot)
         min_equity: 9.00 (now: 12.48)  |  dd: 0.1% / 20.0%
```

| Display | Meaning |
|---|---|
| `Safety: off` | Not configured (`enabled: false` or block omitted) |
| `Safety: ● ACTIVE (spot)` | Configured, spot mode, conditions not triggered |
| `Safety: ● ACTIVE (margin)` | Configured, margin mode, conditions not triggered |
| `Safety: ⛔ BLOCKED  min_equity (4.80 < 5.00)` | Triggered — reason shown inline |

Trigger and clear events are logged:
```
WARNING | ⛔ Safety circuit breaker triggered: min_equity (4.8000 < 5.0000)
INFO    | ✅ Safety circuit breaker cleared
```
