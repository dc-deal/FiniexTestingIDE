# AutoTrader Configuration

One profile is one bot: one symbol, one broker, one account. Everything a session does is decided
before it starts, which is why a wrong key here is not a runtime surprise but a boot refusal — and
why the identity a profile declares outlives the process that read it.

This document is the AutoTrader's own cascade, the deployment identity a restarted bot inherits,
and the two fingerprints that answer two different questions about what ran.

**Not here:** the simulation's scenario cascade — `docs/process_execution_guide.md`. The general
override mechanism — `docs/user_configs_override_system.md`. Where credentials live —
`docs/architecture/credentials_layout.md`.

## Configuration

Config file: `configs/autotrader_profiles/backtesting/mock_session_test.json` — own format, NOT scenario-set based.

```json
{
  "name": "btcusd_mock",
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
backtesting batch uses** (the shared `MountPreparer`, #438) — the mock is "a scenario replayed
through the live decision path". `tick_source` then carries only the transport (`type`, replay
delay, the `freeze_after_ticks` outage drill). A **live** session has no `scenario_settings` —
its data streams from the broker.

Sections not listed here (`execution`, `clipping_monitor`, `order_guard`) inherit their values from `app_config.json::autotrader` — only specify them in the profile when overriding a default.

`deployment` is the one block that inherits nothing and **must** be present: see
[The deployment declaration](#the-deployment-declaration-497) below.

| Section | Purpose | Notes |
|---------|---------|-------|
| `name` | Session name | Used for run directory (`runs/live/<name>/`) |
| `symbol` | Trading pair | Single symbol per session |
| `broker_type` | Broker identifier | Maps to MarketType via `market_config.json`; broker connection settings read from there too |
| `adapter_type` | `mock` or `live` | Mock: no credentials needed |
| `deployment` | `{"continuous": true\|false}` | **Mandatory — the loader refuses a profile without it.** Whether this profile's sessions form ONE deployment whose ledger rows join into one history (#497) |
| `dry_run` | `true` / `false` / omit | Optional per-profile override of the global `market_config` dry_run. Omit = inherit the broker default. Setting it (especially `false` = live) overrides the global default for this profile only and logs a loud override warning at startup |
| `strategy_config` | Workers + DecisionLogic | Same format as scenario sets |
| `scenario_settings` | Mock data + account (#438) | **Mock only.** Data window (`start_date`/`end_date`/`max_ticks`, optional `data_broker_type`) resolved via the shared index/prep stack; `data_sentiment_type` for SIGNAL workers; `balances` (spot: `{"USD": X, "ETH": Y}`; live: fetched from the broker at startup); optional `stress_test_config.stale_data_stress`. Absent for live |
| `tick_source` | Tick transport | Mock: `tick_delay_ms` replay speed + `freeze_after_ticks`/`freeze_duration_s` outage drill (#436). Live: WebSocket (#232). The data window lives in `scenario_settings` |
| `execution` | Runtime parameters | Inherits from `app_config.autotrader.execution`; override per profile if needed |
| `clipping_monitor` | Timing config | Inherits from `app_config.autotrader.clipping_monitor`; strategy: `queue_all` or `drop_stale` |
| `display` | Dashboard config | Inherits `enabled: true`, `update_interval_ms: 300` — test profiles set `enabled: false` |
| `order_guard` | Pre-validation guard | Inherits from `app_config.autotrader.order_guard`; override per profile if needed |
| `safety` | Circuit breaker | Always profile-specific. Omit or set `enabled: false` to disable |

**Config cascade (2-level):**
```
configs/app_config.json::autotrader  ← Level 1 (global defaults for all sessions)
  ↓ deep_merge (profile wins)
autotrader_profiles/*.json           ← Level 2 (session-specific overrides)
```
`user_configs/app_config.json` can override the `autotrader` block too — same mechanism as all other app_config sections.

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
command nobody typed, so a deployment declared there would fragment at exactly the restarts it
exists to span — silently, because a missing flag looks like a one-off. **The flag whose absence
is expensive lives in the profile; the flags whose absence is harmless live on the command
line.** Everything shown — the display title, the startup line in the session log, the ledger
row — is the RESOLVED answer, never the declaration.

The identity travels through the cold-start carry-over (store 4b): a session writes it at boot
and at shutdown, and the next session reads it before its own run header is written.

**The carry-over's write gate is split by what each field CLAIMS**, which is what makes this
rehearsable at all. The session key and the open position book are claims about the VENUE — this
key sent orders, this book is open — and a dry run sent nothing anywhere, so its successor must
not inherit either (#355: a key recorded by a dry run would let a restart loop evict the key that
owns a real resting order). The risk baseline, the reported drawdown curve and the deployment
identity are OUR OWN records: numbers this process computed, true whether or not the venue was
real, and written either way. A refused boot still writes nothing at all.

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

A live run's ledger row carries two hashes over the profile, and the split is deliberate:

| Hash | Covers | Answers |
|---|---|---|
| `param_hash` | `strategy_config` | Did the bot's DECISIONS change — what #512 compares a backtest against |
| `profile_hash` | the operational rest — safety, order guard, execution, tick source, capital, the deployment declaration | Did what the session DOES change, without changing what it decides |

One wide hash would answer neither. A raised stop level must not read as a different strategy —
that would put an otherwise comparable run beyond comparison; and a changed RSI threshold must
not pass as mere operation. Both are computed from the LOADED config, so a value the loader
resolved is fingerprinted as resolved; `config_path`, `name` and `symbol` are excluded, because
where a profile sits on disk is not a property of the run.

Neither hash decides anything. A change is RECORDED and reported — `run_index_cli.py
deployments` marks the session it happened on — and whether the halves may be compared is a
judgement a person makes. The full resolved configuration rides the same row, so a tool can say
`rsi_buy_threshold: 45 → 40` rather than only "the hash differs".

End-user view of all of this: [Live Deployment & Ledger](../user_guides/live_deployment_ledger_guide.md).
