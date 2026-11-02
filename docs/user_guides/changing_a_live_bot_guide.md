# Changing a Live Bot — One Week, Four Composites

A bot that runs for weeks gets changed: a parameter that looked too eager, a safety limit that
looked too loose, a release with a fix. Each change takes a minute. What it does to the record is
harder to see — the trade that crosses it, the series it breaks, the backtest that now has to
reproduce two configurations instead of one.

This guide follows one invented bot through one week of real money and shows, day by day, what
the operator did, what the bot said, and what its record keeps. Every message is what the code
prints, shortened where marked.

**Not in this document:** the rule itself and its reference — *A restart under a changed
configuration* in [Unattended Operation](../autotrader/unattended_operation.md); how a deployment
is declared and read — [Deployment & Ledger](deployment_ledger_guide.md); what a composite is —
[Reading the Results Ledger](results_ledger_guide.md) and the glossary.

---

## The bot

`my_bot_btcusd` trades BTCUSD at Kraken spot. The shipped
[`configs/autotrader_profiles/production/ethusd_production.json`](../../configs/autotrader_profiles/production/ethusd_production.json)
is a complete profile of the same shape; the lines that matter this week are these:

```json
{
  "profile_name": "my_bot_btcusd",
  "bot_id": "mybot01",
  "symbol": "BTCUSD",
  "broker_type": "kraken_spot",
  "adapter_type": "live",
  "deployment": { "continuous": true },
  "session_end": { "orders": "leave" },
  "safety": { "enabled": true, "max_drawdown_pct": 25.0 },
  "strategy_config": {
    "decision_logic_type": "user_algos/my_bot/my_strategy.py",
    "decision_logic_config": { "rsi_oversold": 40 }
  }
}
```

`session_end.orders: leave` lets a resting order outlive a stop, so the next session takes it over.
The broker's own posture has to allow that, and real money is switched on there too — both in the
workspace override of `market_config.json`:

```json
{ "brokers": [ { "broker_type": "kraken_spot", "dry_run": false, "session_end_orders": "leave" } ] }
```

The bot runs under systemd with `Restart=on-failure` and `RestartPreventExitStatus=2 3`, as
[Unattended Operation](../autotrader/unattended_operation.md) describes.

## The week

### Sunday 20:00 — a rehearsal

The workspace override still says `"dry_run": true`, so the profile's first session is a dry run:
real ticks, simulated orders. It runs twelve hours, its simulated account peaks at 10,400 USD, and
its carry-over records the drawdown curve and the risk baseline — stamped as a rehearsal's. It
claims nothing at the venue: no session key, no position book.

### Monday 09:00 — real money

The operator sets `"dry_run": false` in the override and starts the profile by hand with
`--new-deployment` — once; the unit's own command carries no flags — so the real history does not
begin inside the rehearsal's deployment:

```
🔗 Starting deployment deploy_20261102_090012_5c1e8a3f — this session's ledger row joins that history
📉 The drawdown curve and the risk baseline start fresh with the new deployment (--new-deployment).
🎯 Risk baseline taken: session_fixed = 10000.00 (…)
```

Without the flag the session would continue the rehearsal's deployment — and still start its
readings fresh, saying they were written by a rehearsal. A rehearsal's readings describe a
simulated account: continued, the report would count the rehearsal's decline as this account's
drawdown, and every risk limit would measure real money against the rehearsal's denominator. No
`--first-start` either: the rehearsal's carry-over is a usable one. The composite this session
runs is **A**.

At 18:00 the operator stops it for an update. The bot holds 0.004 BTC and a sell limit at
71,200; the position stays, as positions always do, and the order stays because the profile says
`leave`. From now on the unit starts the bot.

### Tuesday 08:00 — a parameter while a position is open

The operator changes `rsi_oversold` from 40 to 35 and starts the unit. The cold start reads the
venue, finds the holding and the order, compares the configuration with the one they were opened
under — and stops:

```
❌ CONFIGURATION CHANGED WHILE POSITIONS ARE OPEN — start refused

This session continues deployment deploy_20261102_090012_5c1e8a3f (bot my_bot_btcusd).
What the venue still holds was carried over from run 20261102_090012_3a7c19e0, and this session's configuration
defines another composite (strategy changed):

  strategy_config.decision_logic_config.rsi_oversold   40 → 35   strategy   your profile

Still open at the venue:
  · a long position of 0.004 BTC (pos_btcusd_12, this bot's position book)
  · 1 resting order(s) of ours — a sell limit of 0.004 at 71200.0

A trade opened under one configuration and closed under another is the result of neither, so the start stops.
Ways on:
  1. Undo the change …
  2. Finish under the old configuration …
  3. Accept it for this start — run again with --accept-composite-change 7f3a9c2e. …
```

The session exits with code 3, which the unit does not restart; the operator alert says
`STARTUP REFUSED` once. Nothing was applied and nothing was written to the carry-over, so the
next start meets the same question. Until someone answers it, the holding and the order sit at
the venue with no bot watching them — no stop the bot would move, no drawdown limit. The alert is
what calls a person.

### Tuesday 08:05 — the change accepted

The operator decides the new value is worth it and starts the profile once by hand:

```bash
python python/cli/autotrader_cli.py run --config <the profile the unit starts> --accept-composite-change 7f3a9c2e
```
```
⚠️  CONFIGURATION CHANGE ACCEPTED (--accept-composite-change 7f3a9c2e): what the venue still holds, …
  strategy_config.decision_logic_config.rsi_oversold   40 → 35   strategy   your profile
  · a long position of 0.004 BTC (pos_btcusd_12, this bot's position book)
  · 1 resting order(s) of ours — a sell limit of 0.004 at 71200.0
  From here the deployment is two series: figures that need one long unbroken series — Sharpe, …
```

The run header records `origin.accept_composite_change: "7f3a9c2e"`, the session's warnings carry
a `composite_change_accepted` finding that names the key, the run it crossed, the changed setting
and what was still open, and the carry-over is written under the new configuration before the
first order — so whatever starts the bot next, the unit's ordinary command included, finds nothing
to ask. The flag belongs to this one start and not to the unit. The composite is now **B**.

A start by hand runs outside the unit: if it dies, nothing restarts it. Once it is running, stop it
and start the unit — its plain command now finds nothing to ask. (This week the operator lets it
run until the reboot.)

### Tuesday 22:40 — a restart that changes nothing

The machine reboots. The session receives SIGTERM and stops cleanly; after the reboot the unit
starts the bot with its ordinary command. The configuration is B on both sides, so the cold start
takes the position and the order over without a question:

```
📦 Carry-over: continued from run 20261103_080507_b1d2e3f4 of my_bot_btcusd, …
📉 Reported drawdown continues from the previous session: peak 10051.20, … — carried across 2 restart(s)
```

### Wednesday 10:00 — a limit while flat

Overnight the sell limit filled and closed the position. The operator tightens
`safety.max_drawdown_pct` from 25 to 12 and restarts. Nothing of the bot is open at the venue, so
there is no trade to cross the change and no refusal — the start says what changed and goes on:

```
🧬 Configuration changed since run 20261103_224109_c4e5f6a7 (conditions) — nothing of this bot is open …
  safety.max_drawdown_pct   25 → 12   conditions   your profile
```

No flag, no warning in the record — and still a new composite, **C**. The series breaks here just
as it did on Tuesday; it is only spared the question.

### Thursday 09:00 — a release with a new setting

A fix release is deployed while the bot holds again. Suppose the release adds a safety setting —
this example invents `safety.pause_on_gap`, default `false` — mirrored in the shipped
`app_config.json`. The profile file is untouched, and the start is refused all the same:

```
  safety.pause_on_gap   not set → false   conditions   app_config.json as shipped

  A value marked 'not set' exists in only one of the two sessions' configurations: the code changed
  between them — an update added or dropped it.
…
  1. Undo the change — … A value marked 'not set' cannot be restored in a file: when its new value
     keeps the old behaviour, take way 3.
```

The new default changes nothing the bot does, so the change is accepted — by a colleague, who puts
`--accept-composite-change 91b0c3d4` into the unit's command and starts the unit. Composite **D**.
The flag stays in the unit.

### Friday 12:00 — a flag left behind

The operator lowers `rsi_oversold` to 32 while the bot holds, and restarts the unit — which still
carries `--accept-composite-change 91b0c3d4`:

```
❌ CONFIGURATION CHANGED WHILE POSITIONS ARE OPEN — start refused

--accept-composite-change 91b0c3d4 does not name the composite this session runs (e2d4a7f1): the
configuration changed again after the refusal that printed it.
…
```

The flag names one composite. Left in the unit, it never accepts the next change; a person has to
look again. The bot stays down until someone does — and as on Tuesday, what it holds sits at the
venue unwatched until then; the `STARTUP REFUSED` alert is what calls someone.

## What the deployment shows

The history command prints the week, newest first (values invented, columns shortened):

```
$ python python/cli/run_index_cli.py deployments --id deploy_20261102_090012_5c1e8a3f

deploy_20261102_090012_5c1e8a3f — 8 session(s) · since 2026-11-02 09:00 UTC · bot mybot01
⚠️  Code changed 2 d ago (2026-11-05) — 2 code stands over 8 sessions; net P&L adds them up
──────────────────────────────────────────────────────────────────────────────────────────────
⚠️  THIS DEPLOYMENT SPANS MORE THAN ONE CONFIGURATION
    3 strategy stand(s) and 3 operational stand(s) over 8 sessions.
    …
──────────────────────────────────────────────────────────────────────────────────────────────
run id                     started             ran     net P&L  max DD (cum)   share   notes
20261106_120004_e8f9a0b1   2026-11-06 12:00   0.0 h       0.00         -0.00   0.00%
·········· STRATEGY CHANGED HERE — rows above and below answer different questions ··········
20261105_090511_d6e7f8a9   2026-11-05 09:05  26.9 h      41.30       -118.40   1.16%
20261105_090002_c5d6e7f8   2026-11-05 09:00   0.0 h       0.00         -0.00   0.00%
·········· OPERATION CHANGED HERE — rows above and below answer different questions ·········
··· code changed here ········································································
    framework  e5f6a7b8 → 91c2d3e4  +4 commits  "Add the pause on a gap"
20261104_100003_b4c5d6e7   2026-11-04 10:00  23.0 h     -12.75        -96.10   0.95%
·········· OPERATION CHANGED HERE — rows above and below answer different questions ·········
20261103_224109_c4e5f6a7   2026-11-03 22:41  11.3 h      18.20        -61.20   0.61%
20261103_080507_b1d2e3f4   2026-11-03 08:05  14.6 h      30.10        -61.20   0.61%
20261103_080001_a0b1c2d3   2026-11-03 08:00   0.0 h       0.00         -0.00   0.00%   idle 14.0 h
·········· STRATEGY CHANGED HERE — rows above and below answer different questions ··········
20261102_090012_3a7c19e0   2026-11-02 09:00   9.0 h      23.50        -21.00   0.21%
…
```

Three things to read in it:

- **A refused start is a session of its own** — a row with no trades, 0.00 and no drawdown: it
  stopped before it continued the curve. It is recorded under the configuration it refused to run,
  so the change mark sits on it rather than on the session that accepted the change a few minutes
  later.
- **The marks compare the configuration, the line below them the code.** The marks read the
  strategy's parameters (`STRATEGY`) and the rest of the loaded profile, defaults included
  (`OPERATION`), so Thursday's release draws a mark even though nobody edited a file. It also draws
  `code changed here`, as every release does, new setting or not: one row per repository, with how
  far it moved and the newest commit's message. The dated line above the table says when that last
  happened. Neither is a composite boundary, though: a value the composite ignores — the capital
  declaration, most of the execution block — still draws an `OPERATION` mark, a composite value from
  outside the profile — the broker's price formation in `market_config.json` — draws none, and a
  release that moves no value draws its code line and names no new composite. What names a new
  composite is the start's own notice or refusal.
- **The drawdown column runs on across every change** that a session actually ran — the refused
  rows aside. It is the account's own history, one account over time; what changed is the
  configuration it was measured under.

The viewer shows the same marks on the deployment's detail and links the configuration before and
after each one. What changed inside it — the values, the part of the composite, whether a start was
refused or accepted — and each composite stretch with its length, come to that view with the
composite key's reporting (#615).

## What the week cost

Five days of real money became four composites, none longer than a little over a day:

```
A  Mon 09:00 – Mon 18:00      rsi_oversold 40, drawdown limit 25 %
B  Tue 08:05 – Wed 10:00      rsi_oversold 35                         accepted with a position open
C  Wed 10:00 – Thu 09:00      drawdown limit 12 %                     a clean restart
D  Thu 09:05 – Fri 12:00      pause_on_gap added by a release         accepted with a position open
```

- **No figure of one composite means much.** A Sharpe ratio over a day is noise; its reliability
  grows with the length of the unbroken series behind it, and here no series is longer than a day
  and a half. The same holds for the profit factor and for one composite's drawdown.
- **The parity proof became four.** A backtest of the week has to reproduce each stretch with the
  configuration it ran — possible, because each session froze what it ran with, but four
  comparisons instead of one, each over a stretch too short to say much.
- **Two trades crossed a change** (Tuesday and Thursday). Their P&L belongs to neither
  configuration.

None of this is a fault. Every change was allowed and every one is on the record. The question
before a change is not only "is the new value better", but "is it worth starting the series
again".

## How the same week could have gone

- **Try a change on a copy, not on the bot.** A copy of the profile with its own `bot_id` is a
  separate deployment, rehearsed and backtested apart from the series that runs.
- **Gather changes and make them once.** Tuesday's and Friday's edits to `rsi_oversold` would have
  been one stretch instead of two.
- **Change while flat.** A restart with nothing open crosses no trade — it still starts a new
  stretch, so it is no free change either.
- **Backtest the new composite before it goes live.** The record says what ran; it cannot say the
  new configuration was ever tested.
- **Never leave the flag in a unit.** It names one composite, so a stale one never accepts
  anything — an unchanged restart starts with a note, and the next change while something is open
  is refused as if the flag were not there. It is still a decision that belongs to a person and a
  moment.

## Related

- [Unattended Operation](../autotrader/unattended_operation.md) — *A restart under a changed
  configuration*: the rule, the four cases, the ways on, the readings by kind of run
- [Deployment & Ledger](deployment_ledger_guide.md) — the `deployment` declaration and the history
  command
- [Reading the Results Ledger](results_ledger_guide.md) — what a composite is and why accounts add
  up only within one
- [AutoTrader Configuration](../autotrader/autotrader_configuration.md) — `--new-deployment` and the
  declared continuation
