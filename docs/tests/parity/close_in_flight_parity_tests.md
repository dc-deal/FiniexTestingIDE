# Close-in-Flight Parity Tests

A strategy may ask to close a position while a close for it is already on its way: a
counter-signal arrives before the first close has filled, or a protective level is breached in
the same tick an emergency flatten runs. Both pipelines key an in-flight close by its position
id, so the second request used to REPLACE the first. Live lost the only reference to the first
close's fill — the book said open while the venue had sold — and the simulation sent the second
close in place of the first. A shipped CORE strategy reaches it: `cautious_macd` closes on a
counter-signal without asking whether a close is already in flight.

The rule now: the second request JOINS the close already in flight. One order reaches the venue,
the caller hears `PENDING` with `metadata['joined_in_flight_close']`, and the first close keeps
its size, its reason and — in the simulation — its due time.

**Location:** `tests/parity/test_close_in_flight_parity.py`

Not covered here: the close parked behind a protective order's cancel, which joins through its
own deferral and is pinned in `docs/tests/autotrader/protective_level_tests.md`.

## What It Validates

| Class | Focus |
|-------|-------|
| `TestLiveJoins` | Two `close_position` calls before the first answer: one close order reaches the venue, the stored close keeps the first request's size and reason, the second result is marked as joined, and the position closes with exactly one executed close |
| `TestSimulationJoins` | The same in the latency queue: the pending close is the same object, keeps its `broker_fill_msc`, size and reason, and the position closes once |

Both run in both account models (margin and spot).

## Why join, and not refuse or queue

Refusing the second request writes a rejection row and makes the stop-loss path report the
position as unprotected, which would be false — the first close is on its way. Queueing it needs
a second keyed slot in both stores, with release and abandon paths in both pipelines, for a request
whose purpose the first close already serves. A joined close draws no latency in the simulation,
so later latencies in a scenario that closes twice shift by one draw; none of the committed test
scenarios does.

## How to Run

```bash
python -m pytest tests/parity/test_close_in_flight_parity.py -v
```
