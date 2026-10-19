# AutoTrader Configuration

One profile is one bot: one symbol, one broker, one account. Everything a session does is decided
before it starts, which is why a wrong key here is not a runtime surprise but a boot refusal — and
why the identity a profile declares outlives the process that read it.

This document is the AutoTrader's own cascade, the deployment identity a restarted bot inherits,
and the two fingerprints that answer two different questions about what a profile sets.

**Not here:** the simulation's scenario cascade — `docs/config_cascade_guide.md`. The general
override mechanism — `docs/user_configs_override_system.md`. Where credentials live —
`docs/architecture/credentials_layout.md`.


## `bot_id` — the identity a bot's state is filed under

A bot's persistent state — its open position book, the position-counter high-water mark, the
session keys its orders were sent under — lives in a file named after the bot. Which bot that is
was composed from what the profile is CALLED:

```
profile_name: "dotusd_production"  +  symbol: "DOTUSD"   ->   dotusd-production_dotusd.json
```

**That makes the identity move when the name does**, and a display name is exactly the thing an
operator improves. Renaming `dotusd_production` to `dotusd_production_v2` points the next session at
`dotusd-production-v2_dotusd.json`, which does not exist — so the bot starts, finds no carry-over, and
reads its own holding as flat. At spot that is not recoverable from the venue: a holding is a
balance the venue cannot describe as a position, so our own record is the only one there is.

Declaring the identity separates the two:

```json
{
  "profile_name": "dotusd_production_v2",
  "bot_id": "dotlive01",
  "symbol": "DOTUSD"
}
```

The file stays `dotlive01_dotusd.json` through any rename of anything else. It is written into
the document's own envelope, so the file can say what it is filed under rather than leaving that
to be recomputed.

**The NAME still decides whose document it is.** The envelope records the profile that wrote it,
and a store reads a document as this bot's only when that name is this profile's — so a renamed
profile finds its own document written by "another" profile. Where that document still holds an
open position or a session key, the boot refuses rather than start over it (#358), names the old
name and says to restore it:

```
This bot's carry-over was last written by the profile 'dotusd_production' (run 20261014_…),
    not by 'dotusd_production_v2', and it still holds 1 open position(s) and 3 session key(s).
```

Before that refusal, the session's first write replaced the document, and the position it
described stayed at the venue with nothing left that knew about it. A document that holds neither
— what a dry run or a mock session writes — may be started over. Taking a renamed profile's
document over on purpose is not built yet (#597).

**Set it once and never change it.** Changing a `bot_id` is the same event as renaming without
one: the next session looks somewhere else.

**MANDATORY on every profile since 2026-09-24**, and the widening is the point. The older rule asked
only of a profile declaring `deployment.continuous: true`, which protected the case least in need of
it: a profile declaring `deployment.continuous: true` is one somebody thought about. The route into
a collision is copying a profile into another purpose folder and keeping its name — and that copy
was exactly what the narrow rule exempted. A one-off is no longer exempt either: it inherits
nothing, which was the old argument, but it still WRITES a carry-over document, and a document
written under a name is one the next rename orphans.

**The shape:** 1 to 10 characters of `a-z`, `0-9` and hyphen.

The ceiling is so that an identity stays typeable, readable in a table and comparable by eye. The
character set is not a style choice: the id BECOMES half of a filename, and anything outside that
set would be rewritten on the way to disk — the profile would declare `Bot_01` and the store would
hold `bot-01`, which is the same class of confusion the id exists to prevent, one level down. The
underscore is excluded because it is the reserved join character between the two halves.

A profile with no `bot_id`, or one whose shape cannot survive the trip to disk, is REFUSED at
boot, with the value to paste in:

```
The profile 'DOTUSD Live Bot' declares no `bot_id`.
    A bot's state is filed under this identity — the open position book, the position
    counter, the session keys. Without one it is filed under the profile NAME, so renaming
    the profile points the next session at an empty document while the venue still holds
    the position.

    Add it to the profile, beside `profile_name`:

        "bot_id": "dotusd-liv"

    Up to 10 characters of a-z, 0-9 and hyphen. What it has to be is UNIQUE
    across every profile and never changed again. The identity this session would file
    under is 'dotusd-liv_dotusd'.
```

A refusal rather than a warning, because a warning on a thirty-day unattended run is a warning
nobody is there to read.

**Uniqueness is checked at boot across EVERY place a profile may live** — the tracked tree under
`configs/`, the workspace one under `user_configs/` and each user algo directory — not only within
a folder and not only within the tree the session was started from. The reason is the route an
operator actually takes: copying a profile and forgetting to change its `bot_id`. A private copy of
a shipped profile lands outside the shipped tree, across the boundary a single-tree check never
crossed, so the one check that could catch a copy was blind to exactly the copy that matters. A
profile in a user algo directory was skipped entirely until #581, because no `autotrader_profiles`
folder sits above it. A profile started from anywhere else — a test's temporary tree — is compared
only with its own tree.

They are separate BOTS, not a cascade. An AutoTrader profile does not merge with a same-named file
the way `app_config.json` does — `deep_merge` puts the app defaults UNDER one profile and nothing
else — so two files claiming one identity are always two bots sharing one position book, one
position counter and one set of session keys.

**A copy defeats the other guard too, which is why this one has to hold.** The carry-over document
carries its own `profile` and `symbol` and the store refuses a document belonging to a different
bot. A copy matches on both, so that refusal never fires: the second bot reads the first one's
position book and adopts it as its own.

The session is REFUSED, never repaired. Minting a fresh id on a collision would be the same
disaster arriving as a helpful fix — the id IS the key to the position book, so a silently changed
one points the bot at an empty document while the venue still holds the position.

A test holds the shipped profiles to the same three rules, so a new profile cannot arrive without
an identity, with a malformed one, or with one already taken.

### A declared continuation — `carry_over_from`

One case needs two profiles in one document on purpose: a later stage of one bot that has to start
from what an earlier stage left. The security study restarts its bot to prove a risk baseline
survives a restart, and again to prove a hard stop fires on a kept holding
([security_cert_guide.md](../tests/security_cert/security_cert_guide.md)). The later profile says
so, naming the earlier one by its `profile_name`:

```json
{ "profile_name": "kraken_spot_btcusd_security_cert_restart", "bot_id": "scsafe01",
  "carry_over_from": "kraken_spot_btcusd_security_cert_soft" }
```

Both stores then read the pair's documents as one bot's, in BOTH directions: the later stage reads
what the earlier left, and the earlier reads what the later left — a store writes by reading
first, so an earlier stage that ignored the document would replace the later stage's book on its
first write.

**The boot holds the pair to every condition that keeps it from being mistaken for a copy**, and
refuses before any order when one fails:

| Condition | Why |
|---|---|
| exactly two profiles claim the identity, the later naming the earlier byte for byte | a third would read the pair's state as its own; the stores compare names exactly |
| the earlier declares nothing itself, and the two have different names | a chain or a cycle makes both sides accept each other; the stores tell the stages apart by name |
| both declare `run_purpose` `certificate` or `fixture` | a release study's stages or a test's fixtures — a production bot keeping its identity across a rename is a different question |
| one `adapter_type`, one `broker_type`, one `cold_start.path` | a mock stage beside a live one would hand a real account a baseline taken from simulated balances |
| the earlier stage does not leave orders resting at session end | the next boot to meet them would be the later stage's, which never agreed to adopt them |
| the later stage reads a carry-over at all — its cold start is on, and it is not a study that skips the adoption step | the continuation is carried by the cold-start document alone |
| the later stage finds its predecessor's document, or its own from an earlier run | starting fresh would run it against a state it was not built for — a restart stage would trade with no risk limit armed |
| a document the partner wrote comes from a run that completed — reached its ledger row | a run still going and a run that died look the same, and two sessions must not manage one book |

The declaration is read from the profile alone, never from `app_config.json`, and it is left out of
both fingerprints: which stage came before says nothing about how this one trades.

## Configuration

Config file: `configs/autotrader_profiles/mock/mock_session_test.json` — own format, NOT scenario-set based.

```json
{
  "profile_name": "btcusd_mock",
  "bot_id": "mocksess01",
  "symbol": "BTCUSD",
  "broker_type": "kraken_spot",
  "adapter_type": "mock",
  "deployment": { "continuous": false },
  "strategy_config": { ... },
  "scenario_settings": {
    "data_sentiment_type": "crypto_sentiment",
    "start_date": "2026-04-27T05:26:21+00:00",
    "max_ticks": 20000,
    "balances": { "USD": 10000.0, "BTC": 0.0 }
  },
  "tick_source": { "type": "mock" },
  "display": { "enabled": false }
}
```

A **mock** session replays scenario base data: `scenario_settings` describes the data window
(broker/symbol/window/sentiment) resolved through the **same index/preparation stack the
backtesting batch uses** (the shared `MountPreparer`, #438), and the mock session replays that
window through the whole AutoTrader stack. `tick_source` then carries only the transport (`type`,
replay delay, the `freeze_after_ticks` outage drill). A **live-adapter** session has no
`scenario_settings` — its data streams from the venue. `adapter_type` selects only the adapter and
`tick_source.type` the tick source; a live adapter paired with the replaying `mock` tick source or
with `scenario_settings` is refused at load (`AdapterWiringError`).

Sections not listed here (`execution`, `clipping_monitor`, `order_guard`) inherit their values from `app_config.json::autotrader` — only specify them in the profile when overriding a default.

`deployment` is the one block that inherits nothing and **must** be present: see
[The deployment declaration](#the-deployment-declaration-497) below.

| Section | Purpose | Notes |
|---------|---------|-------|
| `profile_name` | Profile name | **Required.** Used for the run directory (`runs/autotrader/<profile_name>/`) and as the unit name in every report |
| `bot_id` | Carry-over identity | **Mandatory** — see [`bot_id`](#bot_id--the-identity-a-bots-state-is-filed-under) above |
| `carry_over_from` | The profile whose carry-over this one continues (#358) | Optional; a release study's or a fixture's later stage only — see [A declared continuation](#a-declared-continuation--carry_over_from) above |
| `run_purpose` | What the sessions are FOR (#576) | `regular` (default when omitted), `fixture` or `certificate`. Every profile in `mock/` declares `fixture` — the test session refuses a run whose profile does not; the `field_study/` and `security_cert/` profiles declare `certificate`. A profile of your own — in a user algo directory or in `user_configs/` — declares none: it always runs as `regular`, and a declaration there is refused. Stamped on the run header; see [Run Kinds](../consumer/run-kinds.md) |
| `description` | Why the profile exists | Optional text, Markdown allowed; served by the configuration directory as `config_description` |
| `symbol` | Trading pair | Single symbol per session |
| `broker_type` | Broker identifier | Maps to MarketType via `market_config.json`; broker connection settings read from there too |
| `adapter_type` | `mock` or `live` | Selects only the adapter; the tick source is `tick_source.type`. Mock: no credentials needed |
| `deployment` | `{"continuous": true\|false}` | **Mandatory — the loader refuses a profile without it.** Whether this profile's sessions form ONE deployment whose ledger rows join into one history (#497) |
| `dry_run` | `true` / `false` / omit | Optional per-profile override of the broker's `market_config` dry_run. Omit = inherit the broker default. May only TIGHTEN: `true` wins over a live broker default; `false` against a dry-run default is refused at startup (`DryRunConflictError`) |
| `strategy_config` | Workers + DecisionLogic | Same format as scenario sets |
| `scenario_settings` | Mock data + account (#438) | **Mock only.** Data window (`start_date`/`end_date`/`max_ticks`, optional `data_broker_type`) resolved via the shared index/prep stack; `data_sentiment_type` for SIGNAL workers; `balances` (spot: `{"USD": X, "ETH": Y}`; live adapter: fetched from the broker at startup); optional `stress_test_config.stale_data_stress`. Absent for a live adapter |
| `tick_source` | Tick transport | Mock: `tick_delay_ms` replay speed + `freeze_after_ticks`/`freeze_duration_s` outage drill (#436). Venue feed: WebSocket (#232). The data window lives in `scenario_settings` |
| `execution` | Runtime parameters | Inherits from `app_config.autotrader.execution`; override per profile if needed |
| `clipping_monitor` | Timing config | Inherits from `app_config.autotrader.clipping_monitor`; strategy: `queue_all` or `drop_stale` |
| `display` | Dashboard config | Inherits `enabled: true`, `update_interval_ms: 300` — test profiles set `enabled: false` |
| `order_guard` | Pre-validation guard | Inherits from `app_config.autotrader.order_guard`; override per profile if needed |
| `safety` | Circuit breaker | Always profile-specific. Omit or set `enabled: false` to disable |
| `operator_alerts` | Out-of-band channels to the operator (#235) | Inherits from `app_config.autotrader.operator_alerts`, both halves off. `telegram`: alerts through a bot (`active` + `endpoints`, each pairing `base_url` with a `credentials_file` holding `bot_token` and `chat_id`), paced by `min_send_interval_s`, retried through `connection`, whose `attempt_budget` must be finite. A symptom is one alert episode, its all-clear sent once it stayed away for `all_clear_after_s`; `daily_digest` sends each trading day's close. `liveness`: a ping every `interval_s` while the tick loop has passed within `loop_stall_after_s`, to the `ping_url` in `services/liveness_<bot_id>.json` (one monitor check per bot; `credentials_file` names another), optionally a `liveness_stamp_file`. With the ping off and Telegram on, the loop is still watched and a stall alerted. A mock session switches both off unless its profile sets `enabled` itself. See [Unattended Operation](unattended_operation.md) |

**Config cascade (2-level):**
```
configs/app_config.json::autotrader  ← Level 1 (global defaults for all sessions)
  ↓ deep_merge (profile wins)
autotrader_profiles/*.json           ← Level 2 (session-specific overrides)
```
`user_configs/app_config.json` can override the `autotrader` block too — same mechanism as all other app_config sections.

A session records both ends of the cascade in its run header, before the first tick. The profile
as you wrote it is frozen under `config_id`; what the session runs with — the merged result, every
parameter default filled in, and the broker's entry in `market_config.json` with its local
override — under `rendered_config_id`. A change to `app_config.json`, to a parameter default or to
the local `market_config.json` that reaches the session therefore moves the second and never the
first. Why a run keeps both:
[A run is a record, and an old one stays true](../introduction_to_the_ide.md#a-run-is-a-record-and-an-old-one-stays-true).

### The deployment declaration (#497)

A bot left running for a month restarts — for an update, after a crash, on a reboot. Each start
is its own run with its own identity and its own ledger row, so by default a month of operation
reads as a dozen unrelated bots, each reporting a drawdown that begins where it happened to
start. The `deployment` block is what ties those rows back together.

```json
"deployment": { "continuous": false }
```

It is **mandatory**, and it is the only block with no default. Every other section may be
omitted because its default is the safe reading; here there is no safe reading. Omitted and read
as one-off, a deployed bot's sessions never group and the join key cannot be added afterwards —
the history is unrecoverable. Omitted and read as continuous, a field study's repeated runs are
welded into a history that means nothing. So the profile has to say, and one that does not fails
at load, before anything runs.

**The profile declares; the command line may only narrow.** `--one-off` is the PROBE that comes
BEFORE a deployment — the day the profile is run to see that it behaves, on a profile that
already declares `continuous` but has never started one. Once the deployment exists the flag is
REFUSED (`OneOffInsideDeploymentError`), because past that point it means something else: the
session still trades the account, but leaves no mark on the history its own drawdown keeps
running inside. `--new-deployment` begins a fresh history instead of continuing the last one. Declaring a
deployment from the command line is deliberately impossible: an unattended restart re-executes a
command nobody typed, so a deployment declared there would break apart at exactly the restarts it
exists to span — silently, because a missing flag looks like a one-off. **The flag whose absence
is expensive lives in the profile; the flags whose absence is harmless live on the command
line.** Everything shown — the display title, the startup line in the session log, the ledger
row — is the RESOLVED answer, never the declaration.

The identity travels through the cold-start carry-over (store 4b): a session writes it at boot
and at shutdown, and the next session reads it before its own run header is written.

**The carry-over's write gate is split by what each field CLAIMS**, which is what makes this
rehearsable at all. The session key and the open position book are claims about the VENUE — this key
sent orders, this book is open — and a dry run or a mock session placed nothing at any venue, so its
successor must not inherit either (#355: a key recorded by a dry run would let a restart loop evict
the key that owns a real resting order). The risk baseline, the reported drawdown curve and the
deployment identity are OUR OWN records: numbers this process computed, true whether or not the
venue was real, and written either way. A refused boot still writes nothing at all.

Before that split the whole write was refused for a dry run, and since `_is_dry_run()` answers
True on `adapter_type == 'mock'` before it looks at anything else, a mock profile could not reach
the path at all — which meant the deployment identity and the drawdown continuity had no
end-to-end coverage and no manual rehearsal.

`parent_id` on the run header carries the identity rather than a new field. A deployment is the
same SHAPE of parent as a sweep: an identity that groups runs without being one, defined by the
runs that name it. `run_tree_pruner` therefore counts deployments exactly as it counts sweeps —
`--keep-last N` spares the N newest of each, whole.

**Same shape is not the same thing, so the header says which** (`parent_kind`, `sweep` |
`deployment`). Both ids are a prefix plus a timestamp, which makes them indistinguishable to any
consumer holding only the id — and one consumer was getting it wrong. The pruner sorted both
kinds into ONE ordering, and because the ordering is over the id string, `sweep_` outranks
`deploy_` on every comparison: with as few sweeps as the quota, a deployment could never be
spared, however recent. The quota is now counted per kind. A header written before the field
existed reports no kind, which is the truth about it — an unknown kind is never read as a
particular one.

### Two fingerprints, because they answer two questions

An AutoTrader session's ledger row carries two hashes over the profile, and the split is deliberate:

| Hash | Covers | Answers |
|---|---|---|
| `param_hash` | `strategy_config` as the profile writes it | Did the parameters the profile sets change — what #512 compares a backtest against |
| `profile_hash` | the operational rest — safety, order guard, execution, tick source, capital, the deployment declaration | Did what the session DOES change, without changing what it decides |

One wide hash would answer neither. A raised stop level must not read as a different strategy — that
would put an otherwise comparable run beyond comparison; and a changed RSI threshold must not pass
as mere operation. Both are computed from the LOADED config, so a value the loader resolved is
fingerprinted as resolved; `config_path`, `profile_name`, `symbol`, `broker_type` and
`scenario_settings` are excluded: where a profile sits on disk, which venue and symbol it trades
and — for a mock session — which window it replays say which run this is, not how it operates.
So are `run_purpose` and `carry_over_from`:
what a session is FOR and which stage came before it say nothing about how it trades. A profile's `description` never reaches the loaded configuration
at all, so a reworded one cannot mark a session as an operation change.

**Neither covers the parameter defaults, the code or the broker.** The `app_config.json` defaults
are in `profile_hash`, because the loader merges them in before it is computed. A parameter the
profile leaves unset gets
its schema default only when its component is built, so a default changed in a worker's or a
decision logic's parameter schema moves neither hash. What the session ran with, every default
filled in, is its rendered configuration (`rendered_config_id` in the run header). The code is the
commit and the component versions on the same ledger row, and the code identity in the header. The
broker configuration is `broker_config_id` beside `config_hash` in the run's broker section.

**Three identity columns sit beside them on the ledger row, and they answer different
questions.** `scenario_set_name` is what the profile is CALLED and an operator improves that;
`deployment_id` is minted per deployment and `--new-deployment` starts a fresh one; only `bot_id`
does not move. A reader asking *is this the same bot as the row above* has no other column to ask
— and it is what the bot's carry-over state is filed under, which is what makes a ledger row and a
position book joinable at all. Empty on a simulation row, and on a row written before `bot_id` was
mandatory.

Two bots running ONE strategy are the case this makes readable, and it is worth seeing measured:

```
Bot A   config_id ae4f91bb5520   param_hash a17e364498f6   carry-over  dotlive01_dotusd
Bot B   731d21024b31             a17e364498f6              dotlive02_dotusd
        ↑ two configurations     ↑ provably one strategy   ↑ separate position books
```

Neither hash decides anything. A change is RECORDED and reported — `run_index_cli.py
deployments` marks the session it happened on — and whether the halves may be compared is a
judgement a person makes. The strategy configuration rides the same row as the profile gave it
(`strategy_config_json`), so a tool can say `rsi_buy_threshold: 45 → 40` rather than only "the
hash differs".

End-user view of all of this: [Deployment Ledger](../user_guides/deployment_ledger_guide.md).
