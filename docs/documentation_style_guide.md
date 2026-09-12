# Documentation Style Guide

How documentation is written in this project. Eleven rules, each with an example from this
tree — none invented.

The premise: **documentation is read far more often than it is written, and the reader is
usually in a hurry and slightly lost.** Every rule below follows from that.

---

## 1. Open with the problem, not with a label

The first paragraph earns the rest of the document. State what goes wrong without this thing,
then what it does about it. A label tells the reader nothing they could not read off the title.

Label — the reader still does not know why this exists:

> This guide shows you how to create a custom trading bot with FiniexTestingIDE.

Problem — `architecture/reporting_pipeline.md`:

> Run statistics used to be derived **and** formatted inside the console print step, so the API
> would have to re-derive them and the two pipelines drifted apart. The reporting pipeline
> separates the three concerns so every consumer renders identical data from one model.

## 2. Say what is *not* here

A reader who is in the wrong document should find that out in the first screen, not the third.
State the boundary and link across it.

From `architecture/live_execution_architecture.md`:

> For shared architecture (AbstractTradeExecutor, fill processing, portfolio, design decisions):
> see `architecture_execution_layer.md`

## 3. Tell the reader what to do, not only what the document contains

A guide that lists its contents leaves the reader to work out the next action.

From `user_guides/adapter/adapter_development_guide.md`, after naming the two reference
implementations:

> Read either alongside this guide.

## 4. Claim first, evidence second

State the rule flat, then prove it. Do not build up to it — the reader who stops halfway should
still have the rule.

Weak: *"There are several considerations when deciding how often to write state to disk,
including performance and durability trade-offs."*

Strong: *"The position book is written on structural change, not on every tick. One write costs
11 ms on this tree, and a trailing stop would otherwise write on nearly every tick of a trend."*

## 5. A number carries its date and its source

Measurements age. A number without a date cannot be re-checked, and an undated number that has
gone stale is worse than no number, because it looks verified.

> Measured 2026-08-31, `/app` against the container's own ext4: `stat` 616× · `read` 110× ·
> `write` 65× · a directory walk 282×.

## 6. Never state a count that will grow

"The fourteen stores", "Total tests: 87", "three categories" above a table that counts — each
goes stale on the next addition, and the enumeration is directly underneath. What counts at
**runtime** may be printed, because it is derived and cannot drift.

## 7. One H1 per document

The H1 is the document's identity. A second one means the file is two documents.
Measured 2026-09-12: every document in this tree holds to it.

## 8. Name a heading for what is under it

`## Overview` was the most common heading in this tree when it was last measured — 47
occurrences on 2026-09-11 — and it says nothing a
reader at the top of a document does not already know. `## Why`, `## What breaks without this`,
`## Tick flow, phase by phase` all say something.

And use **one name per concept**, the same rule that governs file and store names. Currently
split on 2026-09-11:

| Same section, two names |
|---|
| `Running` vs `Running the Tests` |
| `Fixtures` vs `Fixtures (conftest.py)` |
| `Architecture` vs `Architecture Notes` |

## 9. Wrap prose at roughly 100 characters

Every commit in this project is reviewed line by line. A one-word change inside an
800-character paragraph produces a diff of the whole paragraph; the same change in a wrapped
paragraph produces a one-line diff.

*Measured 2026-09-12: 64 documents wrapped, 45 did not, and the longest single prose line
ran to 717 characters.* Tables, code blocks and diagrams are exempt.

## 10. Draw the flow, write the rest

An ASCII diagram earns its place when the subject is a **flow, a stage split, or a boundary** —
something the reader would otherwise have to hold in their head while reading three paragraphs.
It does not earn its place as decoration for something a sentence already says.

```
CAPTURE  (source-specific, raw)      DERIVE (shared, pure)        PRESENT (thin renderers)
  sim:  List[TradeRecord]  ──┐                                      ┌─► console
        per scenario         ├─► postprocessor → ReportModel ──────┼─► CSV
  live: List[TradeRecord]  ──┘      (off the hot loop)              └─► API → frontend
```

## 11. Link instead of restating

A fact restated in a second document goes wrong the day the first one changes, and the copy
nobody edits is the one that decays. Link to the owner of the fact. This holds for
configuration defaults, thresholds, file paths and counts alike.

---

## Three audiences, three depths

Documentation here serves readers at different depths, and mixing them serves none of them:

| Layer | Reader | Wants |
|---|---|---|
| **Quickstart** (`docs/user_guides/`) | writing their first algorithm | the contract their code sees, and a worked example. No internals. |
| **Architecture** (`docs/architecture/`, `docs/autotrader/`) | debugging, optimising, or contributing | what happens between the call and the broker — classes, threads, queues, storages |
| **Integration** (`docs/user_guides/adapter/`) | building a broker adapter | the contract to satisfy, the reference implementation, the pitfalls |

The cross-layer rule: a quickstart never explains internals. If it has to, the API has a leaky
abstraction and that is the bug to fix.

---

## Before publishing

- Does the first paragraph say what breaks without this?
- Would a reader in the wrong document know within one screen?
- Is every number dated, and does every count refer to something that cannot grow?
- One H1, headings named for their content, prose wrapped?
- Is anything restated here that another document owns?
