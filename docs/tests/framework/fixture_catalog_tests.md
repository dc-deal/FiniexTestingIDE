# Fixture Catalog Tests

`tests/framework/fixture_catalog/test_fixture_catalog.py` — the catalog of the runs a consumer pins
(#576): `python/framework/fixture_catalog/`. The catalog itself is explained in
[Fixture Catalog](../../architecture/fixture_catalog.md).

## Why this suite exists

A consumer replays its tests from runs it pins in the archive, and before the catalog nothing on
this side knew which: a rebuild deleted one, and the consumer found out from a failing capture. The
catalog makes each such run a committed procedure. This suite holds the procedure to its promises —
that every entry is a fixture by its own configuration, that a failed production never replaces a
good one, and that the report-coverage run still carries every state its consumers assert.

Every test runs under the session's isolated stores. The one real production writes its run, its
ledger rows and its record there, never into the operator's tree; the session sequence is run with a
session runner that starts nothing, because an AutoTrader session runs in a process of its own,
which the session's isolation does not reach.

## What Is Tested

| Class | What it pins |
|---|---|
| `TestTheCatalogIsComplete` | every entry is named once, asserts something and names a consumer; every source exists; every entry starts from a configuration that declares `"run_purpose": "fixture"` — a sweep through its base set; only a session sequence declares sessions and a bot |
| `TestTheRecordDerivesTheCurrentFixture` | a production reads back as recorded; the newest verified production is current; a failed one stays in the record and never replaces a good one; a cut-off last line hides nothing before it; an entry the catalog no longer declares has no current production, so its runs read as superseded |
| `TestASessionSequenceRunsEveryDeclaredSession` | each session runs from its own profile — the sequence's bot, the continuous deployment, its own replay day, a carry-over inside the production's own workspace — with its own flags (a new history, a kill before the close); a production that made no run is not verified; how each session ended reaches the record |
| `TestAProductionMakesExactlyTheRunsItDeclares` | a production with more runs than its entry declares fails its check — a run of the same name started elsewhere is not taken for its own; a sweep, whose runs are found by the sweep, is not counted |
| `TestASessionIsKilledAtItsMomentAndSaysWhy` | with a real process standing in for the session: it is killed the moment it is due, killed at the limit when it never is, and left alone when it ends first — each outcome names which, an error with its last stderr line |
| `TestAPropertyReadsWhatIsServed` | the two-histories check reads the deployment list's rows |
| `TestTheRunListSaysWhichFixtureIsCurrent` | the served `fixture_superseded`: `true` for a run of an older production, `false` for the current one, `null` for a run no production made — derived from the record beside the index |
| `TestTheReportCoverageEntryCarriesEveryProperty` | one real production of the report-coverage entry: exactly one run, every property held — the regression guard on the set itself |

## Running the Tests

```bash
pytest tests/framework/fixture_catalog/ -v
```

Launch entry: `🧩 Pytest: Fixture Catalog (All)`. The real production makes the suite take about
twenty seconds (measured 2026-10-08).
