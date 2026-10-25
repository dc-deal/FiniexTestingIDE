# Composite Key Tests

`tests/framework/composite/` — which accounts ran the same strategy under the same conditions, and
may therefore add up to one result (#615).

Run: `python -m pytest tests/framework/composite/ -v`
Launch entry: `🧩 Pytest: Composite Key (All)`

---

## What this suite exists to catch

A run's accounts are folded into one result only when they share a composite key. The key is a
projection of each account's rendered configuration through the composite definition, so a wrong
projection either joins accounts that ran different strategies — a sum no strategy produced — or
splits accounts that ran the same one.

| Failure | What it costs |
|---|---|
| A defining value does not reach the key | Two strategies fold into one result |
| A neutral value reaches the key | One strategy splits over windows, symbols or balances |
| The floor can be loosened by a file | A backtest joins real money, or spot joins margin |
| A value no rule reaches | A decision nobody made — it counts as defining, which can only split |
| A rule that reaches no value | A rule for a value that no longer exists |
| A comment, a resolved type path or `12` against `12.0` moves the key | The same strategy reads as two |

Not here: where the key is stamped and checked — the refusal of a set, the header, the ledger row —
which later steps of #615 add with their own tests.

## The tests

`test_composite_definition.py` — the definition file and its model:

- the shipped definition loads, with rules for both pipelines; a stress block is excluding in both;
  a section's comment is no rule
- a rule for a floor path, the floor tier itself, a rule that is no path, an unknown section or an
  unknown tier is refused
- the workspace override changes one rule and keeps the rest, and cannot loosen the floor
- each pipeline's section has its own fingerprint, so an edit of the session half leaves every
  backtest key as it was

`test_composite_key_builder.py` — the projection, over small hand-built documents:

- **What joins and what splits:** a neutral value joins; a defining parameter splits the strategy
  part only, a defining condition the conditions part only; the floor splits whatever the definition
  relaxes; the account part takes the resolved currency and the account model; the run axes split a
  rehearsal from real money; an unclassified value splits; the key carries the definition and the
  version
- **What reads alike:** an integer and a float of one value; a bool is no number; a comment at any
  level; a type path a factory resolved on one machine against the configured one
- **The excluded member:** a stress test that is switched on excludes the account without moving
  its key; a stress block with every test off is no stress test; an excluded member belongs to no
  composite
- **The changed values:** every differing value is named in path order with its tier and part, a
  neutral one with no part; a value only one side holds is a change
- **A session document:** its blocks are lifted, and its kind is no value

`test_composite_catalog_completeness.py` — both directions over what the shipped configurations
render: every scenario of every scenario set the loader accepts, and every AutoTrader profile.

- every value has a tier — a floor path or a rule reaches it
- every rule of the definition reaches a value
- the floor's document paths are in both kinds of document
