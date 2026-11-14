# Golden Samples Tests

`tests/framework/golden_samples/test_golden_samples.py` — every aggregate frozen in the golden
samples (#557), rebuilt through today's code and compared field by field:
`python/framework/fixture_catalog/golden_samples/`. What a sample holds and how it is frozen is
explained in [Fixture Catalog](../../architecture/fixture_catalog.md), section *Golden samples*.

## Why this suite exists

The aggregation refactor moves every aggregate the reports serve onto one component and must move
no number. A test written after the move can only say that the new code agrees with itself; the
samples were frozen before, from productions of the fixture catalog, so they say what the old code
computed. This suite rebuilds each aggregate from the frozen inputs and compares it with what the
runs recorded — a float to the last bit, a missing field a difference like any other. It reads only
the committed samples, starts no run and takes seconds.

When a change is MEANT to move a number, the failure names the sample, the aggregate, the run and
the field; the entry is then produced and frozen again, and the review reads the diff.

## What Is Tested

| Class | What it pins |
|---|---|
| `TestEveryFrozenAggregateRebuilds` | every block — portfolio headline, run summary, aggregated portfolio, trade analytics and per-scenario totals, execution totals and fill slippage, worker totals, profiling, feed stability without its counters, booking-period run and unit totals, the decision totals and the slowest performers, the failed orders counted, the warmup, the worker/decision breakdown — rebuilt from its frozen sections for every run it applies to; the read-time views — run list figures, the deployment list, detail and booking periods, the sweep list and detail — composed from the frozen ledger and run index rows |
| `TestTheSamplesCoverWhatTheyMustCover` | every catalog entry the samples are taken from is frozen; every block is checked by some sample; every view has a subject; whatever is frozen and cannot be rebuilt is frozen with content somewhere |
| `TestEachSampleSaysWhereItComesFrom` | each header names its production, its contract, when it was frozen, and for every run a commit and whether its tree was clean; its files are the runs it names, with the sections it lists |
| `TestTheFrozenSectionsMatchTheRun` | the declared frozen sections and the fields that hold them, both ways; no block needs a section a run cannot freeze |

## Running the Tests

```bash
pytest tests/framework/golden_samples/ -v
```

Launch entry: `🧩 Pytest: Golden Samples (All)`.
