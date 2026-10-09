# Introduction to FiniexTestingIDE

FiniexTestingIDE runs one trading strategy in two worlds: in a **backtest** over recorded market
data, and in an **AutoTrader session** against a venue. Its central claim is that the two agree —
the same decision logic and the same workers, fed the same data, make the same decisions. Every
other page in this documentation assumes you can tell the kinds of run apart and that a word
means one thing. This page gives you both, in one place, and links to where each part is
explained.

Not here: how to write a strategy ([Quickstart](user_guides/quickstart_guide.md)), how a
configuration is built up ([Config Cascade](config_cascade_guide.md)), and what the API serves
([API Server](architecture/api_server_architecture.md)). The meaning of each term is in the
[Glossary](glossary.md); look a word up there before you use it for something new.

## Two pipelines, one strategy

```
scenario set ──► simulation pipeline ──► TradeSimulator        (run type `simulation`)
                 batch · one subprocess per scenario · archive data

profile ───────► AutoTrader pipeline ──► LiveTradeExecutor     (run type `autotrader`)
                 one session · one symbol · a mock or a live adapter
```

A **scenario set** describes a backtest: one or more scenarios, each a symbol over its own market
window, with a configuration that cascades from the application defaults over the set's `global`
block to the scenario ([Process Execution](process_execution_guide.md)). An **AutoTrader profile**
describes one bot: one symbol, one broker, one account ([AutoTrader
Configuration](autotrader/autotrader_configuration.md)). Both hand the same kind of decision logic
and workers the same kind of ticks and bars.

## The kinds of run

Two facts separate them, and every run records both at its start, from its resolved
configuration: where its **ticks** came from, and where its **orders** went.

| Kind | Run type | Ticks from | Orders to |
|---|---|---|---|
| Backtest | `simulation` | `archive` | `simulated` |
| Mock session | `autotrader` | `archive` | `simulated` |
| Dry run | `autotrader` | `venue` | `simulated` |
| Real-money session | `autotrader` | `venue` | `venue` |

- **Backtest** — the simulation pipeline runs a scenario set over the tick archive. Each scenario is
  a run unit with its own window; fills come from the trade simulator.
- **Mock session** — an AutoTrader session whose profile sets `adapter_type: mock`. It replays the
  window in its `scenario_settings` from the archive through the whole AutoTrader stack — the live
  executor, the order guard, the safety layer, cold start — against a broker-neutral mock adapter.
  It places no order anywhere, and it is reported like every AutoTrader session, not like a
  one-scenario backtest ([Mock Adapter](architecture/mock_adapter_guide.md)).
- **Dry run** — an AutoTrader session on a live adapter whose `dry_run` resolves true. The ticks are
  real; every order is sent to the venue to be validated and is never placed; a local simulator
  fills it when the market reaches its price. A profile may switch a dry run on, never off: that
  belongs to the broker's own setting. Planned to become the named mode *paper* (#304).
- **Real-money session** — a live adapter with `dry_run` false: orders are placed at the venue and
  move the account. "Live trading" means this, and only this.

AutoTrader profiles live in folders named for their purpose: `mock/` for mock sessions,
`observation/` for dry runs pinned in the profile, `field_study/` for the real-money acceptance
test ([Field Study](tests/live_field_study/field_study_guide.md)), `production/` for the bots that
trade for real.

The code keeps one older word: classes such as `LiveTradeExecutor` say *live* for the execution
stack every AutoTrader session runs, mock sessions included. In prose this documentation never
uses a bare *live* for a kind of run — it says which of the four it means.

## Runs that belong together: sweeps and deployments

- A **sweep** searches a parameter grid. Each combination is an ordinary backtest whose header names
  the sweep as its parent; the sweep itself has no header and is not a run. It is the level above
  the reports — the ranking ([Parameter Optimization](architecture/parameter_optimization_system.md)).
- A **deployment** joins the sessions of one bot into one history. A profile that declares
  `deployment.continuous: true` gives each session a parent: the deployment. Each session is still
  its own run and writes its own rows; the deployment history reads across them. A session that
  belongs to none is **one-off** ([Deployment Ledger](user_guides/deployment_ledger_guide.md)).

## What every run leaves behind

Every run gets an id and a directory with a `header.json`, written first — before anything can
fail, because a run that crashed is the one somebody needs to identify. The header says what the
run was: its type, its kind, its market windows, which configuration and which code it started
from ([Run Origin & Code Identity](architecture/run_origin_and_code_identity.md)). Its report
sections land beside the logs, and the two pipelines produce different sets — the run list says
which ones a run has ([Reporting Pipeline](architecture/reporting_pipeline.md)). Its results are
booked into the ledger, one row per booking period and currency
([Accounting Periods](architecture/accounting_periods.md)).

The header answers the main questions about a run, each with a field of its own, so no reader has
to infer one from another:

| Question | Field |
|---|---|
| Which pipeline ran it? | `run_type` — `simulation` or `autotrader` |
| Which kind of run is it? | `ticks_from` · `orders_to` — the table above |
| Does it stand alone? | `parent_kind` · `parent_id` — the sweep or the deployment it belongs to |
| Who started it, and where? | `origin` — channel, client, principal, host |
| Which configuration did it run? | `config_snapshot` · `config_id` |
| What is it FOR? | `run_purpose` — `regular`; `fixture`, a run constructed to show something (every run a test starts, every run a consumer pins); `certificate`, a release-gate run |
| Why does its configuration exist? | the configuration's own `description` |

`run_purpose` and `description` are declared at the top of a scenario set or a profile; a
configuration that says nothing is `regular`. Every configuration a test runs declares `fixture`,
and the test session refuses a run whose configuration does not. Your own configurations, in
`user_algos/`, never declare one: their runs are always `regular`.

Where each of these lives, and which stores can be rebuilt, is the
[Data Storage Layout](architecture/data_storage_layout.md).

## The words

A term in this project means one thing, and the [Glossary](glossary.md) says which. Where one word
had several meanings, each got a qualifier — *price basis* and *compute basis*, *AutoTrader
profile* and *generator profile* — and the bare word is not used on its own. When you need a word
the glossary does not have, add it there in the same change.

## Where to go next

- Write a first strategy: [Quickstart](user_guides/quickstart_guide.md)
- Run things from the command line: [CLI Tools](cli_tools_guide.md)
- Understand an AutoTrader session: [AutoTrader Architecture](autotrader/autotrader_architecture.md)
- Find any document: [Documentation Index](documentation_index.md)
