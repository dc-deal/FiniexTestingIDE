# Suite Runner Contract Tests

`tests/framework/test_suite_runner_contract.py` — that the test runner names how a suite ended
instead of guessing from a number, and that its defaults are the ones the operator can read in
`configs/test_config.json`. Runs under the synthetic `framework/_root` suite.

## Why this file exists

While the runner ran one suite at a time, one question was enough: was the exit code zero. That
worked because the ways a run can end without a verdict were rare and visible — a hang stopped
everything, a killed process left the terminal empty.

Running several suites side by side splits that question. A suite whose tests failed names a
defect to fix. One the machine killed names something that ran out. One the runner gave up on
names a hang. One that never started names work nobody has done yet. The first asks for a code
change, the last three do not, and folding them together reports the machine's problem as the
code's — sending whoever reads the summary to the wrong place.

So the runner decides on a named outcome, and the summary separates three states: suites that
reached a verdict, suites that ran without reaching one, and suites the run never got to. A
count of passes means something only for the first group.

```
  framework/api                   ❌ timed out (exit -1)  (15m 00s)
  ─────────────────────────────────────────────────
  framework/api: timed out (exit -1) — its counts describe a fragment, not the suite
ABORTED (fail_fast) — no further suites were started
Suites: 28 with a verdict, 1 without, 59 never started
TOTAL: 3389 passed  (9m 38s on the clock, 24m 31s across suites)
```

## What it pins

| Test | What it holds |
|---|---|
| `test_every_outcome_is_reachable` | No outcome exists that nothing can produce — every member comes from an exit code, or is one of the two declared as coming from elsewhere |
| `test_nothing_reachable_is_declared_unreachable` | The reverse direction: the two declared exceptions really are unreachable from an exit code, so the declaration cannot quietly go stale |
| `test_pytest_documented_codes` | Each code pytest documents is read as pytest defines it, including 5 — no tests collected is an empty directory, not a failure |
| `test_a_signal_is_a_kill_not_a_test_failure` | A process ended by a signal reports a dead process, never a defect in the code |
| `test_an_undocumented_code_is_unknown` | A code nothing maps becomes UNKNOWN rather than the nearest guess |
| `test_only_a_finished_suite_has_counts_worth_reading` | Exactly the two outcomes that ran to the end carry usable pass and fail counts |
| `test_a_suite_never_started_does_not_fail_the_run_on_its_own` | A suite the run never reached is not a verdict about that suite; everything else that is not a pass fails the run |
| `test_defaults_are_mirrored_in_the_config_file` | The lane ceiling, the timeout and the fail-fast switch hold the same value in the model and in the file an operator reads |
| `test_the_file_is_accepted_as_it_stands` | The shipped configuration builds the model, so no key in it is one the model forbids |
| `test_a_misspelled_key_is_refused` | A typo is a hard error rather than a setting that silently does nothing |
| `test_a_run_always_has_at_least_one_lane` | Zero lanes would start nothing, which is worse than running one at a time |

## What it does not cover

It does not run the runner. Whether the lanes actually overlap, whether a timeout kills what it
should, and whether a suite's temporary directory stays its own are properties of a run, not of
these two declarations; they are exercised by running the suite itself.
