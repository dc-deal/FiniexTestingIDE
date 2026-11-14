# Divergence Report Tests

`tests/framework/divergence_report/` — the comparison of a live session with a backtest of exactly
its window (#512): what the backtest is built from, which trading days of the live side are
compared, and what the report measures.

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
| A live day slips into the comparison although its record is incomplete, its session was killed in it, or no session saw its opening | The divergence measures a day the live account did not trade the way the backtest did |
| A day's order events are counted otherwise than the executor counted them | Every day with a partial fill the venue ended reads incomplete and drops out |
| A segment's window starts at the first period's opening instead of the first tick | The backtest decides on bars the session never saw |
| A position changed by hand across a restart goes unnoticed | The days after compare two different accounts |

Not here: the warmup read back from a record, bar for bar against the archive path — that is the
warmup history suite (`test_warmup_from_record.py`); the count of a day's order events held to the
live executor's own counts on every way an order lives — that is `test_order_event_count_pin.py` in
the [Order Events Tests](order_events_tests.md).

The live side's tests build their deployments with `tests/shared/divergence_live_chain.py`:
sessions in memory with their ledger rows in the shape the ledger writes them, how each ended, their
first ticks and state frames, and the day records each sealed — on the crypto market, starting five
minutes after midnight the way the thirty-day run does. The collector's own tests write real run
directories instead.

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

### `test_live_side_days.py`

The trading days as the report places them, and the counting a day is checked by.

| Test | Description |
|------|-------------|
| `TestTheGridIsTheRecorders` | the booking period recorder, driven hour by hour, seals exactly the grid's days with the grid's closings — the crypto anchor at midnight UTC, and the forex anchor at 17:00 New York across a weekend and the switch back to winter time; the forex grid skips the closed days; a grid that closes no day is empty |
| `TestTheCountDeclaration` | every order event is counted in one column or declared uncounted; every count is counted or declared uncompared; its endings are the executor's own two maps composed |
| `TestADaysEventsAreCountedAsTheExecutorCounts` | an order submitted and filled; the step after a partial fill — an ending carrying `cum_lots` — counts nowhere; a cancel with nothing executed counts; an adoption counts as adopted |
| `TestThePositionAtABoundary` | the last frame strictly before the boundary; a short is negative and flat is empty; no frame before it reads nothing |
| `TestTwoPositionsAgree` | within exactly one lot step; flat against flat; a side without a frame is apart; at spot the base currency's holding decides too |
| `TestPairing` | compared days pair with the backtest's rows by their closing, a day without a counterpart says so; orders pair by rank — the live ones by session, then `seq` — and what is left over is listed per side |

### `test_live_side_segments.py`

| Test | Description |
|------|-------------|
| `TestTheBoundaryDetectors` | every boundary kind but the deployment's start has exactly one detector; the same code is one segment; a code change begins one and names what moved; a stand nobody could read counts as moved; a session without booked figures never cuts; a further boundary kind is one more detector, and one segment names both boundaries |
| `TestSessionsThatCoverNoDay` | a session that failed before it measured anything, one that refused at its first tick and one that recorded neither a tick nor a frame are named with why |
| `TestWhereASessionEnds` | a close row with its report says closed, Ctrl+C or crashed (its cause quoted); without its close a session was killed when a later one followed, and is open when none did |
| `TestTheWindow` | it starts at the first tick, never at the first period's opening; it ends at the boundary asked for once sealed, at the last sealed boundary while that one is not, and there without one asked for; a requested end that is no boundary is refused; no recorded first tick leaves it without a start |
| `TestAStopThatChangedTheAccount` | a declared stop ends the window at the last boundary before it; a position that moved across a restart with no stop declared ends it as an undeclared account change; a stop declared as changing nothing lets it run on; the same position within one lot step is no change |
| `TestTheReplaysInputs` | a segment's window gives what a backtest of it starts from — the first session's warmup run, the window's ends, the first frame's balances, the configuration ids; a window without a start cannot be replayed |
| `test_nothing_reads_a_clock` | the builders run with a clock that fails when read, and the same records give the same segments |

### `test_live_side_completeness.py`

| Test | Description |
|------|-------------|
| `TestADaysRecordAgainstItsRow` | complete; incomplete with every disagreeing count named; a partial fill the venue then ended is complete; every way a day cannot be checked — no record, unreadable, mislabelled, a line cut off, an event after the day closed, a row without a count, pruned — says why |
| `TestTheThirtyDayRunAsPlanned` | one session still running: every sealed day compared; the first day, whose first tick comes five minutes after midnight, is exempt from its opening |
| `TestAKilledSession` | killed and restarted the same day: that day excluded, the days after wait for the positions; restarted two days later: the empty day and the restart day named; an emergency shutdown is a kill with its cause quoted; an orderly stop is named as a stop |
| `TestALateSealOverSeveralDays` | a machine that slept: the days its loop did not run through are named once, the day after waits; a day another stream sealed but whose order record is missing is not checked rather than a loop that stopped; a session killed between booking a day and sealing its records leaves that day not checked |
| `TestGuardsAgainstADaySlippingIn` | a day sealed twice by overlapping sessions; a record holding a fill nothing was booked for reads incomplete; pruned records; the days after a declared stop lie after the window; without a first tick no day is placed |
| `TestPropertiesOfEveryClassification` | every grid day appears; a day is included exactly when no cause holds; every cause has its sentence and names its rule section; which causes break the record |
| `TestTheResumption` | apart, apart, then the same: compared again from there; exactly one lot step counts as the same; a side without a position is apart; the live positions come from each day's last frame |

### `test_live_side_collector.py`

Real run directories, each session written the way a session writes itself: registered under its
deployment in the run index, its order events and state frames sealed into day records by the
stream sealer, its warmup history and warnings-errors report beside them, its periods through the
ledger's writer.

| Test | Description |
|------|-------------|
| `TestTheSessionsWithoutAStream` | every session with its booking state, first tick, outcome, frame ends, lot step and base currency from the frozen document, and code stand; a session only the run index names has booked nothing yet; a deployment nobody names and one naming two brokers are refused |
| `TestTheSealedDays` | each day's records — its events in `seq` order, its label, its last frame, its position at the closing; an unbroken session is compared every day and the restart after its stop waits; a day record gone is not checked |
