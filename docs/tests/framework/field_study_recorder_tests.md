# Field Study Recorder Tests

`tests/framework/field_study_recorder/` — the field study's capture (`field_study.jsonl`) and the
certificate that reads it. Runs as the `framework/field_study_recorder` suite, offline: no broker,
no network, no order placed.

The field study is the acceptance test of the live core, and its certificate is one of the three a
real-money run starts on. So what the capture says and what the certificate makes of it have to
hold before a funded run ever writes one. Since #566 the capture keeps no picture of its own: its
order and venue lines are the session's order-event stream, copied in while the executor writes it.
This suite holds that copy to the stream, and the certificate to what the copy carries.

Not here: the phase machine and the order hooks, which are the
[Field Study Machine Tests](../autotrader/field_study_machine_tests.md); a mock field study end to
end, which is `test_field_study_capture.py` in the
[AutoTrader Integration Tests](../autotrader/integration_tests.md); the guide to running one with
real money, which is the [Field Study Guide](../live_field_study/field_study_guide.md).

## What Is Tested

### `test_recorder_format.py`

The capture's own format: a header first, the stable core keys on every line, a rising `seq`, both
planes, `None` left out, and the session-end marker last. The phase in progress is what a submission
is filed under; the reconciliation totals are filed at the session's end. A write that fails — a
full disk — is reported once on the session channel and never raised, because the recorder writes
from inside the executor's order path.

### `test_stream_projection.py`

The projection from the order-event stream into the capture:

| Class | What it holds |
|---|---|
| `TestEveryEventTypeIsDeclared` | Every event type is either written or declared silent |
| `TestEveryLineOfTheTable` | Each written type becomes the line the guide names, with the event's own status |
| `TestTheLineNamesThePhaseThatSubmitted` | A fill that arrives after its phase ended keeps that phase; a refusal before sending takes the phase in progress |
| `TestAnEndingWithoutADirection` | A close whose position is gone writes a line without a side, never a crash |
| `TestAnEndingCarriesItsOwnStatus` | A venue expiry is a cancel line with status `expired` |
| `TestOpenPartialAndFullClose` | Open, partial close and full close of one position through a real executor, in both account models |
| `TestAnOrderThatEndsAfterPartOfItExecuted` | The executed part is a fill line; the ending says who ended the rest and why |
| `TestTheVenueClosesInTwoSteps` | A protective stop executed in two parts: a partial close, then the full one |
| `TestTheUnaccountedEnding` | An unaccounted order names why it ended |
| `TestTheShortSide` | A short opens with a sell and closes with a buy — the side its slippage is signed by |
| `TestTheSlippageSources` | A fill carries what it is measured against: the mid at submission, its limit, its trigger |
| `TestTheVenueReads` | The start and end reads become the preflight and session-end snapshots; an unread book proves nothing; a reconciliation change becomes an alert |

### `test_certificate_analyzer.py`

The certificate over synthetic captures: a clean run certifies, a failed or aborted one does not,
and the end gate passes only on a counted, empty order book. Beside the verdict, every figure says
where it came from or that it is missing — the fees from the run's own report (or the capture's
fills where the report is gone or no longer reads), the account's movement, the slippage per order
type, the REST calls, and whether the session reconciled at all.

## Running

```bash
pytest tests/framework/field_study_recorder/ -v
```
