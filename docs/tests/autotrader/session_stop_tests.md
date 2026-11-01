# Session Stop Tests (#630)

A supervisor kills a stopping session after a time it never tells the process. A session whose waits
do not fit under that time loses the last part of its stop without a word, and nobody finds out before
the first stop in an outage. This suite holds the settings of a stop, the budget its steps ask and what
the start says when the settings cannot keep their promise.

| Item | Value |
|---|---|
| Suite path | [tests/autotrader/session_stop/](../../../tests/autotrader/session_stop/) |
| Profile | [configs/autotrader_profiles/mock/session_stop_test.json](../../../configs/autotrader_profiles/mock/session_stop_test.json) |
| Pytest mark | `autotrader` (auto-applied via path) |
| Launch entries | `🧩 Pytest: Session Stop (All)` · `🧪 AutoTrader: Session Stop` |
| Configuration | [autotrader_configuration.md](../../autotrader/autotrader_configuration.md), the `shutdown` row of *Configuration* |

What a session leaves standing when it ends is not here: that is [Session End](session_end_tests.md).

## Suite Coverage

### `test_stop_budget.py` — what the stop may still spend

The instant the stop was asked for is handed in, a moment back on the monotonic clock, so nothing sleeps.

- **No kill declared:** the time to the kill is unknown rather than a number, there is time for anything,
  and the wait for orders keeps its own bound whatever is still to come.
- **A kill declared:** it counts down from the instant the stop was asked for, a step has to fit in front
  of the reserve for the records, and the wait leaves the reserve and what is still to come. A bound read
  again on a later pass ends the wait at the same instant as one read when it began.
- **Past the kill:** nothing is left, there is time for nothing, and the reserve ends the budget before
  the kill does.

### `test_stop_budget_check.py` — the check at the start, and the settings it reads

- **The waits against the kill:** the defaults fit a `docker stop -t 80`, waits that fill a kill exactly
  still fit, and waits beyond it are named with their figures, in a mock session too.
- **An unattended session:** told when a live session declares no kill. An attended one and a mock one
  are not asked.
- **The order wait against the poll:** below the broker's poll interval plus a second it can miss an
  order's second status read. A mock venue has no poll to miss.
- **What a finding is:** a warning on the session, with an id the check catalog declares.
- **The settings:** the sum of the waits counts every join and the flush, the defaults are mirrored in
  `app_config.json`, a misspelled key is refused at load, a wait below zero and a kill at no time are
  refused, and a rendered session keeps its composite key whatever its stop settings say. The signal
  stream's join of zero is refused too: not waited for at all, its thread would read abandoned on every
  stop; the other joins may be zero.

### `test_shutdown_status.py` — one status every surface of a stop reads

A supervised session has no terminal and its loggers buffer their console, so its stop said nothing
until the reports, and a stop that waits could not be told from one that hangs.

- **The declared steps:** each has its words and each word its step, what is still to do is the steps
  after the current one, and the first of them leaves the line as it was approved. A whole session walks
  them all, in the declared order.
- **The status:** what asked and when, the count the cleanup carries where it is known, an order's age
  where this session sent it, the wait's progress and what follows it by what it waits for (a resting
  order is left to the cleanup, not booked), and a kill that counts down from the reading, reads
  overdue once it has passed, and says no kill is coming where no signal asked for the stop.
- **What the wait came to:** every way an order can stand counts once, read off the order records, the
  last ending is the one that counts, an open and its close under one id are two orders, and an
  execution below the lots sent counts as filled in part.
- **The holder:** what asked first stays, a signal after it still says a kill is coming, each reading is
  measured against its own moment, a write replaces the snapshot and leaves a reader its own, the kill
  counts from the instant the budget counts from, a step ends what a crashed wait left on screen.
- **The journal:** a line reaches the session log always and the console flushed, stays off the console
  while the display holds it, and a console that is gone does not end the stop. What asked is said once,
  at the first step. The wait says when it begins, what settled, what it waits for every five seconds of
  silence, and what a run-out came to.
- **A signal handler writes nothing:** a real signal, delivered by `os.kill`, only notes what asked,
  and writes neither the journal nor the global log nor the buffered stdout. A print or a log write
  there can raise on the very write it interrupted. Ctrl+C's own notice goes to the stderr
  descriptor instead, and reaches it even with `print` made to raise.
- **A real SIGTERM in a whole mock session:** sent by `os.kill` on the main thread right after the order
  went out, so it lands inside a tick pass the way a supervisor's does, in both account models. The lines
  stand in order in the session log and on the console, the result says what the wait came to, and the
  closing message carries its line, with the unanswered count on a run-out.
- **The display through a stop:** the same signal with the live display running. It showed the order, it
  stops after the end read and before the reports, its last frame says what is left, and on a terminal
  the lines reach the console only once it stopped. With no terminal, a supervised session's case with
  the default profile, it shows only its last frame, and the journal still hears every line.
- **What a failure on the way must not do:** the number of orders to cancel raising does not end the stop; the steps go on, the number reads unknown, and it is said once.
- **A fill timeout inside the wait:** in a whole session under SIGTERM, an answer that comes after the fill timeout and within the wait is booked, exit 0, in both account models.
- **A crash while stopping:** a whole session whose stop raises still stops the display, sends the crash alert and flushes as an emergency.
- **Threads that outlast their join:** a display that did not finish its last frame is said by a warning on the session channel, one that finished says nothing, and the display reports a thread it could not join.
- **A display that fails:** it hands the console back to the stop's lines.
- **The last frame after the cleanup:** the display gets the book the cleanup and the venue's read left, before it stops.

### `test_stop_settings_in_a_session.py` — from the profile to what the session does

One mock session with every setting at a value of its own. The threads a replayed session has already
finished with are stood in for at the moment the stop begins, so each wait is read where it is spent.

- **The check in a session:** the finding stands on the session's record, is said in the log and on the
  console, enters the log pot as no warning, and refuses and grades nothing. On a live start, asked
  directly so that no venue is reached, the session hands the check its adapter and its broker, and
  its broker's poll interval: an order wait of 3 s is told it misses a second read 5 s apart, and not
  one 0.5 s apart.
- **Each wait takes its setting:** the tick thread, the signal transport, the request worker, the alert
  flush and the display.
