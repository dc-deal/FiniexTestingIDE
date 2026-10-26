# Unattended Operation — Alerts, the Liveness Ping and the Supervisor

A session that runs for weeks with nobody watching has one failure that looks exactly like
success: **silence**. An OOM kill, a reboot of the machine, a `kill -9`, a hung network stack —
none of them sends anything, and a quiet market sends nothing either. This document covers the
three pieces that turn that silence into a message (#235): **operator alerts** the session sends
about itself, a **liveness ping** whose absence an outside monitor turns into an alarm, and a
**supervisor** that brings a dead process back.

It also covers the two things a restart must survive: a second start of a bot that is still
running, and a deploy into a new code tree — and the daily check-in, which reads the session from
outside without touching it.

Not here: what the session does when it runs — that is
[AutoTrader Runtime Model](autotrader_runtime_model.md); how a restart picks up the open book —
[Live Execution Architecture](../architecture/live_execution_architecture.md), *Cold Start*; the
full configuration reference — [AutoTrader Configuration](autotrader_configuration.md).

## Before you leave a session alone

1. **Create a Telegram bot** with BotFather (`/newbot`), and send the bot `/start` from the chat
   that should receive the alerts — a bot can only write into a chat that addressed it first.
2. **Write its credential** to `user_configs/credentials/services/telegram_credentials.json`:
   `{"bot_token": "…", "chat_id": "…"}`. The chat id is printed by
   `python python/cli/operator_alerts_cli.py chat-id`, which reads the token from that file and
   never prints it.
3. **Create a check at a monitoring service, one per bot** — healthchecks.io works out of the
   box (free for 20 checks, open source, self-hostable): period 1 minute, grace 5 minutes. Write
   its URLs to `user_configs/credentials/services/liveness_<bot_id>.json`:
   `{"ping_url": "https://hc-ping.com/<uuid>", "fail_url": "https://hc-ping.com/<uuid>/fail"}`.
   One check per bot, because two sessions pinging one check would each vouch for the other's
   hung loop; `operator_alerts.liveness.credentials_file` names another file where needed.
4. **Switch both on** in the profile:
   ```json
   "operator_alerts": { "telegram": { "enabled": true }, "liveness": { "enabled": true } }
   ```
5. **Run the check** on the machine that will run the session:
   `python python/cli/operator_alerts_cli.py check --profile <profile>` — one test message and one
   ping, each reported as it went. The fail URL is not exercised: it would raise the monitor's
   alarm. At every session start the same check of the chat runs again — the chat shows "typing"
   for a moment — and a token or chat the bot may not use refuses the start.
6. **Put the process under a supervisor** (below), and decide `cold_start.adoption_mode` with it.

Both credential files are secrets: the token can post as the bot, and the ping URL can fake a
liveness ping. They stay in `user_configs/`; the tracked copy is a placeholder the session refuses.
A test run reads only the tracked copy, so it never reaches your bot or your monitor.

A **mock** session switches both off unless its profile switches them on itself — switched on in
`user_configs/app_config.json`, they would otherwise reach every test replay and feed the live
bot's monitor check.

## The daily check-in — how the session stands, read from outside

Once a day, from your own machine, five reads answer whether everything runs to plan. None of them
reaches into the session; each reads what the session recorded, through the API of the machine it
runs on:

```
1  GET /api/v1/activities                                   is the session's process alive?
2  GET /api/v1/reports/runs/{run_id}/state-frames/latest    how does it stand, how old is that,
                                                            what does its process hold?
3  GET /api/v1/machine-resources                            how full is the machine?
4  GET /api/v1/reports/runs/{run_id}/order-events           what happened to its orders?
5  GET /api/v1/deployments/{deployment_id}/booking-periods  what did each trading day book?
```

The second is the session's own latest state frame: its account, positions and resting orders,
worker outputs and decision, with `age_s` and `recording` — `current` while it writes on its
cadence, `stopped` when its frames stop without an end (the first read tells whether the process
died or hangs), `ended` once it wrote its last frame, with how it ended beside it. `first_frame`
beside the latest frame's `process_memory` is the memory's growth since the start — a leak shows
there, long before it fills the machine. The fifth answers for completed sessions only: a session
books its periods at its end, and yesterday's period of a session still running reaches you as the
daily close over Telegram (below). What each field means is the served document
[state frames](../consumer/state-frames.md).

## What the operator hears

Symptoms, not causes — every message either needs the operator or ends a state that did. A
symptom is one **alert episode**: its message goes out when it starts, and its all-clear only once
it has stayed away for `all_clear_after_s` (300 s by default). A breaker or a feed flapping around its
threshold therefore sends two messages, not one per crossing, and the message in between stays
true — a recurrence before the all-clear keeps the episode open.

| Moment | Mark |
|---|---|
| The session started — live, dry run or mock replay, run id, commit | ✅ |
| STARTUP FAILED — a step of the start broke | ⛔ |
| STARTUP REFUSED — a step of the start declined, quoting why: a restart that may not adopt under `operator_confirm`, a Field Study account that holds a resting order | ⛔ |
| REFUSED AT THE FIRST TICK, and what had been sent before it | ⛔ |
| RUNTIME ERROR — the session aborted | ⛔ |
| EMERGENCY STOP, and EMERGENCY STOP incomplete — check the account by hand (only when closes were sent; kept spot holdings are said in the first message) | ⛔ |
| TICK LOOP STALLED — the process is alive and not trading; with Telegram on, also when the liveness ping is off | ⛔ |
| SESSION CRASHED while shutting down — the report may be incomplete | ⛔ |
| The liveness ping was refused (wrong URL, deleted check) / its fail URL was refused | ⛔ / ⚠️ |
| Safety circuit breaker — new entries blocked / cleared | ⚠️ / ✅ |
| Market data stale / fresh again — including no first tick within `market_data_stale_after_s` of the loop's start, a feed that never connected | ⚠️ / ✅ |
| A signal input stale / fresh again | ⚠️ / ✅ |
| A trading day closed — its booking period: trades, net P&L and the open part, fees, equity at the close and at the open, the deepest dip; `daily_digest` switches it off. Days one late check closes together — a machine that slept, a clock that jumped — are the first day in full and one message for the rest, naming their span | ✅ |
| The session ended — its grade, exit code, why it was asked to end, duration, ticks, trades, net P&L and the open part, errors logged | by grade |

An abort or a refused start is one message, sent at once, before the shutdown — not a second one
at the end. A single reconnect is not on the list: when it matters, the market data goes stale,
and that is.

```
⚠️ Safety circuit breaker — new entries blocked
max_drawdown_abs (412.50 > 400.00) · 1 position(s) open, kept
eth_daily · kraken_spot ETHUSD
2026-10-20 03:12:05 UTC
```

The last two lines name the speaker, so several bots can share one chat, and the UTC time the
session raised it — a message may arrive later when Telegram asked us to slow down. Each has a
line of its own, because a phone breaks a long last line wherever it likes.

**The day's close is one record, distributed.** At the market's trading-day boundary the session
seals a booking period; its ledger row is the record the reports and the API read, and the session
log carries the same period in one line the moment it is sealed. The message renders that period
for the operator and computes nothing of its own:

```
✅ Day closed — 2026-10-20
Trades: 3
Net P&L: +12.40 USD
Fees: 1.85 USD
Equity: 1,000.00 → 1,012.40 USD
Deepest dip: -8.20 USD
eth_daily · kraken_spot ETHUSD
2026-10-21 00:00:05 UTC
```

One figure per line, in the account currency; a position still open adds an `Open P&L` line
beside the realised one, and a day the session only started in opens with a `Covers: from 14:32
UTC` line, because its figures are the rest of that day, not all of it. The session's own end
reads the same way — its reason, its duration, its ticks, its figures — and the reason is whoever
asked: a strategy, a hard stop, an operator's Ctrl+C or a supervisor's SIGTERM.

**What it never does: block the session.** The session hands a message to a queue and goes on;
a thread of its own paces the messages, honours Telegram's `retry_after`, and retries through the
connection ladder ([External Connection Policy](../architecture/external_connection_policy.md)).
An outage costs that one message. A refused token or chat closes the channel for the session,
said once in the session log — so the run is graded with errors rather than looking clean. A
full queue drops the oldest alert below CRITICAL first, and says so when it starts. What happens
to the closing message itself is in the session log only: it goes out after the run was graded.

**What was sent is in the run's own log.** Every message the channel accepted leaves one line in
the session log — its kind, its title, when the session raised it and when it went out:

```
📣 Sent day_closed · ✅ Day closed — 2026-10-08 · raised 00:00:05, delivered 00:00:06 UTC
```

The figures are not repeated there: a message is rendered from the run's records — a day's
close from its booking period, the end from the run's report — and those records are what to
open when a number needs checking. A message that never went out leaves no such line.

## The liveness ping — the alarm the session cannot send

Every alert above is sent by the session about itself, which covers one class of failure: the
process is alive and notices something. The ping covers the other class. It goes out every
minute — but only while the **tick loop** has come round within `loop_stall_after_s`:

```
  tick loop (every pass)            liveness thread (every interval_s)        monitor
  ──────────────────────            ──────────────────────────────────        ─────────────────
  stamp loop progress   ───────►    loop passed recently?
                                      yes → ping            ───────────►     last_seen = now
                                      no  → stop pinging,
                                            fail URL once,  ───────────►     ALARM at once
                                            alert the operator
                                                                             no ping for
                                                                             period + grace → ALARM
```

A ping sent because the PROCESS is alive would report a hung loop as healthy — the one failure no
supervisor sees, because nothing crashes and nothing exits. The watch ends with the loop: a
shutdown that takes longer than `loop_stall_after_s` is a shutdown, not a stall. A session that
ends in an emergency pings the fail URL, so the alarm comes at once; any other end of a loop that
passed sends one last ping, and the silence after it is the monitor's to read; a session refused
before its loop vouches for nothing and sends none, so the monitor raises its alarm after the
grace period; a crash while shutting down pings the fail URL too.
**Stopping a session on purpose: pause the check at the monitor**, or it will raise its alarm once
the grace period runs out. A refused ping URL stops the pings — the monitor then reports the
session silent, which is the right answer to a check that no longer exists; a refused fail URL
stops only the fail pings, and the ordinary pings go on.

With the ping off and Telegram on, the same watch runs without pinging: a stalled loop still
reaches the operator. What it cannot report is a process that died — that silence is the ping's.

Optionally the pinger also rewrites a `liveness_stamp_file` with the time of each ping, for a
watcher on the same machine.

## The supervisor — bringing a dead process back

The ping says the session is gone; a supervisor brings it back. **It restarts a crash, never a
decision** — and the exit code says which one it was:

| Exit | The session | Restart |
|---|---|---|
| 0 | ended because it was asked to | no |
| 1 | did not complete — an uncaught exception | yes |
| 2 | ended in an emergency — a hard stop, a runtime error, a start that failed for good, a second start of a bot that already runs | no |
| 3 | finished but logged errors — a start the cold start refused among them | no |
| 75 | a start that failed for a passing cause — the venue did not answer within its budget, or the first tick came before it had committed its bar | yes, after a pause |
| killed — `kill -9`, an OOM kill | ended by the operating system | yes |

Each of 0, 2 and 3 needs a person, and a restart repeats it. Exit 75 does not: a timeout at the
start after a nightly reboot is gone a minute later, and a venue's maintenance window ends; left
down, either would cost a trading day.
The code is the one `sysexits.h` reserves for a temporary failure. **Nothing carries a hard stop over
to the next session:** after one, a restarted session takes over what was kept and trips the stop
again at its first valued tick — or, when the risk baseline is not carried over, starts from the
drawn-down value and trades on. A supervisor that restarts exit 2 turns the hard stop into a
loop or into a pause. A refused start refuses again.

- **On a Linux server** — systemd says it in three lines of the unit: `Restart=on-failure`
  restarts a crash, a kill and exit 75, `RestartPreventExitStatus=2 3` keeps the session's own
  decisions down, and `RestartSec=60` puts a minute between two attempts — without it systemd
  starts again at once and gives up after five quick failures. From systemd 254 on,
  `RestartSteps=` and `RestartMaxDelaySec=` let the pause grow while the cause lasts. Exit 0 is
  not restarted anyway, and `systemctl enable` brings the session back after a reboot.
- **Not natively on Windows** — the session runs on Linux: a server, WSL or a container. The lock
  that keeps one bot from running twice is a file lock of the POSIX kernel.
- **In Docker** — the restart policies decide on whether an exit code is zero, not on which one:
  `unless-stopped` restarts every end and `on-failure` every non-zero one, a hard stop included.
  Neither fits; restart a session in Docker by hand after its alarm, or run it under a supervisor
  that reads the exit code. Docker Desktop must itself start when the user logs in.
- **Task Scheduler alone is not enough**: its restart triggers on a FAILED run, and a hung
  process never ends. That case is the liveness ping's.

**One bot runs once.** A second start of a bot whose session is still running is refused before
it reads or sends anything — exit 2, not restarted — with a message naming the running session's
run, its process and the lock file. systemd never starts one unit twice; the refusal is for
everything else: a start from a second terminal, a manual start after a liveness alarm while the
hung process lives on, two units for one bot, or a deploy's new code tree started before the old
session has ended. The lock lies beside the bot's carry-over and is held until the session has
written its last record; the kernel drops it with the process however that ends, so there is
never a stale lock to clear.

**The restart and the adoption are one decision.** A session that starts restores the previous
session's position book whatever the adoption mode (at spot, in a real-money session — a dry run
restores none); what the adoption may refuse is the start itself, over an ORDER. When an order of
ours still rests at the venue, `cold_start.adoption_mode: operator_confirm` asks a person, and a
session started without `--attended` has nobody to ask: it refuses to start rather than trade
beside an order it cannot account for. A refused start restores nothing — the book waits on disk
for the next start, and the holding sits at the venue unmanaged, protected only by a stop the venue
itself holds. At 03:00, unattended, that is a bot that stays down until somebody reads the alert.
The STARTUP REFUSED alert tells you, quoting the cold start. Whether a restart may adopt such
orders on its own (`adoption_mode: auto`, or a decision logic that accounts for each of them by
name) is the profile's decision, made before the run, not discovered during it. A bot that rests
nothing — market orders only — restarts the same way in both modes.

**A restart runs what is on disk.** The supervisor starts the same command again, so the restarted
session runs the code and the profile it finds at that moment, and records both in its own header.
A real-money start refuses uncommitted code, but a clean new commit — a `git pull` between two
restarts — passes, and the deployment history draws no line for it: it compares the strategy
parameters, the operational settings and where the orders went, never the code. A backtest of the
whole period runs at one commit. For a run you mean to compare, leave the checkout, your strategy's
repository and the profile alone until it ends, or read each session's `code_identity` from its run
header before you compare: it names the commit of every repository a component came from, while the
ledger row names only this repository's commit and the component versions — see
[A run is a record, and an old one stays true](../introduction_to_the_ide.md#a-run-is-a-record-and-an-old-one-stays-true).

## A deploy — a restart into a new code tree

A fix during a long run is a deploy: the session stops, the new tag is checked out BESIDE the old
tree, and the session starts from there. Not a checkout in place, because that changes the files
under every process still running from them and leaves no old tree to step back to. The new tree
has to find what the old one wrote — the carry-over above all — so the operator's state lives
apart from the code, in one **data home** that the environment variable `FINIEX_DATA_HOME`
names: the workspace (`user_configs/`), the strategy directories and every store the session
writes. The code tree keeps what a tag carries: `configs/`, `docs/`, the code. Unset, the working
directory is the home, as on a development machine. A named home that does not exist refuses
the start; it is never created on the fly. See
[Data Storage Layout](../architecture/data_storage_layout.md) for which store lives where.

**A session trading real money proves it continued, or refuses.** Before its first order it names
the data home and the carry-over it read — the run that wrote it and when. A session that finds
no carry-over it can use — none, an unreadable one, one in another format, or another profile's —
refuses to start with exit 2: that is a start in the wrong home caught before it trades blind
beside a holding the venue still has. The bot's very first real-money start is declared,
`--first-start`, and the run header records the declaration; a declared first start that finds
a carry-over it could use refuses too, so a flag left behind in a unit cannot switch the rule
off. A dry run and a mock session still start fresh on an empty carry-over: they claim nothing
about the venue.

```
1  systemctl stop <unit>          SIGTERM → normal shutdown: carry-over and ledger row written, exit 0
2  run_index_cli.py status        the stopped session completed — run with FINIEX_DATA_HOME set
3  git checkout of the new tag beside the old tree (a clone or a worktree), its requirements
   installed in an environment of its own — a checkout, never an export: a tree git cannot
   read counts as uncommitted, and real orders refuse to start from it
4  the unit points at the new tree; the home and the profile path stay
     WorkingDirectory=/srv/finiex/releases/v1.5.1
     Environment=PYTHONPATH=/srv/finiex/releases/v1.5.1
     Environment=FINIEX_DATA_HOME=/srv/finiex/home
5  systemctl start <unit>         the boot names the home and the carry-over it continued
6  GET /api/v1/reports/runs       the new tag's commit, the same installation, the same deployment
```

The code is imported from `PYTHONPATH`, not from the working directory: left on the old tree, the
new command runs the old code, and step 6 shows the old commit. The API server's unit moves to
the new tree in the same step and names the same home, so a store's reader and its writer never
run two versions. Going back is the same steps with the old tree. The stop must finish inside
systemd's stop timeout (`TimeoutStopSec`, 90 s by default); a kill after that costs the session
its ledger row, never its carry-over.

## What the venue could do, and why it does not here

Kraken offers the mirror of the ping: a dead man's timer that cancels the account's orders when
the client stops refreshing it. It is not armed, deliberately — it would cancel a stop the venue
holds exactly when the stop is needed. The reasoning is in
[Protective Levels](../architecture/protective_levels.md), section 7.

## Related

- [AutoTrader Configuration](autotrader_configuration.md) — the `operator_alerts` block
- [Credentials Layout](../architecture/credentials_layout.md) — the `services/` directory
- [External Connection Policy](../architecture/external_connection_policy.md) — the alert
  channel's ladder and the ping's cadence
- [Operator Alerts Tests](../tests/autotrader/operator_alerts_tests.md) — the suite
