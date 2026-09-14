# External Connection Policy

**One retry ladder, one give-up rule, one classification** — shared by every connection this
system holds to something outside its own process (#473).

This document exists so the next external connection inherits a decision instead of making
an eighth behaviour. Before it there were seven connections and five behaviours, and the
ones carrying the most consequence had no ladder at all.

---

## The three outcomes

```
                        ┌─ TRANSIENT ──────► wait, escalate the delay, try again
   connection fails ────┤                    (their proxy cycling, a dropped socket,
                        │                     a 5xx, a 429, a DNS blip)
                        │
                        ├─ TERMINAL ───────► stop, ALERT a human, let the staleness
                        │                    contract declare the input blind
                        │                    (credential refused, unusable cursor,
                        │                     unknown pipeline id — retrying a typo
                        │                     forever reports THEIR outage for OUR mistake)
                        │
                        └─ INADMISSIBLE ───► refuse to start at all
                                             (a precondition that cannot be satisfied)
```

**Classification is by exception TYPE, never by parsing a message.** A ladder is built with
the types that are worth retrying for that connection; anything unlisted is TERMINAL,
because an exception nobody registered is most likely our own defect and retrying a defect
forever costs more than stopping does.

Readers that report failure as a *result* rather than by raising — the producer registry is
the case — raise `ConnectionAttemptFailedError(msg, terminal=…)` at their call site. Only
that reader knows whether the answer was a refused credential.

**A give-up is never silent.** It lands in the session error pot (§35) with a message naming
the system, because the reader's first question is whether the trading logic broke. Silence
is worse than a ladder that never ran: "gave up" then looks exactly like "still trying".

---

## The second axis — may this be retried at all?

The three outcomes classify the **failure**. Whether a retry is permitted also depends on
the **operation**:

| Operation | Retry-safe | Why |
|---|---|---|
| Reads — open orders, balance, asset pairs, OHLC, the registry | yes | no side effect at the venue |
| Connects — SSE, WebSocket | yes | a cursor makes the reconnect idempotent |
| **Writes — submit, cancel, amend** | **never** | a retry after a lost answer is how one intent becomes two positions |

**A write is never retried. It is RESOLVED by asking.** This is not our invention: FIX has
settled a lost response with an Order Status Request (MsgType=H) since 1992, and
`PossDupFlag` / `PossResend` exist to flag a re-send as a fault condition. nautilus_trader,
CCXT and LEAN all resolve the same way.

The mechanics on our side:

```
submit → transport fault
   └─ BrokerOrderStatus.UNRESOLVED        (the one value in that enum that is OURS)
      ├─ the PendingOrder STAYS in the tracker
      ├─ in_flight_operation = PENDING_SUBMIT
      ├─ on_order_rejected does NOT fire   (the venue never spoke)
      └─ the truth pull resolves it, keyed by our own client order id
```

**Who does the asking, and what is still missing (#355 Phase 1 / #487).** The reconcile
truth pull now joins on the client order id before `broker_ref`, so a resting order
carrying THIS session's key is matched to the pending that lost its answer, and the
executor restores the reference — the order returns to the poll path. Two limits are worth
knowing rather than rediscovering:

- **A latency-queue order is out of the pull's reach entirely.** The truth pull compares
  against `get_active_orders()` — resting orders only — so an unresolved MARKET or CLOSE
  order can never be attributed by it, whatever the cadence. Its only exit is the timeout
  (`order_timeout_seconds`, 30 s) → `BROKER_UNREACHABLE`.
- **That timeout fires exactly once, and removal is keyed by the order's OWN id.** The
  reference-keyed removals cannot serve an order whose write was never answered: its
  `broker_ref` is `None`, the index lookup finds nothing, and they return before removing
  anything — so the same order timed out again on every heartbeat and every tick for the
  rest of the session, repeating `on_order_rejected` at the algo and holding
  `has_pending_orders()` true. For a CLOSE it held `is_pending_close` true, which made that
  position unclosable. `discard_order()` removes by `pending_order_id`, which always exists.
- **`BROKER_UNREACHABLE` arms the order cooldown**, for the same reason as the other
  cooldown reasons: when the venue cannot be reached, sending more orders helps least. The
  brake gates ENTRIES only, so closing and protecting an open position stay unaffected.
  This depends on the line above and cannot precede it: `record_rejection` re-arms the
  cooldown on every call once the count is at threshold, and only a success in the same
  direction clears it — so a timeout that re-fired every tick would re-block the direction
  every tick, turning a sixty-second pause into a permanent trading stop.
- **And the pull is cadenced.** It fires every `interval_ticks` (100) ticks OR at most every
  `min_interval_seconds` (60 s by default, profile-configurable) — whichever comes first, so
  60 s is the CEILING of the wait during an idle market, not a floor. It is the slow lane;
  the fast one is the resolution below, fired by the unresolved event itself.

The algo needs no new code: `has_in_flight_operation()` stays true and its existing
discipline pattern blocks. What it gains is that "the venue refused this order" and "we
could not reach the venue" stop arriving as the same value.

## Asking, and what the answer may be turned into (#487)

The truth pull is cadenced and only sees resting orders. The resolution is neither: it is
armed by the lost answer and it drives itself on the tick and the heartbeat, which matters
because an unanswered write is exactly the situation in which no tick may arrive for a
while.

**All four writes are covered, and three of them used to collapse.** `is_unresolved` was
read in two places in the whole live layer; everything else treated "not rejected" as
"accepted", so an unresolved cancel ran the entire success path — dropping an order the
venue may still hold, clearing the protective stamp, and releasing the deferred close
beside a stop that may still be resting. That is the double-fill the cancel-before-close
ordering exists to prevent, reached through a transport fault instead of a race.

The rule is one sentence: **an unresolved write books NOTHING.** The local state stays as it
was, `in_flight_operation` stays set so no second write races the first, and the resolution
is armed.

**Which question is asked depends on what the lost answer left behind**, and both routes are
measured against Kraken (2026-09-13,
`python/experiments/venue_probes/probe_kraken_order_identity.py`):

```
broker_ref known      QueryOrders by that reference             one read
  (cancel / amend)

broker_ref None       OpenOrders  +  ClosedOrders by our key    two reads
  (submit)            QueryOrders REFUSES a key with no txid
                      (EGeneral:Invalid arguments), so there is
                      no one-call form
```

The second route needs our key to name ONE order, which is why a close mints its own
counter rather than reusing its position's: before that, `ClosedOrders` for one key answered
with two orders — an entry and its close — and a resolution that has to guess which is worse
than one that keeps asking.

**Three verdicts, no fourth:**

| Verdict | What the venue said | What we do |
|---|---|---|
| `RESOLVED_RESTING` | it names the order | restore `broker_ref` and step aside — the ordinary poll path already knows how to book a fill |
| `RESOLVED_ABSENT` | it answered and named nothing, *after* the settle window | now, and only now, a genuine rejection |
| `UNKNOWN_AT_CEILING` | still nothing when the budget ran out | escalate to the session error pot, block new ENTRIES, and dispose of the order per its world — see below |

A read that FAILED is none of these and books nothing at all: the venue did not answer, so
the next pass asks again.

**Why the empty answer waits.** An order accepted a moment ago may not be indexed yet, so
"named nothing" is not yet evidence that nothing was taken. `venue_read_settle_seconds` is
how long the venue's read plane may lag its write plane, and it is ONE key with several
readers — this resolution, the reconciler's stale / orphan / unconfirmed verdicts, and the
DriftAuditor, which meets the same lag and discards its measurement. A constant per site
would let them disagree about a physical property of one venue.

**Its budget is its own, never the broker ladder's.** `broker_transport.connection` is
`attempt_budget: 3` with `on_give_up: "abort"`, and abort ENDS the session. A resolution
that ends the run is worse than the state it resolves.

**The ceiling has a local disposition, and it is not "carry on".** New ENTRIES stop while an
order we sent is unaccounted for; closing and protecting what is already held continue,
because the guard is reached from `validate` on an `OpenOrderRequest`. It is a LATCH, not a
cooldown — the condition does not expire with time, it ends when the order is finally
accounted for, and specifically NOT because the order left the tracker.

**And the two worlds part company there**, along the same structural line #473 already
documented:

- A **resting** order stays in its list. The truth pull sees it every cadence and reports it
  into the session error pot, so keeping it costs a true `has_pending_orders()` and buys a
  standing record.
- A **MARKET or CLOSE** pending is outside that pull's reach entirely — nothing would ever
  look at it again — so it takes the disposition the fill timeout already defines: recorded
  `BROKER_UNREACHABLE`, never `BROKER_ERROR`, and removed from the tracker so it stops
  gating the algo. One booking routine serves both callers, because a timeout and a ceiling
  disagreeing about how a give-up is recorded is how a report and a record come to describe
  different sessions.

**The fill timer stops applying the moment the resolution claims an order**, and that is the
issue's first sentence made real. While the two shared a timer the 30 s timeout DISCARDED the
pending long before a 120 s resolution could finish — for MARKET and CLOSE orders, the ones
the truth pull cannot see, that was the only exit at all. The suppression is gated on the
resolution being able to RUN: it needs the canonical clock, and before the first event there
is none, so an order submitted that early keeps its ordinary timeout rather than losing every
exit.

**The ceiling is also the watchdog, and nothing else supplies one.** `check_timeouts`
iterates the request processor's own store, and a resting order is not in it, so a pending
left in `PENDING_MODIFY` or `PENDING_CANCEL` would sit there until session end — silently,
while every further operation on it is refused as busy. At the ceiling the operation is
released and a cancel that was holding a deferred close abandons it, with the error line the
operator reads. Abandoning is the safe direction: releasing would send the close beside a
stop whose fate is unknown.

Config lives at `autotrader.execution` in `app_config.json` —
`order_timeout_seconds`, `venue_read_settle_seconds`, and the `unresolved_resolution`
block.

### The key that makes asking possible

The venue's own reference is exactly what a lost answer did not deliver, so the query needs
a key we chose: `cl_ord_id`.

```
internal (both pipelines, unchanged)   pos_btcusd_47
wire key (live only)                   p1641_47      1641 = 4 chars of the run id's random half
```

**The 18 characters are KRAKEN's limit, not the framework's.** The adapter contract carries a
neutral `client_order_id: Optional[str]` and each adapter maps it to whatever its venue
offers — the truncation lives in the Kraken adapter, not in the shared builder. MT5 has no
client order id at all; its equivalent is the per-EA `magic` number, an integer, which #209
carries.

Two reasons for the shape. **It fits** — Kraken allows 18 ASCII characters, which the
readable internal id does not, so the readable form stays in our own books. And **it does
not collide across a restart**: the internal counter restarts at 1 with the process, so
without a session discriminator a fresh order would carry the key of one still resting at
the venue from last night, and boot adoption (#355) would match the wrong order.

The **session** owns the key, not the run — a #476 day fragment mints its own run id and
must not change it mid-session.

An order the venue reports with *no* key of ours is not a defect: it is somebody else's
order, and that absence is the fact that tells it apart. #349 turns it into an EXTERNAL
order rather than a ghost.

**We take this string apart, and that is a deliberate exception.** Parsing a speaking key is
normally a smell — an identifier should be opaque, and pulling meaning out of its characters
couples every reader to its format. It is right here for one reason: across a lost answer or
a restart this key is the ONLY handle. The venue's own reference is exactly what did not
arrive, and nothing else survives a new process. What keeps the exception contained is that
the format has ONE writer and ONE reader, side by side in `run_id_utils`, and that we parse
our own minting rather than a foreign convention.

The key is read as strictly as it is written: the discriminator must have the exact minted
width and the counter must be digits (`parse_client_order_id`). A client order id is
free-format at the venue, so a looser parse would claim another client's order as one of
ours — and since the session half of the key is what separates "my own lost answer" from
"an earlier session of this bot" (the #355 adoption candidate), a wrong claim there is the
one classification that must not be guessed.

---

## Who owns the wait

The ladder is **pure arithmetic plus classification — it never sleeps.** Waiting belongs to
the caller, because the four contexts have four different primitives. A ladder that owned
the wait would need a plugin point to serve them all.

| Context | Connections | How it waits |
|---|---|---|
| Own thread with a stop event | signal stream, health probe | `stop.wait(ladder.next_delay(n))` — cancels cleanly on shutdown |
| asyncio | broker tick socket | `await asyncio.sleep(...)` |
| Broker worker thread | order submit / cancel / query jobs | blocking; **does not block the tick loop** |
| **Inside the tick loop** | reconcile pull, order re-poll | **it does not wait at all** |

The last row is the important one. Everything the tick loop does against the broker is
already **cadenced** — `Reconciler.is_due` (ticks OR wall seconds), the per-order
`poll_interval_ms` throttle. So a transient failure there **skips the cycle** and the
cadence IS the ladder: no sleep, no shifted heartbeat, no retry storm.

```
14:32:07  🔍 reconcile #47: SKIPPED — broker truth unreachable (HTTP 502) · next attempt in 30s
14:32:37  🔍 reconcile #48: clean — broker_orders=1 local_orders=1
```

A skipped cycle is **not clean** — nothing was compared, so claiming the local shadow
matches broker truth would be a statement we did not earn. It is counted and surfaced
(`reconcile_skipped`), because a reconcile count climbing against a dead venue is "gave up"
wearing the face of "still checking".

---

## Boot is not one switch

The three reads at boot deserve different answers, and only one of them is an operator
choice:

| Boot read | On give-up | Why |
|---|---|---|
| Signal producer registry | **degrade** (configurable) | the staleness contracts (#434 / #436) describe the reduced state, and the boot bridge mounts the archive slice — the session starts STALE, out loud, not blind |
| Warmup bars | **INADMISSIBLE** | there is no contract for an empty indicator history. The worker still emits a number, that number is wrong, and nothing declares it wrong |
| Account balance | **INADMISSIBLE** | position sizing has no defined answer without it |

Degraded start, as the operator reads it:

```
03:14:02  📡 signal_registry unreachable (connection refused) — attempt 1/3, retry in 2.0s
03:14:04  📡 signal_registry unreachable (connection refused) — attempt 2/3, retry in 3.4s
03:14:08  ⚠️  signal_registry is unreachable after 3 attempt(s): … This is an external
             system, not the trading logic. Continuing DEGRADED.
03:14:08  📡 Starting DEGRADED without the producer registry …
```

Refusal, as the operator reads it:

```
❌ Warmup requirement unmet: H1: 0/200. The broker's bar history could not be read, and
   there is no staleness contract for an empty indicator history — refusing to start
   rather than trading on unreliable worker output.
```

---

## The seven connections

| # | Connection | Ladder | Gives up when |
|---|---|---|---|
| 1 | Signal SSE stream | 5 s → 60 s, jitter, budget 0 | credential refused, request refused, terminal control frame |
| 2 | Signal producer registry (boot) | 2 s → 30 s, jitter, budget 3 | budget → **degrade** |
| 3 | Producer health probe | its 1800 s cadence is the ladder | a 401/403 stops it — an address we got wrong will not correct itself |
| 4 | Broker tick WebSocket | 1 s → 60 s, jitter, budget 0 | never — a dead tick socket is a reason to keep asking |
| 5 | Broker REST (orders, truth pull) | classification only; the caller's cadence waits | see "who owns the wait" |
| 6 | Broker warmup bars (boot) | broker policy, budget 3 | short read → **refuse to start** |
| 7 | Broker config + balance (boot) | broker policy, budget 3 | falls back to a cached copy, warning louder as it ages; no cache → refuse |

Row 7 is a fourth pattern worth naming: it does not retry into the void, it **degrades to an
older copy and gets louder the staler that copy is**. That is a TRANSIENT whose answer in
the meantime is a reduced one, and naming it stops the next connection from inventing it
again.

---

## Where the numbers live

**One schema, several homes.** `ConnectionPolicy`
(`framework/types/config_types/connection_policy_config_types.py`) is embedded — same field
names, same defaults — into the config block that owns each domain:

| Home | Connections |
|---|---|
| `configs/sentiment_config.json` → `stream.connection` / `stream.boot_connection` | 1, 2 |
| AutoTrader profile → `tick_source` | 4 |
| `configs/market_config.json` → `broker_transport.connection` | 5, 6, 7 |

What is decided in one place is the classification and the vocabulary, not the values. §28's
mirror rule applies per file: every default appears with the identical value in its config.

**The policy lives framework-side, never inside an adapter.** #328 moves four of these
Kraken files to `python/adapters/kraken/`, and a ladder that travelled with them would be
re-invented by the next adapter (#209). An adapter contributes exactly two things: which
exception types are retryable for it, and its numbers.

---

## Adding a connection

1. Give it a `ConnectionPolicy` in the config block that owns its domain, mirrored in the file.
2. Build a `ConnectionLadder` with its name and the exception types worth retrying.
3. Decide who waits — a loop of your own, `run_with_ladder`, or a cadence that already exists.
4. Decide the give-up rule, and make sure the give-up reaches the session logger.
5. Add a row to the table above.

**Do not add a retry package.** The ladders are three lines of arithmetic; a third-party
library would cover the `requests` calls and neither the SSE socket nor the asyncio
WebSocket, which is two thirds of the problem. If the design grows a plugin point, it has
gone wrong.

## Related

`docs/architecture/pending_order_architecture.md` (the UNRESOLVED state) ·
`docs/autotrader/autotrader_architecture.md` (boot + reconcile) ·
`docs/data_pipeline/signal_data_source.md` (the stream) ·
`docs/architecture/warnings_errors_tiers.md` (where a give-up lands) ·
`docs/user_guides/adapter/adapter_development_guide.md` (what an adapter contributes)
