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
- **And the pull is cadenced.** It fires every `interval_ticks` (100) ticks OR at most every
  `min_interval_seconds` (60 s by default, profile-configurable) — whichever comes first, so
  60 s is the CEILING of the wait during an idle market, not a floor. A resting order is
  therefore repaired within one cadence, not instantly. Asking EARLY — a targeted
  order-status request fired by the unresolved event itself, plus a bounded in-flight
  window — is #487.
- **Absence at the venue does not resolve anything.** An order missing from the open-order
  list may never have been accepted, or may have filled. The pull cannot tell those apart,
  so such a pending is neither dropped nor confirmed: it is reported once into the session
  error pot naming the order, because it keeps `has_pending_orders()` true. Deciding it
  needs the closed-order / trades channel, which is again #487.

The algo needs no new code: `has_in_flight_operation()` stays true and its existing
discipline pattern blocks. What it gains is that "the venue refused this order" and "we
could not reach the venue" stop arriving as the same value.

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
