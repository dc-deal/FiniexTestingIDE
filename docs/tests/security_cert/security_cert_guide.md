# Security Study — Operator Guide (#358)

The account's protective rules — the order guard's refusals and cooldown, the circuit breaker's
soft block and hard stop, the risk baseline across a restart — had been tested offline only.
Offline, a test sets the equity the breaker reads; with real money the breaker reads a portfolio
that a venue's fills, fees and spread move. Whether the rules fire on THAT value, and whether
what they leave behind is what the record says, is a question only a funded account answers.
The security study answers it: a sequence of AutoTrader sessions on real Kraken Spot that
provokes each rule that can be provoked safely, and a certificate that says, rule by rule, how
it is proven.

It is the second **acceptance study**, beside the [field study](../live_field_study/field_study_guide.md),
which proves the order path and leaves the protective rules out on purpose. Both run on the same
parts in `python/framework/acceptance_studies/`, write the same capture format, and produce a
release certificate ([release_certificates.md](../../architecture/release_certificates.md)).

> **Cost:** ~$0.25 per full run — six min-lot fills on BTCUSD at the taker fee (measured
> 2026-10-14: 0.2343 USD; the account's other assets did not move).

---

## How each rule is proven

Every rule the certificate names is proven one of three ways, declared once per rule in
[`security_cert_rule_catalog.py`](../../../python/framework/reporting/certificates/security_cert_rule_catalog.py)
and held complete by a test:

| State | Meaning |
|---|---|
| `live` | provoked with real money in a stage; the certificate names the check that saw it |
| `offline` | proven by a named offline test of the same commit — a rule that needs a market move, a day to pass, or a venue that refuses on purpose |
| `margin` | waits for a margin account (#209); this study runs at spot |

**No equity is ever injected.** The breaker could be tripped on demand by a switch that feeds it a
made-up account value, and that switch would sit in production code where a copied profile turns
it on: the breaker then fires without a loss, or never fires on a real one. A rule that cannot be
provoked with a small real loss is proven offline instead, where the mock adapter moves the
prices in the ticks and the real tick loop does the rest.

---

## The stages

Each stage is an AutoTrader session of its own with its own profile in
`configs/autotrader_profiles/security_cert/`. They run in order, and a later stage depends on
what an earlier one left:

```
stage 1  guards        refusals that cost nothing; the cooldown arms, holds one side, runs out,
                       re-arms, and a fill resets it — one fill, one force close
stage 2  soft          a min-lot buy moves the account value under limits set tiny → the soft
                       block engages, names every limit, suppresses the next buy; closing works
stage 3  restart       the same bot restarts and reads stage 2's baseline → blocked from its
                       first valued tick, its first buy suppressed — nothing is sent
stage 4  hard_kept     a buy trips the hard stop; selling is not allowed → the holding is KEPT
                       and the session ends
stage 5  hard_restart  the same bot restarts with the kept lot and selling allowed → the stop
                       fires again at the first valued tick and sells → the account ends flat
```

Stages 3 and 5 restart the bot of the stage before them, so each pair shares one `bot_id` — one
carry-over document — and the later profile declares `carry_over_from` naming the earlier one.
The boot holds the pair to the rules that keep it from being mistaken for a copy, and refuses a
later stage that finds nothing to continue
([autotrader_configuration.md](../../autotrader/autotrader_configuration.md), *A declared
continuation*).

Stage 5 records today's behaviour: the restart takes over the kept lot and the stop fires again.
#590 turns that into a refused start, and the stage's expectation changes with it.

---

## Pre-flight

1. **The account holds no BTC** (less than the smallest order) and **some USD**. The study judges
   the account value, and a held base moves it with every tick — against limits this small it
   would trip them at a moment nobody chose. The runner reads both balances from the venue before
   anything is sent and refuses loudly, naming what it found and what to do.
2. **No resting orders.** Each stage's preflight reads the venue and stops before trading if one
   is there.
3. **Commit first** for the certificate that counts. A run from an uncommitted tree needs
   `--allow-dirty`, records its patch, and its certificate fails `study:one_commit` on purpose.
4. The other release certificates of the commit are green.

---

## Running it

```bash
python python/cli/security_cert_cli.py run --release-version X.Y.Z [--comment "..."]
```

One command runs every stage, each as `autotrader_cli.py run --config <stage profile>`, judges
each stage by its own capture as soon as it ends, and writes the certificate once all passed. A
failed stage stops the study — what would follow measures nothing, and a real-money stage after a
failed one only costs. The runner then prints what failed and what to do next.

| Option | Use |
|---|---|
| `--from-stage <stage>` | resume after fixing a cause; earlier stages count from their latest runs |
| `--allow-dirty` | real orders from uncommitted code, each recorded; the certificate fails `study:one_commit` |
| `--profile-dir <dir>` | where the stage profiles are read from — the mock rehearsal's are elsewhere |
| `--reports-dir <dir>` | where the certificate is written |

A session's exit code is not the verdict: the hard-stop stages end their sessions on purpose, and
an emergency end is graded as one. The capture says what happened.

A stage's run is found by its identity: the runs of the stage's own profile, with the purpose
that profile declares, the newest by the start its header records — never the newest file. A mock
rehearsal's stages (fixtures) are found the same way as a real study's (certificates). A capture
that is empty, or broken before its last line, stops the study with its name and what is wrong; a
last line cut off by a stopped session is left out and said in the certificate.

To regenerate the certificate from the latest stage runs without running anything:

```bash
python python/cli/security_cert_cli.py generate --latest --release-version X.Y.Z
pytest tests/security_cert/test_security_cert_certificate.py -v
```

Commit the certificate from `tests/security_cert/reports/`.

### When a stage fails

| Failed at | Account afterwards | Next |
|---|---|---|
| stage 1–3 | flat — a fill there is closed by the stage's force close | fix the cause, `--from-stage <stage>` |
| stage 4 or 5 | may hold the kept lot | `--from-stage hard_restart` sells it through the stop, or sell it by hand before running again |

---

## The rehearsal on the mock adapter

`configs/autotrader_profiles/mock/security_cert/` holds a twin of every stage on the mock adapter:
the same phases, nothing sent. It runs through the same command:

```bash
python python/cli/security_cert_cli.py run --profile-dir configs/autotrader_profiles/mock/security_cert
```

Three differences are declared in those profiles, each for a reason the profile states:

- **Every duration is scaled up.** The mock replays archived ticks, and in a quiet hour up to a
  minute passes between two of them — longer than the real cooldown, which would then lapse
  between two phases.
- **Stages 3 and 5 start a little under the earlier stage's baseline.** A mock session starts
  from the balance it declares, so it declares the state the real account is in after the earlier
  stage's fees.
- **No lot is carried into stage 5.** A mock session is a dry run, which persists the baseline but
  not the position book.

Its certificate fails by design — no real orders, and no sale in stage 5 — but every stage's
phases pass. `tests/autotrader/integration/test_security_study_stages.py` runs it in the suite.

---

## The capture

Each stage writes `security_cert.jsonl` into its run directory, in the format the field study's
capture defines ([field_study_guide.md](../live_field_study/field_study_guide.md), *JSONL Schema*):
the study's own phase lines, and the order and venue lines copied from the session's order-event
stream. What its summary lines carry — the reconciliation's totals, the REST API's performance —
every session now records in its own report sections as well (#631), as the field study guide says.
A submit phase's result adds what it expected and what it sent:

```json
{"event_type":"phase_result","phase":"g_cooled_buy","status":"pass","extra":{"reason":"denied (rejection_cooldown)","expect":"denied","expect_reason":"rejection_cooldown","lot_rule":"min_lot","lots":6e-05}}
```

A phase that waits for the hard stop is concluded when the session ends — the stop ends it before
the study sees another pass — and its reason quotes the end:
`ended by the safety layer: emergency stop, spot holdings kept (...)`.

---

## The certificate

It reads the latest run of every stage and judges them together. Three groups of checks:

| Group | Asks |
|---|---|
| `study:*` | every stage ran, in order, from one clean commit, sending real orders, and the account ends flat |
| `phase:<id>` | every phase passed — a submit's verdict agreeing with its order line, a stop's wait agreeing with the breaker's own record |
| `safety:*` | the breaker's own record (`io/safety.json` of each run) says what the stage was there to provoke: the block and its reasons, the restored baseline with its original stamp, the kept holding, the second stop and its sale |

Every check reads two sources where two exist — the capture says what the study saw, the
breaker's record says what the breaker did — so a study that only believes itself cannot pass.

A declared release is held to the commit its stages ran on: a certificate generated on another
commit fails with `EVIDENCE COMMIT`, a clean tree or not, and its `valid_until` counts from the
first stage's start. The runner writes it right after the last stage, so the two agree unless a
certificate is generated later by hand. The stage runs it rests on stand in `evidence_runs`.

---

## Components

| Piece | Location |
|---|---|
| Decision logic | `python/framework/decision_logic/core/security_cert_study/security_cert_study.py` |
| Phase machine | `.../security_cert_study/security_cert_phase_machine.py` |
| Stages and their profiles | `python/framework/acceptance_studies/security_cert_stages.py` |
| Runner | `python/framework/acceptance_studies/security_cert_runner.py` |
| Certificate analyzer | `python/framework/reporting/certificates/security_cert_certificate.py` |
| Rule catalog | `python/framework/reporting/certificates/security_cert_rule_catalog.py` |
| CLI | `python/cli/security_cert_cli.py` |
| Offline tests | `tests/autotrader/security_study/` · `tests/framework/security_cert_certificate/` · `tests/autotrader/integration/test_security_study_stages.py` |
| Certificate validation | `tests/security_cert/test_security_cert_certificate.py` (release gate, excluded from the daily runner) |
