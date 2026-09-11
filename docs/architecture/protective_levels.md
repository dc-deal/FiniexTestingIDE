# Protective Levels — Who Enforces a Stop

A `stop_loss` or `take_profit` declared on an order has to be enforced by somebody. This document
says by whom, in which of the two pipelines, and what happens to that answer when the level moves,
when the position is closed another way, when the session ends, and when the process comes back.

The short version: **this process enforces the level, and since #503 the venue can hold the stop
as an order of its own** — opt-in, default OFF.

---

## 1 · The local check, and the hole it cannot close

A declared level is evaluated by **this process**, against the tick stream, in both pipelines.
When it is breached the position is closed: live through the normal asynchronous close, so the
exit fills at the venue's next price; in simulation through a synthetic close at exactly the
level, which is what keeps a backtest deterministic. **A backtest therefore reports protected
exits slightly better than live can deliver them**, and that difference is pinned by tests rather
than smoothed over.

**Until #500 a live level was enforced by nobody.** The engine skipped its own check outside the
simulation, on the stated assumption that the broker enforced it server-side — but the submit
payload never carried a level, so the assumption was never true. The level was recorded on the
position, shown to the strategy, printed on the console and carried into the run report, and
nothing acted on it. Kraken's own answer to a submit carrying `stop_loss` confirmed it: the order
came back described without any conditional close.

| | Covered by the local check |
|---|---|
| The price moves while we are running and connected | ✅ live ticks come from the venue's trade channel, so every price it printed reaches the check |
| Our process dies, or the connection drops | ❌ nothing watches the level until we are back |
| The venue gaps past the level | partly — the exit is market-on-trigger, so it fills below a long's stop |

The second row is why the rest of this document exists. A thirty-day unattended run restarts —
that is a certainty, not a risk — and a level enforced only by a running process is not enforced
during the hours that matter most.

---

## 2 · Why a standalone order, and not Kraken's conditional close

Kraken offers two routes and only one of them is worth having.

Its **conditional close** (OTO) attaches an exit to a submit. It can only be set WITH the primary
order, can never be adjusted afterwards, and therefore cannot protect an inventory already held or
a position adopted at cold start — which are the two states an unattended month is in most of the
time.

A **standalone stop order** has none of those limits, and Kraken Spot accepts one. Since #500 the
live path routes `STOP` and `STOP_LIMIT`, so a strategy can place its own; since #503 the
framework places one for a declared `stop_loss`.

> **A premise this project once held is false, and was corrected by measurement (2026-09-07):** a
> cash account does NOT reserve the whole holding per resting exit. Kraken reserves nothing and
> **links** nothing. That is the reason OCO and orphan cleanup are OUR work rather than the
> venue's, and it shapes every rule in §4.

---

## 3 · The switch, and what exactly gets placed

`autotrader.execution.venue_held_protection` — **default OFF**, per-order overridable via
`send_order(venue_held_protection=…)`. Three parties decide, and a refusal names the short side:

```
   profile default  ⊕  per-order override        →  resolved once, at submit
                    ⊕  adapter capability        →  refused where the venue cannot carry it
```

- **Live**, venue cannot carry it → the session **refuses to start** (a per-order rejection would
  reject every protected entry one at a time, and each would read as an isolated incident).
- **Mock / simulation** → the flag is **accepted and ignored**. A strategy that opts in must stay
  backtestable and rehearsable, and sim/live parity is the point of the project.

Once an entry fills, the executor mints a STOP of its own: opposite direction, at the declared
level, its own order id, naming its position in `closes_position_id`.

**Only the STOP is placed, and only ONE of a declared pair can be.** Kraken has no OCO and no
bracket, so a target and a stop resting together would need a link the venue does not offer. The
stop is the half worth placing — it bounds the loss, while a lost target costs an opportunity —
and the take profit stays with the local check. That is why the tick check stands down **per
level**, never per position: standing down for the whole position would leave the target
recorded, printed, carried into the report and enforced by nobody, which is #500's defect one
level up.

---

## 4 · The order's life, and why each rule is not the obvious one

Every failure below is SILENT — the console shows a level and the venue holds a different one, or
none — which is why each of these ends in the session channel rather than in a debug line.

| When | What happens | Why not the obvious thing |
|---|---|---|
| The venue **confirms** it | `Position.protective_broker_ref` is stamped, and only then does the local check stand down for that stop | Standing down on the DECLARATION would leave the level with **neither** enforcer for the whole round trip |
| The level **moves** | the order is **amended** | A cancel-replace opens a window with no protection at all — on nearly every tick of a trailing stop |
| The level is **withdrawn** | the order is cancelled | It cannot be amended into nothing, and the venue would go on enforcing what the strategy just withdrew |
| A close arrives **by another route** | the order is cancelled FIRST, and the close **waits** for that confirmation | There is no `reduce_only` at spot: a market close goes through while a stop rests, and **both can fill** — the second sells a holding that is gone |
| The cancel is **not** confirmed | the close does **not** go out | The position stays open AND protected, which is the safe end of the two outcomes |
| A **partial** close resolves | the remainder gets a **fresh** order at its new size | Kraken can amend a volume; our modify path cannot, so a new order is the honest route |
| The **session ends** | the order is **left standing**, whatever the cancel policy says | It is the one order whose entire purpose is to outlive the process |
| …unless it is an **orphan** | then it is cancelled with the rest | A stop over a holding that is gone sells coins that in a shared account belong to someone else |
| The order **dies** any other way | the stamp is cleared and the local check takes the level back next tick | A stamp left standing means the level is watched by nobody, silently |

### What "a close is in flight" must not start meaning

The engine's `is_pending_close(position_id)` answers one question for the strategy: may I ask
for a close, or is one already on its way? It matches on the ORDER id, which IS the position id
for every close the strategy or the engine requests.

A protective order is the one exception, and it is deliberately **not** matched there. It carries
its own id and names its position in `closes_position_id`, so the obvious widening — "also count
orders that close this position" — would be wrong in the expensive direction: a protective order
**rests for the whole life of the position**, so counting it would tell the algo that a close is
permanently in flight, and the algo would never close anything again. The guard means IN FLIGHT,
not RESTING. What actually keeps the two apart is the deferral in the row above — the close is
accepted, the cancel goes first, and the close follows its confirmation.

### The window that is easy to miss

Between **submitting** the protective order and **hearing back**, nobody at the venue holds
anything. Two defects lived in exactly those seconds and were found only by a real run
(2026-09-10):

- A close requested in that window raced the protective order — the close went out, the venue
  confirmed the stop four seconds later, and the order outlived the position it protected as an
  orphan at the venue. The deferral now hangs on the order's **existence**, not on its
  confirmation.
- The protective order's wire key is the **trailing number** of its internal id. Minted from a
  private counter it collided with the next entry's, and Kraken refused it with
  `EGeneral:Invalid arguments:cl_ord_id not unique`. It draws from the same sequence as every
  other order now — which also keeps the cold-start high-water mark honest.

Three more cases sit in the same window, found by an audit on 2026-09-11 rather than by a run.
Each is the ordinary sequence, not the exotic one:

- **A second close request joins the first; it does not overtake it.** A deferred close registers
  nothing with the request processor, so `is_pending_close` and `has_pending_orders` both answer
  False while it waits — and the local level check calls in again on every tick for as long as
  the level is breached. Letting the repeat through would send the close beside the still-resting
  stop, which is the very thing the deferral prevents.
- **Wherever a broker reference arrives, the position is stamped.** There are three such places,
  not one: the submit answer, the cold-start adoption, and the reconcile attribution that
  reclaims an order by the client key we minted. A protective order confirmed through the third
  one without the stamp leaves the position reading LOCAL for a stop the venue holds, so both
  enforcers act.
- **An order id carried across a restart without its reference is cleared at boot.** It names an
  order object that died with the previous session, so it can be neither asked about nor
  cancelled — and since every close waits behind exactly that cancel, leaving it standing would
  make the position permanently unclosable, re-persisted on every save.

### A refused amend does not arm the cooldown

A rejected amend of a **protective** order is not a new-order rejection, because no new order was
attempted. It stays visible in the log and the order history but does not reach the order-outcome
fan-out the OrderGuard feeds on. Measured on the trailing stop: 378 amends over 62 positions,
worst burst 20 in 39 ticks — and two rejections arm a 60-second block on every new order in that
direction, the replacement protective order included.

---

## 5 · Coming back: what the boot asks

The carried broker reference is the only key that can still ask what happened while nothing was
running. A pairing flag would not do — a pairing can be re-derived from a counter, a reference
from nothing — which is why it travels in the cold-start carry-over.

```
  carried protective reference
        │
        ├─ FILLED (or terminal with executed volume)
        │     → the venue closed the position while nothing ran.
        │       Recorded at boot, BOOKED on the first tick — a trade record needs a
        │       bid and an ask, and the boot has neither yet.
        │
        ├─ still RESTING
        │     → adopted back into this session's stop world, so it can be amended
        │       and cancelled like any other order
        │
        └─ UNKNOWN — the venue does not recognise the reference
              → an ABSENCE, never "still working". The position is UNPROTECTED and
                the local check takes the level back from the first tick.
```

**The read happens BEFORE the balance cross-check**, and that ordering is not cosmetic: the note
still claims the coin because it was written before the stop fired, so a closed position would
otherwise be reported as "somebody sold outside this bot" — the feature's most ordinary success
raising an alarm on every restart.

---

## 6 · Who holds it, as the report says it

`get_protective_level_enforcement()` on the executor answers **by default for this run**; a
position whose protective order the venue confirmed answers for itself
(`Position.protective_enforcement`, derived from the broker reference and never stored beside it).
Every open position in the run report carries that answer next to its levels — an operator reading
a stop can always read who is behind it.

---

## What is measured, and what is not

- **Measured against the live venue:** placement, the stamp, the amend, the cancel-before-close
  ordering, and the release — the field study's `protective_level_test` phase proves the whole
  sequence end to end on a real account, and is part of the release gate.
- **Not measured:** a protective order that the venue fills only PARTIALLY. The resolver is built
  for it and books the delta idempotently, but no run has produced one.
- **Not walked at all: the MARGIN side.** Everything here is spot-shaped — see the anchor on
  `Position.protective_enforcement` and #209, where MT5 holds the level ON the position and there
  is no separate order to derive a reference from.

## Related

- [session_end_policy.md](session_end_policy.md) — why this one order is exempt from the cancel policy, and when it is not
- [live_execution_architecture.md](live_execution_architecture.md) — the resting-order worlds this order lives in
- [pending_order_architecture.md](pending_order_architecture.md) — `closes_position_id` and the order object
- [external_connection_policy.md](external_connection_policy.md) — why a write is resolved by asking rather than retried
- [../tests/autotrader/protective_level_tests.md](../tests/autotrader/protective_level_tests.md) — the suites that pin all of it
