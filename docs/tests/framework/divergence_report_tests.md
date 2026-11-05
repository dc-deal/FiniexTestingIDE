# Divergence Report Tests

`tests/framework/divergence_report/` — the comparison of a live session with a backtest of exactly
its window (#512): what the backtest is built from, and what the report measures.

Run: `python -m pytest tests/framework/divergence_report/ -v`
Launch entry: `🧩 Pytest: Divergence Report (All)`

---

## What this suite exists to catch

A backtest of a session's window is only a comparison when it runs what the session ran. Built
from the configuration files of the day it would run what they say now: a runtime cache refreshed
since, `app_config.json` edited since, a default moved by a release. So every input comes from the
session's record, and each seam that carries one is held here.

| Failure | What it costs |
|---|---|
| A block of the rendered profile reaches no part of the backtest, unnoticed | The backtest runs another configuration than the session, and the divergence measures that |
| The projected component type stays in the strategy | The factory reads a parameter the strategy never had |
| A workspace override changes the simulator | The comparison measures a latency or spread model nobody chose |
| A recorded warmup of another symbol is accepted | The backtest warms up on the wrong market |
| The batch reads the broker file instead of the frozen document | Fees and symbol specifications of today replace the session's |
| One broker configured from two sources in one run | One of the two scenarios runs with the other's configuration |

Not here: the warmup read back from a record, bar for bar against the archive path — that is the
warmup history suite (`test_warmup_from_record.py`).

## The tests

### `test_recorded_inputs.py`

| Test | Description |
|------|-------------|
| `TestEveryBlockIsDeclared` | every block of a rendered profile, and every field of its execution block, is declared once — where it goes in the backtest, or why it has no counterpart — and says why |
| `TestTheScenarioSet` | the built set names the session's warmup run and broker document and the window; the strategy is the rendered one without the projected type keys; the heartbeat cadence, the frame cadence and the order guard are carried; every field of the execution model and of the simulator model is written, so no workspace override reaches one; a regular comparison declares no purpose; another history depth and a document without blocks are refused |
| `TestTheLoaderReadsTheRecordedWarmup` | a scenario naming `warmup_from_run` carries that run's record; a record of another symbol is refused |
| `TestTheFrozenBrokerDocument` | a scenario naming `broker_config_document` trades with the frozen document; an unknown document is refused at load; a document no configuration can be built from is that scenario's error; one broker from a document and from another source in one set is refused |

### `test_inputs_of_record_events.py`

One scenario of the backtesting validation set runs four times in this process (`DEBUG_MODE`): the
archive's warmup and the broker file with frames off, then frames on, then the archive's own warmup fed
back as a record, then the broker file frozen and read back as a document. Each run must give the same
order events and trades, event by event, as the first.

| Test | Description |
|------|-------------|
| `TestTheBaselineIsWorthComparing` | the first run placed orders and closed trades — a comparison over nothing would prove nothing |
| `TestFramesChangeNothing` | the frames were written (cadence, order and end frames, no memory reading); the same order events and trades with frames on as off |
| `TestFramesChangeNothingAtSpot` | the same at spot, where a frame that re-marked a position would change what the decision reads next — trades equal, excursions included |
| `TestTheRecordedWarmupIsTheArchiveWarmup` | the record came from the archive; the same order events, trades and probe bar snapshots when it is fed back |
| `TestTheBrokerDocumentIsTheBrokerFile` | the same order events and trades with the frozen document as with the file |
