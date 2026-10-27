# Execution Counters Tests

`tests/framework/test_execution_counters.py` — that the executor's order counts are kept over the
run and over the running booking period at once, so a period's counts are its own and a run's
periods add up to its execution statistics. Runs under the synthetic `framework/_root` suite.

## Why a period counts for itself

A period's share of a run total is not the difference of two readings of that total: subtracting
cumulative readings happens to work for a count and is the habit that breaks every other figure.
So each count is taken once and reaches both spans, and a seal takes the period's counts and starts
a fresh one (#476). The parity suite then shows the same in both pipelines over a real day boundary
([Booking Period Parity Tests](../parity/booking_period_parity_tests.md)).

## What Is Tested

| Test | Description |
|------|-------------|
| `TestACount::test_reaches_the_run_and_the_period` | a submission, an adoption and a protective close reach the run and the period alike |
| `TestACount::test_every_status_is_counted_in_its_own_field` | every status lands in the field its map names, and nowhere else |
| `TestACount::test_a_pending_row_is_no_ending_and_counts_nothing` | the status declared uncounted moves no count |
| `TestAPeriod::test_is_taken_once_and_starts_afresh` | taking a period's counts returns them and leaves the next period at zero |
| `TestAPeriod::test_leaves_the_run_counting_on` | the run keeps counting across a taken period, and the periods add up to it |
| `TestAPeriod::test_a_read_of_the_run_cannot_move_its_counts` | the run's counts are handed out as a copy |

## What Is Not Tested Here

Which executor path counts what — a submission, a booked row, a protective close — is tested with
the executors, in the simulation and live-executor suites. How a period is sealed and booked is in
[Reporting Tests](reporting_tests.md) and the ledger tests of the optimization suite.
