# Connection Ladder Tests

**Suite:** `tests/framework/connection_ladder/` · **Mark:** `framework`, `unit`

Pins the shared retry decision every external connection routes through (#473): how a
failure is classified, how the delay grows, when the budget runs out, and what a give-up
does. Architecture:
[external_connection_policy.md](../../architecture/external_connection_policy.md).

**Nothing here sleeps.** `run_with_ladder` takes an injected waiter, so a three-attempt
budget against a 60 s cap costs no wall time and the delays are asserted as values rather
than observed as duration.

## Tests

| Group | Checks |
|---|---|
| `TestClassify` | registered transient / terminal types · **an unregistered type is TERMINAL** (an exception nobody declared is most likely our own defect) · `ConnectionAttemptFailedError` carries its own verdict · `INADMISSIBLE` passes through · terminal wins over transient on an overlapping subclass |
| `TestNextDelay` | first retry uses the initial delay · doubles per attempt · capped at max · attempt 0 does not go below initial · jitter stays within `[0.5, 1.0)` of the delay and is not a constant |
| `TestBudget` | budget 0 never exhausts (a long-lived connection's job is to come back) · exhausts exactly at the budget |
| `TestGiveUp` | ABORT raises and names the SYSTEM ("not the trading logic") · DEGRADE returns and says so · INADMISSIBLE raises regardless of the rule · **every give-up reaches the error pot** |
| `TestRunWithLadder` | returns on first success without waiting · succeeds on the third attempt with the expected delay sequence · exhausted budget degrades to `None` · a TERMINAL failure stops immediately **without waiting** · ABORT propagates · every retry is announced |

## Why the unregistered-type test matters

It is the one assertion that decides the failure mode of the whole unit. If an unknown
exception were TRANSIENT, a defect in our own parsing would be retried forever against a
healthy venue — reporting their outage for our mistake, quietly, for as long as the session
runs.

## One HTTP attempt, classified — `test_http_attempt.py`

The other side of that rule. A raw `requests` exception is a type nobody registered, so it is
TERMINAL — which made the warmup read give up at its first timeout with two attempts of its
budget unused. A `requests` call that a ladder retries therefore goes through `send_classified`
(#598), which turns each failure into a `ConnectionAttemptFailedError` carrying its verdict.

| Group | Checks |
|---|---|
| `TestTheClassification` | a timeout and a refused connection are TRANSIENT and name the fault · 429 and 5xx are TRANSIENT, another 4xx TERMINAL, by the shared status rule · a good answer is handed back unchanged |
| `TestTheLadderRetriesWhatWasClassified` | a timeout is tried as often as the budget allows · a 401 ends at the first attempt |

What the venue says inside a successful answer (Kraken's `error` list) is not classified here;
the caller judges it.

## Sibling suites

The ladder's *adoption* is tested where each connection lives:

- `tests/autotrader/reconciliation/test_reconcile_unreachable_broker.py` — a 502 skips the
  cycle instead of ending the session
- `tests/autotrader/live_executor/test_unresolved_order_outcome.py` — a transport fault is
  UNRESOLVED, not a venue rejection
- `tests/autotrader/kraken_adapter/test_client_order_id.py` — the wire key fits the venue
  limit and does not collide across sessions

## Run

```
pytest tests/framework/connection_ladder/ -v
```
Or the launch entry `🧩 Pytest: Connection Ladder (#473)`.
