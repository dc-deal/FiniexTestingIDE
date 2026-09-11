# Kraken Adapter Tests

Offline unit tests for the Kraken adapter's pure layers — signing, payload building, response
parsing. **No network, no credentials, no orders.** Everything here runs in the daily suite, which
is the point: the real-order suite under `tests/live_adapters/` is a release gate and runs far too
rarely to be the only guard on how our payloads reach the venue.

| File | Covers |
|---|---|
| `test_nonce.py` | private-call nonce monotonicity and thread safety (#332) |
| `test_dry_run_fill_rules.py` | what the simulated venue does with an order in dry-run mode (#505) |
| `test_client_order_id.py` | the key WE choose, on the wire and read back (#473) |
| `test_conditional_order_payloads.py` | stop / stop-limit price semantics, both directions (#500) |

## Nonce — `test_nonce.py`

The first live Field Study (#332) aborted with `EAPI:Invalid nonce`: the adapter stamped a
millisecond nonce (`int(time*1000)`) with no lock, so concurrent private calls (the order-polling
worker thread + the reconciler) collided on the same millisecond or reached Kraken out of order.
The fix: private calls are serialized under one lock (`_private_lock`), held through the POST; and
the nonce is strictly monotone — `max(int(time*1000), _last_nonce + 1)`, where the time component
keeps it above the previous session across restarts and the counter guarantees a strict increase
within the process.

| Test class | Asserts |
|---|---|
| `TestNonceMonotonicity` | 1000 rapid calls → nonces strictly increasing (same-ms → counter increments) |
| `TestNonceThreadSafety` | 8 threads × 100 calls under `_private_lock` → all 800 unique + strictly increasing |

## Client order id — `test_client_order_id.py`

A submit whose answer was lost cannot be asked about with the venue's own reference, because that
reference is exactly what did not arrive. The key has to be one we chose. Two properties, both
cheap to get wrong: it must FIT (Kraken allows 18 ASCII characters) and it must not COLLIDE across
restarts (the internal counter restarts at 1 with the process, so without a session discriminator a
new order would carry the key of one still resting from last night, and boot adoption would match
the wrong order).

## Conditional order payloads — `test_conditional_order_payloads.py`

Kraken's two price fields mean different things per order type, and the mapping decides at what
level a real order is placed:

| ordertype | `price` | `price2` |
|---|---|---|
| `limit` | the limit price | — |
| `stop-loss` | the **trigger** | — |
| `stop-loss-limit` | the **trigger** | the limit price |

There is **no `stopprice` request parameter**. That name appears only in Kraken's *responses*
(OpenOrders / QueryOrders), and sending it would be silently ignored while the order went out with
no trigger at all.

| Test class | Asserts |
|---|---|
| `TestTheTriggerGoesWhereKrakenReadsIt` | STOP → `stop-loss` with the trigger in `price`; STOP_LIMIT → `stop-loss-limit` with trigger + `price2`; LIMIT unchanged |
| `TestASignedPriceNeverReachesTheVenue` | a negative or zero price raises; an ABSENT price is simply omitted |
| `TestAmendingAStopMovesItsTrigger` | AmendOrder routes a stop's new price to `trigger_price`, a limit's to `limit_price`, and a stop-limit carries both |
| `TestTheReadSideKeepsTheTwoPricesApart` | a resting stop reports its trigger as `stop_price` and no limit price; a trailing type reports NEITHER price; an unnameable ordertype becomes `OrderType.UNKNOWN` and is still reported |
| `TestAnAnswerThatNamesNoOrderIsNotAState` | a QueryOrders result that does not mention the txid becomes `BrokerOrderStatus.UNKNOWN`, not PENDING — and UNKNOWN is not terminal, so nothing is booked off it |

Three Kraken behaviours are worth knowing before reading the assertions, because each one turns a
mistake on our side into a real order rather than an error:

- **A signed or `%`-suffixed price is an OFFSET**, not a price. `price` and `price2` accept a
  leading `+`, `-` or `#` to mean an amount relative to the last traded price. So `str(-1.5)` is
  not rejected — it is a valid order 1.5 below the last trade. The refusal has to be ours, and it
  lives in `_put_price`.
- **AmendOrder has two price fields.** `trigger_price` activates a triggered order, `limit_price`
  is what it fills at. Everything used to go into `limit_price`, so amending a resting stop's
  trigger — what a trailing stop does on every ratchet — would have moved the fill price and left
  the trigger standing.
- **An unknown ordertype must be reported, not dropped.** Dropping the row hides the order from the
  exclusive-account check, which asks whether a stranger is working our symbol. Calling it a LIMIT
  hands a number of unknown meaning to everything that reads a limit price. `OrderType.UNKNOWN`
  carries the row with no prices.
- **An answer that names no order is an absence, not a state.** Measured 2026-09-08: a QueryOrders
  for a txid Kraken never minted returns `{}`, and reading a status off the absent entry used to
  produce PENDING — so "the venue has never heard of this order" and "the order is resting" arrived
  as the same answer. They are opposite facts and only one is safe to act on. `BrokerOrderStatus.UNKNOWN`
  says which one it is, and it is deliberately NOT terminal: booking a cancel or an expiry off an
  empty answer would invent a fact. Resolving it needs a WIDER read — a time-ranged history rather
  than a reference lookup. That read is **not built**: no `ClosedOrders` route exists on the
  adapter, and the gap is named at both consuming sites in the code. The SAFE half is in place —
  UNKNOWN is never read as "still resting", so the bot never believes in protection it does not
  have; what is missing is the ability to find out what actually happened.
  Probe: `python/experiments/venue_probes/probe_kraken_txid_retention.py`.

## Dry-run fill rules — `test_dry_run_fill_rules.py`

In dry-run nothing at the venue holds the order, so `DryRunOrderSimulator` plays the venue. It
used to play it blind: every order flipped to FILLED after two polls and a MARKET order filled at
`0.0`. With `poll_interval_ms = 5000` that meant every resting order "filled" about ten seconds
after placement at a price nobody chose — and `dry_run: true` is the shipped default for
kraken_spot, so a rehearsal reported a stop that had fired at zero.

| Test class | Pins |
|---|---|
| `TestAMarketOrderFillsAtARealPrice` | a buy pays the ask, a sell receives the bid, and the price is the one at the poll that FILLS it — not the one at submit, which showed zero round-trip slippage |
| `TestARestingOrderWaitsForItsPrice` | a limit or stop the market never reaches never fills, twenty polls or not; a triggered stop fills at the MARKET rather than at its trigger; and a stop-limit the market gapped past never fills at all, which is the real risk of one |
| `TestWhatItCannotDecideItRefuses` | no quote, an order type nothing models, or a reference it never issued → PENDING plus a reason, never an invented price |
| `TestTheLifecycleShapeIsUnchanged` | the ref format, non-colliding refs, idempotent cancel, in-place amend, and a re-query after the fill still reading FILLED |

Neither the comparison nor the book side is duplicated here: both live in
`utils/trading_math/price_trigger.py`, the same module the backtest uses, so a rehearsal and a
backtest cannot disagree about WHEN or AT WHAT an order fills. The refusal's VISIBILITY and the
quote's journey to the simulator are a different suite —
`tests/autotrader/live_executor/test_undecided_dry_run_poll.py`, because the executor is what
logs the one and starts the other.

## Run

```bash
pytest tests/autotrader/kraken_adapter/ -v
```

Or launch.json: `🧩 Pytest: Kraken Adapter (Offline)`.

Source: `python/framework/trading_env/adapters/kraken_adapter.py` — `_do_fetch_private`,
`_sign_request`, `_build_submit_payload`, `_put_price`, `_build_modify_payload`,
`_parse_openorders_response`, `_prices_from_descr`.
