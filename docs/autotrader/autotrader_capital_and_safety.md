# AutoTrader Capital and Safety

This is the half of the live system where a defect costs money directly. What the bot may spend,
what the venue has already reserved against that, who enforces a stop when the process is not
running, and what ends a session that is losing.

The one sentence that organises all of it: **a venue reserves at PLACEMENT, our balances move at
FILL.** Everything below follows from the gap between those two instants.

**Not here:** how an order reaches the venue — `docs/architecture/live_execution_architecture.md`.
The protective-level contract itself — `docs/architecture/protective_levels.md`. The session-end
policy — `docs/architecture/session_end_policy.md`.

## Capital — what the bot may spend (#489)

The framework hands the bot the account's balances and lets it trade against them. Two things
sit between "the account holds this" and "the bot may spend this", and both are on the SHARED
executor, so simulation and live answer them identically.

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
a venue-held LIMIT exit (#503) would be reserved at one rate and charged at another unless the
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
  a spot position and a protective resting order, and a refusal abandons both at the venue with
  nothing reconciling them — and on spot the SL/TP level lives in the very process that declined
  to start.
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

**What follows.** The ERROR lands in the session pot (§35), so it reaches the end-of-session
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

## Protective levels — who enforces a stop

A `stop_loss` or `take_profit` declared on an order is evaluated by **this process**, against the
tick stream, in both pipelines. Since #503 the **stop** can additionally rest at the venue as an
order of its own, so it survives this process dying — opt-in, default OFF
(`autotrader.execution.venue_held_protection`).

Only the stop, and only one of a declared pair: Kraken has no OCO, so the take profit stays with
the local check — which is why the tick check stands down **per level**, never per position.

**Full contract:** [protective_levels.md](../architecture/protective_levels.md) — the switch and
its refusal, the order's whole life at the venue (amend, cancel-before-close, partial, session
end, orphan), what the boot asks about a carried reference, and what has and has not been measured
against a real account.

## Safety Circuit Breaker

A soft-stop mechanism that blocks new position entries when configurable risk thresholds are exceeded. Existing open positions continue to run — SL, TP, and signal-based closes are not affected.

### Behavior

```
Tick rein
  → executor.on_tick()    ← SL/TP checks run against the tick (see below)
  → Workers → Decision    ← produces BUY / SELL / FLAT
  → [SAFETY CHECK]        ← evaluates thresholds against equity (spot) or balance (margin)
  → if blocked: decision.action = FLAT  ← override, no trade opened
  → execute_decision()
```

The check runs after every tick. If conditions clear (e.g. equity/balance recovers above threshold), the block is automatically lifted and trading resumes.

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

**The soft block** — stops new entries, leaves open positions alone:

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

**The hard stop** — CLOSES what is open, then ends the session:

| Field | Description |
|---|---|
| `emergency_flatten_enabled` | Master switch. Default `false`, because turning it on changes what happens to real money |
| `max_drawdown_pct_hard` | Drawdown share at which everything is closed |
| `max_drawdown_abs_hard` | The same as an amount |
| `spot_liquidate_to_quote` | Whether the hard stop also SELLS a spot holding. Default `false`, and the asymmetry is real rather than timid: a margin position can lose more than the account holds, so liquidating it is the point; a spot holding cannot, so selling it converts an unrealised loss into a realised one — a trading decision, not a safety one |

All conditions are OR-combined — any one alone triggers, and every condition that fired is named
in the reason, so a blocked session says whether one limit was touched or three were blown through.

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
