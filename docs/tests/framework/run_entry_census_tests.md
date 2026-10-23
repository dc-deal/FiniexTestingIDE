# Run Entry Census Tests

`tests/framework/run_entry_census/` — real runs, held to what the run store declares a run
directory may hold (#621).

Run: `python -m pytest tests/framework/run_entry_census/ -v`
Launch entries: `🧩 Pytest: Run Entry Census (All)` · `🧪 AutoTrader: Run Entry Census`

Architecture: [Store Entry Members](../../architecture/store_entry_members.md)

---

## What this suite exists to catch

The declaration test (`tests/framework/store/test_run_entry_members.py`) reads the declaration
alone. Whether the writers keep to it can only be seen in what a real run leaves behind, and
that is two failures that look like nothing from the outside:

| Failure | What it costs |
|---|---|
| A writer adds a file nobody declared | The run list does not serve it, the pruner may take its folder for an orphan, and nothing says so |
| A member is declared that no run writes any more | The declaration promises a file a reader then looks for in vain |

Not here: what a report section or a stream line carries. Those are their own suites.

## The runs it makes

All of them land in the suite's isolated run tree and are removed afterwards; the whole suite
takes about 18 s (measured 2026-10-23).

- **A sweep** of two combinations over [`census_grid.json`](../../../tests/fixtures/run_entry_census/census_grid.json),
  each running two short BTCUSD windows with the robustness mode on, and the ranking written as the
  report command writes it. The decision logic is a probe
  ([`census_probe_decision.py`](../../../tests/fixtures/run_entry_census/census_probe_decision.py))
  that writes one diagnostics row and opens and closes one position, so every member that hangs on
  an order, a sink or the robustness mode is written.
- **A mock session** on [`run_entry_census_test.json`](../../../configs/autotrader_profiles/mock/run_entry_census_test.json)
  with the same probe and its state frames recorded.
- **The same session with its log files switched off**, which must still be a declared run with its
  header, reports and streams, and no log.
- **Three runs that stop early and still close:** a backtest over
  [`census_set_without_data.json`](../../../tests/fixtures/run_entry_census/census_set_without_data.json),
  whose only window lies before the archive, so its scenario is excluded before its process starts;
  a session its cold start refuses, after it opened its order record; and a session whose startup
  checks fail, before it opened its order record. The refusal and the failure are provoked by
  replacing one startup step in the test, never by a switch in the code.

## `test_run_entry_census.py`

| Class | What it pins |
|---|---|
| `TestNoFileWithoutAMember` | every file of every run is declared for that run's pipeline, every file of the sweep directory is a sweep member, and both sweep members are written |
| `TestEveryMemberIsWrittenSomewhere` | every member is written by a census run or named in the short table of exceptions with the test that covers it; what a pipeline always writes is seen in that pipeline's own runs, so a write one pipeline drops is not hidden by the other's; a member the census does write is not in that table; each named test exists |
| `TestEveryRunCloses` | every census run writes its footprint and reads clean against it: closed, nothing failed, lost or late; every member written only sometimes is judged at the close — only a scenario's diagnostics (its own process writes them) and the warmup history (decided by the workers at the start) may stay unjudged; a session without log files skips them and says why |
| `TestARunThatStopsEarlyClosesClean` | each of the three runs that stop early closes and needs no look; a file it never got to reads as skipped with the step it stopped before — the backtest's scenario log, the refused session's state frames and day log, the failed session's order record — while the refused session keeps the order record it opened |
| `TestTheStreamsAndTheIndexAgree` | every stream's header line carries the version the declaration names, and the run index row lists exactly the files the declaration puts in `artifacts` and `stream_files` |
| `TestTheCensusCanFail` | on a copy of a real run directory: a planted file is named, a file only the other pipeline writes is named, a deleted member is missed, a run without its footprint is not closed, a file lost after the close is missing, a stream with another version is named |
| `TestWithoutLogFilesTheRecordStays` | a session with its log files off still holds its header, its trades, its order events and its state frames, nothing undeclared, and no log |

The exceptions are the members no census run can produce: the acceptance studies' captures, the
cold-start section (written only when a boot inherits a position or a resting order), the block
splitting section (a profile run only) and the tick stream record (a venue's ticks only). The
suites that produce the two captures in a real run directory hold it to the declaration too —
`test_field_study_capture.py` and `test_security_study_stages.py`, through
`tests/shared/entry_census_helpers.py`. For the other three only the builder or the recorder is
tested, and no test writes the file where a run writes it; the cold-start section waits for a
session that keeps its own book across a restart (#304).
