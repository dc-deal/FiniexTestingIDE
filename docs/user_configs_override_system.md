# `user_configs/` Override System

**How personal workspace overrides on top of the committed `configs/` defaults are resolved.**

The application has two distinct override mechanisms that share the `user_configs/` folder. They look similar from the outside but follow different rules — understanding the split prevents surprises.

---

## TL;DR

| If you want to ... | Override file goes to | Behaviour |
|---|---|---|
| Tweak one or two settings inside a shared config | `user_configs/<name>.json` | **Content-merge** — only the keys you set override the base, the rest is inherited |
| Use your own version of a discrete file (credentials, scenario set, AutoTrader profile) | `user_configs/<subfolder>/<file>.json` | **File-replace** — your file is used instead of the base file, no merging |

The two systems are independent. Don't expect content-merge for credentials, and don't expect file-replace for `app_config.json`.

---

## System A — Content-Merge (shared config files)

These six configs follow the merge pattern. Each has a base in `configs/` and an optional override in `user_configs/` with the same filename:

| Config | Base | User override | Pydantic schema |
|---|---|---|---|
| App config | `configs/app_config.json` | `user_configs/app_config.json` | `AppConfig` |
| Market config | `configs/market_config.json` | `user_configs/market_config.json` | `MarketConfig` |
| Import config | `configs/import_config.json` | `user_configs/import_config.json` | — |
| Test config | `configs/test_config.json` | `user_configs/test_config.json` | — |
| Discoveries config | `configs/discoveries/discoveries_config.json` | `user_configs/discoveries_config.json` | — |
| Generator config | `configs/generator/generator_config.json` | `user_configs/generator_config.json` | — |

### Merge rules

Implemented in [`python/framework/utils/config_merge_utils.py`](../python/framework/utils/config_merge_utils.py) by `deep_merge()`.

**1. Dicts are merged recursively.**
Override only the keys you want to change — the rest stays at the base value.

```json
// base
{ "logging": { "level": "INFO", "color": true } }

// user override
{ "logging": { "level": "DEBUG" } }

// merged
{ "logging": { "level": "DEBUG", "color": true } }
```

**2. Primitives and plain lists are replaced wholesale.**
A list in the override completely replaces the list in the base. There is no element-wise merging by default.

```json
// base
{ "paths": { "user_algo_dirs": ["user_algos/"] } }

// user override
{ "paths": { "user_algo_dirs": ["/ext_algos"] } }

// merged → "user_algos/" is lost!
{ "paths": { "user_algo_dirs": ["/ext_algos"] } }
```

→ When overriding a list, include all base entries you want to keep.

**3. Atomic keys are replaced wholesale even if they are dicts.**
Used for keys whose contents must not be cherry-picked (e.g. `balances` in `trade_simulator_config` — a scenario that sets `{"JPY": 50000}` must not inherit `{"EUR": 10000}`). Declared per-call via the `atomic_keys` parameter of `deep_merge`.

**4. Lists-of-dicts can be merged by identifier (opt-in).**
A loader can declare `list_merge_keys={'fieldname': 'identifier_key'}`. Entries in that list are then matched by the identifier value, deep-merged per entry, and base-only entries are preserved.

Currently used for the `brokers` list in `market_config.json` — matched by `broker_type`. This is why a minimal `user_configs/market_config.json` works:

```json
{
    "brokers": [
        {
            "broker_type": "kraken_spot",
            "dry_run": false
        }
    ]
}
```

The `mt5` broker from the base config is preserved, only `kraken_spot.dry_run` is updated. An override entry without the identifier raises `ValueError` with a clear message.

### Test isolation

Test runs set the env var `FINIEX_CONFIG_ISOLATION=1` (handled in [`tests/conftest.py`](../tests/conftest.py)). With this active, all loaders skip the `user_configs/` step — the personal workspace must never bleed into the deterministic test suite.

---

## System B — File-Replace (discrete files)

These resources are not merged in content. The loader checks whether the user file exists; if yes, it is used instead of the base file. Otherwise the base file applies.

| Resource | Base folder | User folder | Resolution |
|---|---|---|---|
| Credentials | `configs/credentials/<file>.json` | `user_configs/credentials/<file>.json` | User file wins if present (per-filename check) |
| AutoTrader profiles | `configs/autotrader_profiles/.../*.json` | `user_configs/autotrader_profiles/.../*.json` | Each profile is standalone — no merging across folders |
| Scenario sets | `configs/scenario_sets/*.json` | `user_configs/scenario_sets/*.json` | Each set is standalone — discovered by filename |

Don't mix this with System A. A partial credentials file under `user_configs/credentials/` would not merge against the base file — it would simply be the new full credentials file.

---

## Adding a new override-aware config

For System A:

1. Place the base JSON under `configs/<name>.json`.
2. Add a loader class under [`python/configuration/`](../python/configuration/) following the existing pattern (see [`market_config_loader.py`](../python/configuration/market_config_loader.py) as the most feature-complete example — it uses `list_merge_keys`).
3. Add a Pydantic model under [`python/framework/types/config_types/`](../python/framework/types/config_types/) for schema + defaults. Validate the merged dict with `validate_merged_config()` from `config_merge_utils.py`.
4. If the config has a list-of-dicts that should merge element-wise (e.g. `brokers`), declare its identifier in `list_merge_keys` when calling `deep_merge`.

For System B: place the file in the matching subfolder. The discovery logic in the consuming code (credentials cascade, profile finder, scenario set finder) handles the rest.

---

## Related docs

- [Config Cascade Guide](config_cascade_guide.md) — the scenario-set cascade (`app_config → global → scenario`) which is a separate, content-internal system on top of merged `app_config.json`.
- [Broker Config Guide](broker_config_guide.md) — broker-specific config files referenced from `market_config.json`.
- [AutoTrader Architecture](autotrader/autotrader_architecture.md) — AutoTrader profile cascade (`app_config.autotrader → profile`).
