# Reading a live bot across its restarts

A bot you leave running for a month will not run for a month. It will be stopped for an update,
it will die at 03:00 and be relaunched, the machine will reboot. Each of those starts is a
separate run with its own identity, its own report and its own row in the results ledger — and
by default nothing says they belong together. Open the ledger after four weeks and you find
eleven rows that look like eleven unrelated bots, each reporting a drawdown that begins at the
moment it happened to start.

A **deployment** is what ties them back together: one declaration in your profile, and the
sessions of that bot read back as one history with the gaps and the changes visible in it.

**Not in this document:** how the ledger is written, how the run index works, or what the report
pipeline does with a session's numbers. Those are `architecture/data_storage_layout.md` and
`architecture/reporting_pipeline.md`. This one is about the declaration you write and the
history you read.

---

## The one line you have to write

Every AutoTrader profile must declare this block. The loader refuses a profile without it — at
load time, before anything runs:

```json
"deployment": {
  "continuous": false
}
```

`false` — each start of this profile stands alone. This is what a test profile, a field study,
an adapter certification and any one-off observation wants.

`true` — the sessions of this profile are one deployment. Their ledger rows join into one
history. This is what you write on the profile you are actually deploying.

### Why you are not allowed to leave it out

Every other block in a profile has a default, because every other block has a value that is safe
to inherit. This one does not, and both directions cost something:

- **Forgotten on a deployed profile** — the sessions never group. The join key cannot be added
  afterwards, because it is written into each row as that row is written. A month of history is
  unrecoverable.
- **Set wrongly on a one-off profile** — unrelated runs are welded into one history. Annoying,
  and every figure is still there to be read separately.

With no safe value to inherit, the profile has to say. The side effect is worth having on its
own: open any profile in this project and it now states what it is.

---

## What a deployment is, and what it is not

It **is** a declaration that these sessions are one bot's continuous life. It is a name that
rows carry, nothing more.

It is **not** a claim that the sessions are comparable. A deployment survives you changing the
strategy parameters halfway through — it records the change and shows it to you. Judging whether
the two halves may be compared is your job, and the history gives you what you need to do it.

It is **not** a mechanism that does anything at runtime. The bot does not behave differently
inside a deployment. What changes is that the account's risk history — the peak equity and the
deepest decline — is carried from one session to the next, so a drawdown reported after four
weeks describes four weeks rather than the afternoon since the last restart.

---

## Reading the history

The report has two halves, and they answer different questions.

**Without an id you get the LIST** — which deployment do I want to open:

```bash
python python/cli/run_index_cli.py deployments
```

```
3 deployment(s)
──────────────────────────────────────────────────────────────────────────────────────────────────
deployment                     bot                    sessions since             net P&L    max DD  idle max
──────────────────────────────────────────────────────────────────────────────────────────────────
deploy_20260820_090000_5e3f    ethusd_live                   1 2026-08-20 09:00    12.00    -30.00        —
deploy_20260901_060000_ab12    dotusd_live                   2 2026-09-01 06:00    60.80   -212.75   14.5 d ⚠
deploy_20260712_051500_77c1    dotusd_live                   2 2026-07-12 05:15  -402.88   -991.40    2.1 d
──────────────────────────────────────────────────────────────────────────────────────────────────
⚠ = the sessions were not all produced by the same configuration; the detail view draws the break.
Two rows for one bot = it was restarted with --new-deployment. The older history stays readable.
Open one with `run_index_cli.py deployments --id <deployment>`.
```

Deployments of the **same bot stand together**, newest first. That is what a reset looks like:
`dotusd_live` appears twice because it was restarted with `--new-deployment`, and both
histories stay readable under their own names.

Note what the two number columns do: **`net P&L` adds up across the sessions, `max DD` does
not** — it is the largest of them. The reason is in the detail view below.

**With an id you descend into one** — what happened inside it:

```bash
python python/cli/run_index_cli.py deployments --id deploy_20260901_060000_ab12
```

```
deploy_20260901_060000_ab12 — 4 session(s) · since 2026-09-01 06:00 UTC
────────────────────────────────────────────────────────────────────────────────────────────────
⚠️  THIS DEPLOYMENT SPANS MORE THAN ONE CONFIGURATION
    2 strategy stand(s) and 2 operational stand(s) over 4 sessions.
    The sessions are one deployment because they were DECLARED one — that says they belong
    to one bot, not that their figures are comparable. A drawdown that deepens after a change
    is attributable; a P&L column summed across the change is a number about two bots.
────────────────────────────────────────────────────────────────────────────────────────────────
run id                     started               ran     net P&L  max DD (cum)   share   notes
20260916_060500_8e10       2026-09-16 06:05   11.8 h       19.60       -212.75   2.05%   idle 4.5 d
·························· STRATEGY CHANGED HERE — rows above and below answer different questions ···
20260911_063000_c07d       2026-09-11 06:30   11.5 h       88.05       -134.10   1.33%   idle 2.5 d
·························· OPERATION CHANGED HERE — rows above and below answer different questions ···
20260908_071500_41ab       2026-09-08 07:15   10.4 h      -12.75       -134.10   1.33%   idle 6.6 d
20260901_060000_9f2c       2026-09-01 06:00   12.0 h       41.20        -58.40   0.58%
────────────────────────────────────────────────────────────────────────────────────────────────
Amounts in USD. Newest session first. …
Descend into one session: runs/live/<profile>/<run id>/
```

**A configuration change is drawn as a line ACROSS the table**, not as a mark on one row. It
happened between two sessions, and that is also what it did to the numbers: everything above
the line was produced by a different bot than everything below it.

**Newest session first, and each line is keyed by its run id** — because the id is what the
next question needs. The three steps are meant to be walked in order:

```
  deployments                    which deployment do I want?
       ↓  --id <deployment>
  its sessions                   which session do I want?
       ↓  runs/live/<profile>/<run id>/
  that run's logs and reports    what happened in it?
```

`ran` is measured from the session's start to the moment its ledger row was written, so it
carries the few seconds the reports took. A row from before that stamp existed shows `—`
rather than a made-up number.

The warning block is printed **above** the table on purpose. By the time a reader reaches a
`⚠ strategy changed` in row 3 they have usually already added up the column above it.

### The drawdown column is the one that misleads

`net P&L` is per session: session 2 lost 12.75, and adding the column up gives the deployment's
realised result. **The drawdown column does not work that way.** Each row carries the *running*
worst decline against the peak the deployment has reached so far — so session 3's `-134.10` is
the same trough session 2 already reported, not a second one. The deployment's worst decline is
the **largest** figure in the column, never the sum. Adding them here would report -539.35 for a
deployment whose account was, at worst, 212.75 down.

`share` is that decline as a percentage of the peak that stood **at the time it happened**. It
is recorded rather than recomputed, which is why it is not simply `max DD ÷ peak`: by the time
you read it the peak has usually moved on.

### The three notes, and what they are worth

| Note | What it says | What it does not say |
|---|---|---|
| `gap N d` | The bot was not running for N days — measured from the END of the previous session to the START of this one | Whether that mattered. A weekend on a forex bot is nothing; five days on a crypto bot may be everything |
| `gap N d between starts` | The same, but the previous session's row is old enough that it recorded no end, so the figure runs start-to-start and **includes that session's own runtime** | Anything more precise. Treat it as an upper bound |
| `⚠ strategy changed` | The strategy parameters differ from the previous session | Which parameter, or in which direction |
| `⚠ operation changed` | Something operational differs — a safety threshold, an order guard setting, a timeout, the capital declaration | The same |

Nothing here is a verdict, deliberately. A threshold that decided *for* you what counts as too
long a gap would be a judgement about your market and your night, made by a tool that knows
neither.

### Why the two change marks are separate

Because they answer different questions, and one combined mark would answer neither.

**Strategy** is what your bot decides: the indicator periods, the thresholds, the position size.
When this changes, the sessions before and after are running different bots, and a backtest
built from the earlier half no longer describes the later half.

**Operation** is everything that changes what a session *does* without changing what it
*decides*: a raised stop level, a different order-guard limit, a changed timeout, a new capital
declaration. When this changes, the strategy is still the same strategy — but the answer to "why
did the circuit breaker not fire on day 19" may have moved.

Were these one hash, raising a stop level would read as a different strategy and put an otherwise
comparable run beyond comparison. The full resolved configuration of each session is stored on
its ledger row, so a tool can tell you `rsi_buy_threshold: 45 → 40` rather than only "the hash
differs".

---

## What breaks a deployment

**A dry run hands on what it can honestly claim — and nothing more.** A dry-run session places
no order at any venue, so it never records a session key or an open position book: a successor
inheriting either would trade beside orders that do not exist. But the deployment identity, the
risk baseline and the drawdown curve are numbers the process computed, and those are true
whether or not the venue was real, so they are carried.

The practical consequence is a good one: **a mock profile CAN rehearse a whole deployment
chain** — start it, stop it, start it again, and read the history. What a rehearsal cannot show
you is the position book surviving a restart, because there were never any positions at a venue
to survive.

**`--one-off` is the PROBE, and it comes BEFORE the deployment — not during it.** Nobody starts
an algo for thirty days and then goes on holiday. The order that works:

```
1.  write the profile with  "deployment": { "continuous": true }
2.  run it for a day with   --one-off          ← the probe: it trades, it reports,
                                                  and it joins no history
3.  read the report, fix what the day showed
4.  start it without the flag                  ← the deployment begins here
5.  every restart from now on continues it, with no flag at all
```

```bash
# step 2 — the probe day
python python/cli/autotrader_cli.py run --config configs/autotrader_profiles/my_bot_live.json --one-off

# step 4 — the deployment starts
python python/cli/autotrader_cli.py run --config configs/autotrader_profiles/my_bot_live.json
```

**Past step 4 the flag is REFUSED**, and the refusal names the three ways out. The reason is
worth reading once, because the flag looks harmless: a one-off start of a deployed bot still
trades that account with real money, so its P&L would be missing from the deployment's history
while its drawdown keeps running *inside* that history — the account's risk curve does not care
what stood on the command line. Two columns of one table would then describe different periods.

```
🔗 --one-off refused: this bot already belongs to deployment deploy_20260901_060000_ab12.
   A one-off start would still trade this account with real money — its P&L would be MISSING
   from the deployment history while its drawdown keeps running inside it, so two columns of
   one table would describe different periods.
     • starting over on purpose?  --new-deployment
     • just trying something?     copy the profile and give it its own name
     • only probing before you deploy? that is what --one-off is for, and it belongs BEFORE
       the first continuous start
```

**`--new-deployment` begins a fresh one.** Use it when the bot is genuinely starting over — a
new account, a new capital base, a strategy rewrite you do not want measured against what came
before. The old history stays readable under its own name.

**Neither flag can create a deployment out of a profile that declares `false`.** The command line
can only narrow, never widen, and that asymmetry is deliberate: an unattended restart re-executes
a command nobody typed, so a deployment declared on the command line would fragment at exactly
the restarts it exists to span — silently, because a missing flag looks like a one-off. The flag
whose absence is expensive lives in the profile; the flags whose absence is harmless live on the
command line.

**Changing the profile does not break it.** The sessions stay one deployment and the change is
marked. That is the point.

### If you forgot the declaration and the bot has been running

It is recoverable, and in two different ways depending on what you want.

**Start over from here.** Pass `--new-deployment` on the next start, or set `continuous` to
`false`, start once, and set it back. Everything from that point forms a new history under its
own name; nothing already recorded is lost or changed.

**Or pull the missed days in.** The rows for those sessions exist — they are simply missing the
join key. A one-off migration can stamp a deployment id onto ledger rows selected by profile
name and time range; `python/experiments/` is where such scripts live, and one of them did
exactly this shape of job on 2026-09-18 when the pipeline column was backfilled onto 586 rows.

**The one thing that closes the door** is deleting the ledger fragment itself. Note that the
run DIRECTORIES may go — the pruner removes them and the ledger row stays, which is why most
rows no longer have one. That is the design working. But `runs/ledger/*.parquet` is the record,
and once a fragment is gone the session it described cannot be joined to anything.

**And neither flag touches the account's drawdown CURVE.** That is deliberate, and it is the
one place where the deployment and the account deliberately part company: the deployment is
about how records are GROUPED, the drawdown curve is about what the ACCOUNT actually did, and
the account does not know what stood on the command line. So a probe started with `--one-off`
still inherits the peak and the deepest decline before it, and still hands them on. Whether the
curve survives a restart at all is a separate switch — `safety.persist_baseline` — and it is
the only one that decides it.

The alternative would lose real history. Worked through with three sessions: a bot peaks at
11.000 and falls to 9.500 (−1.500), a probe day takes it to 9.200 (−1.800), and the next
ordinary session recovers to 9.800. With the curve carried, that session reports −1.800 — what
the account really did. With the curve cut at the probe, it reports −1.500, and the 1.800 is
gone for good, because it happened in a session somebody ran with a flag.

---

## A session that never reached its close

The session table is built from the ledger, and a row is written as the **last** step at close.
A session killed before that — a crash, a reboot, a hard stop in the debugger — therefore never
appears in it. It is not hidden; it was never recorded.

The run index is what still holds it, because a run registers from its header before anything
can fail. The report reads both and names the difference:

```
⚠️  1 run(s) started and never completed — no ledger row was written.
   A run registers at START; its ledger row is the LAST step at close. So
   these ended abnormally — or one of them is running right now.
   live session — traded, and left no record of what it did
     20260918_174713_0dbd978c  2026-09-18T17:47:13  deployment_continuity_test  parent=deploy_...
```

Two things follow, and both matter more than they look.

**The session count above the table counts completed sessions only.** A deployment whose bot was
killed twice reports fewer sessions than it ran, and the block below the table is what reconciles
them.

**A session running RIGHT NOW looks exactly the same** — a header, no ledger row. The wording says
"not completed" rather than "aborted" for that reason; whoever reads it knows whether a bot is up.

What the killed session still leaves behind is the carry-over in `data/runtime/cold_start_state/`:
the open position book, the risk baseline and the drawdown curve. So the next session resumes
correctly even though the record of the previous one is missing — the loss is the account of what
happened, never the state itself.

---

## Where the rows live, and why most runs are gone

The history is read from the results ledger in `runs/ledger/` — one small file per run, plus a
single index over all of them. It is deliberately not the same thing as the run directories in
`runs/live/`, which hold the logs and reports of each session and are pruned much sooner.

So this is normal and not a fault: **most ledger rows no longer have a run directory.** Measured
2026-09-17, 428 of 580 rows had none. The ledger keeps the record long after the logs are gone —
that is what it is for. A history that names a session whose directory you cannot open is a
history doing its job.

---

## Checking what a session decided

Every session says its resolved answer in two places, and both say the *resolved* one — a
profile declaring `continuous` started with `--one-off` reads `ONE-OFF` everywhere:

- the live display title, beside the symbol
- the session log, as one line near the start:
  `🔗 Deployment deploy_… — continuing a continuous run` or `🔗 One-off session`

If you are ever unsure what a long-running bot is currently part of, that log line is the
cheapest answer, and it is still there weeks later.

---

## Related

- `algo_state_persistence_guide.md` — what your *algorithm* remembers across a restart, which is
  a separate store with a separate opt-in
- `live_outage_handling_guide.md` — what happens to a live session when its inputs go away
- `../architecture/data_storage_layout.md` — every store this project writes, and why the ledger
  and the run tree retain differently
