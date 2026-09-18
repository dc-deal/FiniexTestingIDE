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

```bash
python python/cli/run_index_cli.py deployments
python python/cli/run_index_cli.py deployments --id deploy_20260901_060000_ab12
```

```
deploy_20260901_060000_ab12 — 4 session(s) · since 2026-09-01 06:00 UTC
────────────────────────────────────────────────────────────────────────────────────────────────
  #  started                net P&L   max DD (cum)    share   notes
  1  2026-09-01 06:00         41.20         -58.40    0.58%
  2  2026-09-08 07:15        -12.75        -134.10    1.33%   gap 7.1 d
  3  2026-09-11 06:30         88.05        -134.10    1.33%   gap 3.0 d · ⚠ operation changed
  4  2026-09-16 06:05         19.60        -212.75    2.05%   gap 5.0 d · ⚠ strategy changed
────────────────────────────────────────────────────────────────────────────────────────────────
Amounts in USD. The drawdown column is CUMULATIVE over the deployment — each row
carries the running figure against the inherited peak, so max() is its reduction and a
sum double-counts. A gap and a changed hash are REPORTED, never judged.
```

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
| `gap N d` | The bot was not running for N days between these two sessions | Whether that mattered. A weekend on a forex bot is nothing; five days on a crypto bot may be everything |
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

**A dry run hands nothing on.** A dry-run session places no order at any venue, so it writes no
carry-over — and the carry-over is what passes the deployment identity to the next session. Two
dry-run sessions each mint their own identity and never join. This is not a defect to work
around: it is the same rule that keeps a dry run from claiming a real resting order. **A mock
profile is always a dry run**, so you cannot rehearse a deployment chain with one.

**`--one-off` detaches one start.** Use it for a debugging start of a deployed profile. That
session records no deployment and stays out of the history — and it does *not* end the
deployment: the next ordinary start continues where the last one left off.

```bash
python python/cli/autotrader_cli.py run --config configs/autotrader_profiles/my_bot_live.json --one-off
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
