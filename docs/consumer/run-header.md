# The run header

Every run writes one record before it does anything else: what the run is, what it was started
from, who started it and which code ran. It exists before the run has reported anything, so it
answers for a run that is still going and for one that died before its first report. This document
is the authoritative list of what a header holds — every key, what it means, and the label to show
it under.

**Not here:** what a run DID — its figures and its outcome — which the [run list](/api/v1/docs/runs)
joins from the record written at its close; and how to tell the kinds of run apart, which
[run kinds](/api/v1/docs/run-kinds) explains.

**Route**

```
GET /api/v1/reports/runs/{run_id}/header
```

An unknown run answers `run_not_found` (404). A run the index lists whose header file is missing
answers `run_header_missing` (404); if the run itself is gone, a rebuild of the run index drops its
row. A header that no longer matches its model answers `artifact_unreadable` (409), naming the
fields that failed.

## What a run records, and why an old one stays true

A run writes down, when it starts, what it used, and nothing rewrites that later — so an old run
keeps saying what it ran with even after the files it came from have changed. Where each part is
served:

| What the run used | Served as | Kept as |
|---|---|---|
| the code | `code_identity` on this header | the commits, plus the patch that restores an uncommitted change |
| the configuration it was given | `config_id` on this header; the content at [config](/api/v1/docs/config) | a frozen copy; a sweep's combinations all name the base set, and what each changed is on its row of the [sweep](/api/v1/docs/sweeps) |
| the configuration it ran with | `rendered_config_id` on this header — a session's rendered profile, a backtest's rendered scenario set with one section per scenario; every sweep combination names its own | a frozen copy; no route serves its content yet |
| the broker configuration | `broker_config_id` beside `config_hash` on [broker](/api/v1/docs/broker) | a frozen copy, for a session and a backtest alike |
| the input data | `data_windows` on this header; for a backtest the data's kind per scenario on [scenario details](/api/v1/docs/scenario-details), and for a sweep's combination also the file counts on its [sweep](/api/v1/docs/sweeps) row | a description, never the files themselves |

Two runs naming the same id share one frozen copy; an id cannot be passed back to a route as a
parameter. What no run keeps is the content of the tick and bar files it read — a later re-import
can change what a new run reads over the same window — and a live session keeps no copy of the
ticks it received from the venue: only the collector's recording of that window exists, and that
is what a backtest of it reads.

## Labels

Every key below carries the label to show it under. Use it as written — translate it if you
translate, but do not coin another: the label is how a reader finds the term in these documents,
and a word invented beside it already means something else somewhere.

## What the header holds

| key | label | what it says | values | null or empty | on the run list |
|---|---|---|---|---|---|
| `run_id` | Run | the run's identity: a timestamp and a hash | | never | `run_id` |
| `start_time` | Started | when the run started, in UTC | | never | `start_time` |
| `run_type` | Pipeline | which pipeline ran it | `simulation` — a backtest · `autotrader` — an AutoTrader session | never | `group` |
| `run_name` | Name | the scenario set's name for a backtest, the profile's name for a session | | never | `name` |
| `parent_id` | Parent id | the run this one belongs to | | it stands alone | `parent_id` |
| `parent_kind` | Parent | what kind of parent `parent_id` names | `deployment` · `sweep` | it stands alone — or, with `parent_id` set, the run predates the field and its kind is unknown | `parent_kind` |
| `config_snapshot` | Configuration file | the NAME of the file the run started from — never a path | | never | `config_snapshot` |
| `config_id` | Configuration id | the content identity of the configuration the run was given: two runs naming one id were given the same configuration | | empty: the run predates registered configurations, or its registration failed | `config_id` |
| `rendered_config_id` | Rendered configuration id | what the run ran with, every default filled in — a session's rendered profile, a backtest's rendered scenario set | | empty on a run recorded before the field existed — for a backtest, before backtests rendered theirs — and on one whose configuration could not be rendered or frozen | — |
| `app_version` | App version | the version of the application that ran it | | never | `app_version` |
| `git_commit` | Commit | the commit the run's code came from | | unknown | `git_commit` |
| `reporting` | Reporting | whether the run was commissioned to write reports | `expected` · `none` | never | `reporting` |
| `origin` | Origin | who started the run — the block below | | the run predates the block | the four `origin_` fields |
| `code_identity` | Code | which code ran — the block below | | the run predates the block, or was not commissioned to report | — |
| `ticks_from` | Ticks from | where the run's ticks came from | `archive` · `venue` | the run predates the field | `ticks_from` |
| `orders_to` | Orders to | where the run's orders went | `simulated` · `venue` | the run predates the field, or a session whose dry-run setting was refused — it never traded | `orders_to` |
| `data_windows` | Data windows | the market window each unit was declared to cover | | the run predates the field | `data_windows` |
| `run_purpose` | Purpose | what the run is for | `regular` · `fixture` · `certificate` | the run predates the field | `run_purpose` |
| `features_beyond_venue` | Beyond the venue | the order features a backtest was allowed beyond what its venue's API holds, as its scenario set declared; empty for every other run | `stop_loss` · `take_profit` | the run predates the field | `features_beyond_venue` |
| `report_contract` | Report contract | the API contract the run's reports were written under | | the run predates the field | `report_contract` |

## Origin — who started the run

In these documents the word *origin* on its own means only this: who started a run, for whom, and
where. Three served fields still carry the word for something else and are being renamed: the
configuration directory row's `origin` (where a file lives), a feed-health episode's `origin` (real
or stress-injected), and the `origin_classes` and `origin_evidence_grades` of scenario details and
sweep combinations (the class of instance that produced a run's data).

| key | label | what it says | values | on the run list |
|---|---|---|---|---|
| `origin.channel` | Started via | how the run was started | `cli` — the command line · `sweep` — an optimization sweep · `direct` — a direct call · `api` — through the API | `origin_channel` |
| `origin.client` | Client | who made the call | `console` at a terminal, otherwise the API consumer's name | `origin_client` |
| `origin.principal` | Started for | on whose behalf the run was started — the console's own principal, or the account a token acts for, which may be a person or a service | `operator` at the console, otherwise an account id | `origin_principal` |
| `origin.host` | Host | the installation the run ran on: its minted identity, `h_` and six characters | | `origin_host` |
| `origin.allow_dirty` | Uncommitted code allowed | whether real orders were explicitly allowed from code that was not committed | `true` · `false` | — |
| `origin.first_start` | Declared first start | whether a session trading real money was declared the bot's first start — the one way it starts without a carry-over to continue; `false` on a run recorded before contract 37 | `true` · `false` | — |
| `origin.accept_composite_change` | Changed configuration accepted | the composite the start was told it may continue what the venue still held under — the key the start was given, recorded as typed; the session's `composite_change_accepted` warning says whether it let anything through; null when the start was given none, and on a run recorded before contract 41 | a key, e.g. `7f3a9c2e` · null | — |

## Code — which code ran

`code_identity` names this repository (`framework`), every other repository a component came from
(`repositories`), and one entry per decision logic and worker (`components`).

**Every path in it is relative to a repository the run recorded.** A repository's `root` — and
its `patch_ref` — is `.` for this repository, its path inside this one for one within it
(`user_algos`, `user_algos/vendor/lib`), and its directory's name alone for one outside. A
component's `source_path` is relative to its own repository; its `name` and `type` — a decision is
named by its type, which may be a path — are relative to this repository first. A path that lies
in no recorded repository is shown by its file name only. No path of the machine the run ran on is
served.

| key | label | what it says |
|---|---|---|
| `root` | Repository | which repository, as above |
| `in_repository` | Versioned | whether that directory is under version control at all: false also for a directory the repository around it ignores; null when version control could not be asked |
| `commit` · `branch` | Repository commit · Branch | as checked out when the run started; null when unknown |
| `dirty` | Uncommitted changes | whether the working tree differed from its commit |
| `uncommitted_count` · `changes` | Uncommitted count · Changes | how many changes there were, and the first of them |
| `diff_hash` | Change hash | a hash over the content of every changed path |
| `patch_ref` | Patch | where the patch restoring those changes was kept, relative as above; null when none was |
| `patch_excluded` | Not in the patch | changed paths deliberately left out, such as credential files |
| `restorable` | Restorable | whether the repository's code can be put back exactly |
| `components[].role` | Role | `decision` · `worker` |
| `components[].name` · `type` · `version` | Component · Type · Component version | the instance's name — a decision's is its type — the type the configuration named, the version the component declares |
| `components[].source_path` · `repository` | Source · Repository | the file it was loaded from, the repository it belongs to |
| `components[].package_digest` | Package digest | a hash over the files of a user's own component |
