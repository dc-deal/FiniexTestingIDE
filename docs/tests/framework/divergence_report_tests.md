# Divergence Report Tests

`tests/framework/divergence_report/` — the comparison of a live session with a backtest of exactly
its window (#512): what the backtest is built from, whether a backtest may be compared with the
session at all, which trading days of the live side are compared, and what the report measures.

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
| A file the backtest's code path reaches changes after the session's release, undeclared — or a declared one changes again | The divergence measures a change of code, not parity |
| The backtest runs another strategy, strategy configuration, broker document or window than the session | The divergence measures that difference instead of the simulator |
| A measure reports a divergence between a run and itself | What it measures is the instrument, not the difference between the two runs |
| A day whose decision frame is missing on one side counts as agreeing | A one-sided day the verdict should have named slips through |
| A stretch one side did not see stays in a measure | The divergence measures a capture or an outage, not the simulator |
| The parts of a day's change of account value do not add up to the change | The attribution names a cause for a divergence that is not where it says |

Not here: the warmup read back from a record, bar for bar against the archive path — that is the
warmup history suite (`test_warmup_from_record.py`); the count of a day's order events held to the
live executor's own counts on every way an order lives — that is `test_order_event_count_pin.py` in
the [Order Events Tests](order_events_tests.md).

The live side's tests build their deployments with `tests/shared/divergence_live_chain.py`:
sessions in memory with their ledger rows in the shape the ledger writes them, how each ended, their
first ticks and state frames, and the day records each sealed — on the crypto market, starting five
minutes after midnight the way the thirty-day run does. The collector's own tests write real run
directories instead. The gate's tests start from `tests/shared/divergence_gate_pair.py`, a session
and a backtest the gate lets through, and change one thing each.

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
| `TestADaysRecordAgainstItsRow` | complete; incomplete with every disagreeing count named; more executions than counted and nothing else apart is complete, the surplus counted as unbooked; fewer executions, or more beside another difference, is incomplete; a partial fill the venue then ended is complete; every way a day cannot be checked — no record, unreadable, mislabelled, a line cut off, an event after the day closed, a row without a count, pruned — says why |
| `TestTheThirtyDayRunAsPlanned` | one session still running: every sealed day compared; the first day, whose first tick comes five minutes after midnight, is exempt from its opening |
| `TestAKilledSession` | killed and restarted the same day: that day excluded, the days after wait for the positions; restarted two days later: the empty day and the restart day named; an emergency shutdown is a kill with its cause quoted; an orderly stop is named as a stop |
| `TestALateSealOverSeveralDays` | a machine that slept: the days its loop did not run through are named once, the day after waits; a day another stream sealed but whose order record is missing is not checked rather than a loop that stopped; a session killed between booking a day and sealing its records leaves that day not checked |
| `TestGuardsAgainstADaySlippingIn` | a day sealed twice by overlapping sessions; a record holding a fill nothing was booked for stays compared and names the execution the book did not book; pruned records; the days after a declared stop lie after the window; without a first tick no day is placed |
| `TestPropertiesOfEveryClassification` | every grid day appears; a day is included exactly when no cause holds; every cause has its sentence and names its rule section; which causes break the record |
| `TestTheResumption` | apart, apart, then the same: compared again from there; exactly one lot step counts as the same; a side without a position is apart; the live positions come from each day's last frame |

### `test_live_side_collector.py`

Real run directories, each session written the way a session writes itself: registered under its
deployment in the run index, its order events and state frames sealed into day records by the
stream sealer, its warmup history and warnings-errors report beside them, its periods through the
ledger's writer.

| Test | Description |
|------|-------------|
| `TestTheSessionsWithoutAStream` | every session with its booking state, first tick, outcome, frame ends, lot step and base currency from the frozen document, code stand, release, rendered strategy and the code identity of its header; a session only the run index names has booked nothing yet; a deployment nobody names and one naming two brokers are refused |
| `TestEverySessionsOutages` | a session's tick-stream outages read back as written; a session without the record has none |
| `TestTheWarmupsReach` | the longest stretch a timeframe's required bars span; a record naming no timeframe has none |
| `TestEverySessionsFrames` | each session's state frames read whole across its sealed days, in time order; a session that records none says so |
| `TestTheSealedDays` | each day's records — its events in `seq` order, its label, its last frame, its position at the closing; an unbroken session is compared every day and the restart after its stop waits; a day record gone is not checked |

### `test_import_closure.py`

The closure of a code path is computed from source text, so a synthetic tree of a few files shows
every way an import reaches a file.

| Test | Description |
|------|-------------|
| `TestTheClosure` | `from a import b` reaches `a` and the module `b`, a plain import and a conditional one count; an import naming no file and a relative one add nothing; an excluded prefix is neither held nor followed; on this tree the path holds its entry points and no served model, and none of the divergence report's own modules |
| `TestTheSyntaxTree` | docstrings, comments and layout leave the tree and its digest unchanged; a changed value and a string that is no docstring change it |

### `test_divergence_gate_code_path.py`

A repository in `tmp_path` laid out like the backtest's path — entry points, a data-preparation
module, a served model — with a release commit; each test commits one change on top and reads both
trees back through git, as the gate does.

| Test | Description |
|------|-------------|
| `TestReadingATreeAtACommit` | every file of the directory with the suffix, at the commit; a commit the repository does not hold reads nothing |
| `TestTheCodePathFileByFile` | unchanged and docstring-only pass; a code change is refused undeclared and passes declared at its tree; a declared file that changed again is refused, a docstring fix keeps its declaration; a file entering the path — new or newly imported — and one leaving it are refused undeclared; a served model is outside the path; a malformed declaration is a fault |
| `TestTheGateOverTheCode` | the release against an unchanged commit is comparable; an undeclared change is refused with the file; a release without declarations permits no change; an uncommitted session or backtest, a commit the repository does not hold, sessions on two commits, a session whose code or release cannot be read are refused |
| `TestTheGatesOwnDeclarations` | every declaration names known categories and a full digest; every category is declared somewhere |

### `test_divergence_gate_checks.py`

The code path is judged over an empty tree here, so each check stands alone.

| Test | Description |
|------|-------------|
| `test_the_pair_as_built_is_comparable` | the starting pair passes every check |
| `TestTheStrategy` | a component of different code, one on one side only, the strategy repository on another commit or uncommitted on either side are refused; a repository is compared by its label, never by its root |
| `TestTheRenderedStrategy` | the strategy as both sides rendered it: a deep value and a default filled in on one side only are refused by their path; key order does not count; a session or a scenario whose rendered strategy cannot be read is refused |
| `TestTheBrokerDocument` | another frozen configuration, and a session without one, are refused |
| `TestTheWindow` | a first tick more than the gap bound after the window's start, a last tick more than it before the end, a tick limit, a scenario without ticks, a window without an end and a backtest without a scenario are refused; a first tick within the bound passes |
| `TestWhatTheBacktestRecorded` | an order feature beyond the venue, a backtest without a ledger row and a scenario without state frames are refused |
| `test_every_refusal_is_an_error_with_a_declared_check` | every refusal is an error whose check id the catalog declares |

### `test_backtest_side_collector.py`

A real run directory, written the way a backtest writes itself: its header, registered in the run
index, a state-frame stream per scenario, its scenario details, its ledger row through the ledger's
writer, and its rendered scenario set in a run-config store.

| Test | Description |
|------|-------------|
| `TestTheRunAsItRecordedItself` | the header's release and code identity, the ledger row's broker document, each scenario's declared window, first and last tick, frames and rendered strategy from the run-config store; a scenario without frames says so; missing scenario details and a run naming no rendered set are named, never filled in; a tick limit leaves the window open; a run nobody names and a session are refused |
| `TestWhatAScenarioRecordedAsItRan` | a scenario's own order events, taken from the run's one stream that holds every scenario's, and its frames |
| `test_the_gate_takes_what_the_collector_read` | the gate passes the pair the collector read |
| `test_the_render_stamps_of_the_bar_files_the_backtest_read` | each timeframe's bar file and its render stamp; an unknown stamp reads as none, a timeframe the index lacks is left out |
| `test_the_trade_ids_of_a_windows_ticks` | the trade ids and event times of the ticks inside a window, a tick without an id left out |

### `test_paired_difference_statistics.py`

| Test | Description |
|------|-------------|
| tests at module level | the mean and the tracking error of a series worked by hand; the 90 % interval with the t quantile of its degrees of freedom, the wider one between two tabulated degrees; the lag-1 autocorrelation and its Ljung-Box statistic; a trending series rejects independence; too few days give no figure, a series without variation no autocorrelation |

### `test_order_divergence.py`

One day's orders on both sides, built from the record's own types, with one thing changed per test.

| Test | Description |
|------|-------------|
| `TestAnOrderOnBothSides` | the same order shows nothing; a worse live fill is a positive execution difference, for a buy and for a sell; the live fee rate against the taker rate, and a fee the venue did not report is not checked; a partial fill counts its executed lots, several fills are priced by their lot-weighted mean; a refusal is named with its cause; the size against the two account values; the delay from the decision mid apart |
| `TestWhatIsLeftOver` | an order on one side only is listed, and the fills are counted per side; an order whose record names no side measures no price |
| `TestTheDecisionFrame` | found by the order it names, else the order frame written when the order was submitted; no frame of the pass gives none |
| tests at module level | a close sells what a long held and buys back what a short held; the taker rate comes from the frozen document, in percent |

### `test_equity_divergence.py`

| Test | Description |
|------|-------------|
| `TestTheSlots` | only slots both sides valued inside a compared day, the largest gap and the gap at the end; an unvalued frame is no slot |
| `TestTheDays` | each side changes in percent of its opening value; an excluded day is not measured; a side without a frame before the opening has no change; the days' differences read as a series |
| tests at module level | the valued frame before an instant skips unvalued ones and never takes the instant itself |

### `test_day_attribution.py`

A spot day worked by hand: 1000 USD at a mid of 3000, 0.1 ETH bought at 3001 for a fee of 0.60 —
decided at 2999, submitted at 3000 — and a mid of 3010 at the end.

| Test | Description |
|------|-------------|
| `TestOneSpotDay` | a paired fill splits at its decision and its submission and the parts add up; an unpaired fill stays whole; what was held at the start is valued over the day; a missing quantity leaves a residual; a fill outside the two frames belongs to another day |
| `TestWhatIsNotTakenApart` | a margin day says why; a day without frames at both ends; a spot day without a base currency |
| `TestTwoRuns` | the difference is taken part by part; a side that cannot be taken apart names itself |

### `test_divergence_measures_on_a_backtest.py`

Two fixture scenarios run once each with state frames, in this process: a spot account and a margin
account.

| Test | Description |
|------|-------------|
| `TestTheRunsAreWorthMeasuring` | each run filled orders and valued its frames |
| `TestTheEquationAtSpot` | every window of the spot run is taken apart and its parts add up to its change of account value; the fees it paid are seen |
| `test_a_margin_run_is_not_taken_apart_and_says_why` | the margin run is not taken apart, and says why |
| `TestARunAgainstItself` | held against itself, every decision agrees, every order measures nothing and the account values never part, in both account models; every part of the spot attribution is zero |

### `test_segment_measures.py`

The live side's deployment of a session killed and restarted the same day, against a backtest that
recorded the same orders and account values.

| Test | Description |
|------|-------------|
| `TestTheSegmentAgainstABacktestOfIt` | the days after the restart are compared where both sides are flat, and a position the backtest holds keeps a waiting day out; every compared day is paired with the backtest row of its boundary; the same records show nothing in any measure, every decision agreeing; what could not be read is named, and so is a comparison given no decision timeframe |

### `test_gap_list.py`

| Test | Description |
|------|-------------|
| `TestEachSource` | an archive gap across the window's edge counts; a run of missing trade ids longer than the bound, a short one is none; an outage between the venue's trades, else from loss to restoration, and none without an end; an outage reads back as written; a pause in the frames longer than the bound |
| `TestTheList` | gaps merge into stretches and an unconfirmed run leaves nothing out; a run the venue published trades in does; a run it published none in is refuted and leaves nothing out; a stretch holds its start and not its end |
| `TestWhatAGapLeavesOut` | a decision whose bar closed in a gap, and one whose pass came late after its bar closed; an order submitted or filled in a gap is named and left out; a slot in a gap leaves measure 3 |

### `test_decision_divergence.py`

Frames on both sides with a decision on the day bar's close; each test changes one thing.

| Test | Description |
|------|-------------|
| `TestTwoDecisions` | the same action and orders agree and are counted in the table; a different action is one-sided; an order one side sent alone is one-sided though the actions agree; a side without its decision frame is not checked, never agreeing |
| `TestTheProposedKind` | the live breaker held back the backtest's order; differing workers within a restart's warmup are a re-warmup; differing workers with no restart in reach are a capture, unconfirmed; agreeing workers and differing account values are the account value; a one-sided day with nothing differing stays unexplained |
| `TestTheDecisionFrame` | a pass that closed no decision bar is no decision; the segment's first day decides on its first tick |

### `test_venue_record.py`

What the venue's own record says: its trades inside a run of ids our archive lacks, its trades in
a decision bar, and its ledger's movements of the account. The venue's readers stand in as small
classes; the Kraken readers themselves are held in the venue history suite.

| Test | Description |
|------|-------------|
| `TestARunOfMissingIds` | the venue's trades strictly between the run's two archive trades are counted; ids it never published count none; a run without its ids is refused |
| `TestACaptureDifference` | the decision bar is the one that closed in the pass; only one-sided days proposed as a capture are fetched; trades the archive lacks confirm it, an archive holding every one refutes it |
| `TestTheAccountsMovements` | the bot's own trade moves nothing, a deposit, a conversion, a trade of another order and a trade without an order each count; another currency and a zero movement count nothing; the bot's orders are every handle its events carry |
| `TestTheLedgerDecidesTheWindow` | a movement ends the window at the last boundary before it; a ledger without movements overrides a position change the frames show; a movement before the window counts nothing; the live side carries the movements |
| `TestTheVenueSide` | each run is answered and a failed one is named; a venue without a trade list or a ledger reader says so; a ledger that gave up is never an empty one; the bars' trades |
