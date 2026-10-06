# The configuration a run was commissioned with

A run's record can say that a configuration **moved** — a deployment's history marks the session
where the strategy's parameters changed, or where the rest of the profile did. What it could not
say was *what* moved, because the record carries two pointers, a file name and a content id, and
nothing served what they point at. This route is the configuration itself, parsed, exactly as the
run was given it.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/config
```

**What is not here:** what the run did with it. The figures are on
[run summary](/api/v1/docs/run-summary) and in the run list; which scenarios actually ran is on
[scenario details](/api/v1/docs/scenario-details). The broker's rules are not in this body either
— a configuration names a broker, and what that broker's specifications were is
[broker](/api/v1/docs/broker).

## Four fields

| Field | Meaning |
|---|---|
| `run_id` | the run this configuration was served for |
| `config_snapshot` | the file name the run's header declared |
| `config_id` | the content fingerprint of that configuration |
| `config` | the configuration itself, parsed |

## The body is the file, parsed

`config` is the configuration's own content as JSON. Parsed rather than handed over as text,
because a consumer comparing two runs wants the difference in the **values**, not in the
whitespace.

Its shape is the shape of whatever started the run: a scenario set for a backtest, a profile for
an AutoTrader session. [Run kinds](/api/v1/docs/run-kinds) says which of the two you are holding.

On a scenario set, `config.scenarios` is the **declared** list — every scenario the configuration
names, the ones carrying `enabled: false` included. It is not the list that ran. A disabled
scenario appears here and in no other section; one that was enabled but refused before it started
appears here and on [scenario details](/api/v1/docs/scenario-details) with its reason.
[Run summary](/api/v1/docs/run-summary) states the difference as numbers, so the three lists never
have to be compared by hand.

No local path reaches you. The configuration store's own index row carries the workspace path the
file was read from, and that row is never served — the content is.

## `config_id` is an identity, not a handle

It is a SHA256 over the configuration's normalised content. Two runs naming the same id ran the
same configuration, and a changed file mints a new one.

It names the **input**: what the run was given, before any layer was merged into it. An AutoTrader
session resolves a rendered profile from that input — the layers merged, every default filled, the
broker's entry beside it — and records that separately. This route does not serve the rendered
form.

It is served here as well as in the run list, so you can assert that the body you received is the
one the index attributes to this run rather than trusting the join. Nothing on this API takes a
configuration id as a parameter; it is for comparing, not for fetching.

It is **empty** on a run that started before the configuration store existed, and on a run whose
configuration could not be registered. Those are also the runs this route refuses.

## Comparing two sessions of a deployment

A deployment's session history marks where something moved: `strategy_changed` when a session's
strategy parameters differ from the previous session's, `operation_changed` when the rest of the
profile does. Those marks say **where**. This route says **what** — fetch the configuration of the
session before the mark and of the session carrying it, and compare the two bodies. See
[deployments](/api/v1/docs/deployments).

## Two refusals, and the second one is ordinary

| `error` | What it means |
|---|---|
| `run_not_found` | the identity is unknown — no such run in the run index |
| `config_snapshot_missing` | the run exists, and its configuration cannot be resolved |

The second is a normal state rather than a defect. A run's header is written when the run starts
and its configuration is filed afterwards, so a run that died in between declares a snapshot it
never filed. A run older than the configuration store carries no content id at all, and neither
does one whose configuration could not be registered.

The two are deliberately separate, and this route keeps its own pair rather than the four a report
section answers with: a configuration is registered at a run's **start**, a report section written
at its **end**, so a missing configuration says nothing about whether the run reported. The whole
refusal vocabulary is in [errors](/api/v1/docs/errors).

## Reading it beside the file on disk

[The directory](/api/v1/docs/directory) lists every configuration file that can start a run and
says what each one declares **now**. This route says what one run was handed **then**. The two
disagree the moment the file is edited, and that is the point: a run's configuration is frozen
with the run, and the file goes on changing.
