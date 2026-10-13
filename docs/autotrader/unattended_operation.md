# Unattended Operation — Alerts, the Liveness Ping and the Supervisor

A session that runs for weeks with nobody watching has one failure that looks exactly like
success: **silence**. An OOM kill, a reboot of the machine, a `kill -9`, a hung network stack —
none of them sends anything, and a quiet market sends nothing either. This document covers the
three pieces that turn that silence into a message (#235): **operator alerts** the session sends
about itself, a **liveness ping** whose absence an outside monitor turns into an alarm, and a
**supervisor** that brings a dead process back.

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
| The session ended — its grade, exit code, why it was asked to end, duration, ticks, trades, net P&L and the open part, errors logged | by grade |

An abort or a refused start is one message, sent at once, before the shutdown — not a second one
at the end. A single reconnect is not on the list: when it matters, the market data goes stale,
and that is.

```
⚠️ Safety circuit breaker — new entries blocked
max_drawdown_abs (412.50 > 400.00) · 1 position(s) open, kept
eth_daily · kraken_spot ETHUSD · 2026-10-20 03:12:05 UTC
```

The last line names the speaker, so several bots can share one chat, and the UTC time the
session raised it — a message may arrive later when Telegram asked us to slow down.

**What it never does: block the session.** The session hands a message to a queue and goes on;
a thread of its own paces the messages, honours Telegram's `retry_after`, and retries through the
connection ladder ([External Connection Policy](../architecture/external_connection_policy.md)).
An outage costs that one message. A refused token or chat closes the channel for the session,
said once in the session log — so the run is graded with errors rather than looking clean. A
full queue drops the oldest alert below CRITICAL first, and says so when it starts. What happens
to the closing message itself is in the session log only: it goes out after the run was graded.

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
| 2 | ended in an emergency — a hard stop, a runtime error, a start that failed | no |
| 3 | finished but logged errors — a start the cold start refused among them | no |
| killed — `kill -9`, an OOM kill | ended by the operating system | yes |

Each of 0, 2 and 3 needs a person, and a restart repeats it. **Nothing carries a hard stop over
to the next session:** after one, a restarted session takes over what was kept and trips the stop
again at its first valued tick — or, when the risk baseline is not carried over, starts from the
drawn-down value and trades on. A supervisor that restarts exit 2 turns the hard stop into a
loop or into a pause. A refused start refuses again.

- **Natively on Windows** — a service wrapper such as NSSM can be told exactly that:
  `nssm set <service> AppExit Default Restart`, then `AppExit 0 Exit`, `AppExit 2 Exit` and
  `AppExit 3 Exit`.
- **On a Linux server** — systemd says it in two lines of the unit: `Restart=on-failure` restarts
  a crash and a kill, and `RestartPreventExitStatus=2 3` keeps the session's own decisions down;
  exit 0 is not restarted anyway. `systemctl enable` brings the session back after a reboot.
- **In Docker** — the restart policies decide on whether an exit code is zero, not on which one:
  `unless-stopped` restarts every end and `on-failure` every non-zero one, a hard stop included.
  Neither fits; restart a session in Docker by hand after its alarm, or run it under a supervisor
  that reads the exit code. Docker Desktop must itself start when the user logs in.
- **Task Scheduler alone is not enough**: its restart triggers on a FAILED run, and a hung
  process never ends. That case is the liveness ping's.

**The restart and the adoption are one decision.** A restart finds the previous session's orders
and positions; with `cold_start.adoption_mode: operator_confirm` and no terminal attached it
refuses to adopt them and stays flat — at 03:00, unattended, that is a bot that is up and does
nothing. The STARTUP REFUSED alert tells you, quoting the cold start; whether a restart may adopt
on its own (`adoption_mode: auto`) is the profile's decision, made before the run, not discovered
during it.

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
