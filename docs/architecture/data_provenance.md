# Data provenance — who wrote a file, and what that is allowed to mean

A file this project imports arrives from a producer we do not run: a tick collector, a trading
terminal's export, a signal producer. Byte for byte, a file from a development machine looks
exactly like one from a production server — and on 2026-09-15 one reached the import path and was
noticed by luck rather than by a contract.

This document describes the contract that replaces the luck: what a producer states, what this
side decides, and where each answer is recorded. It does **not** describe how a producer mints its
identity — that lives in each producer's own repository — and it does not cover credentials, which
are a separate axis ([`credentials_layout.md`](credentials_layout.md)) deliberately kept from
proving anything about provenance.

## The inversion, and why a declared environment does not work

The obvious design has the producer write `environment: "production"` into its output. It fails in
a way that is hard to see and easy to reach: a configuration travels between machines and the
truth does not. The collector's own configuration carried exactly that field with a tracked
default of `production`, never overridden on the development box — so the obvious fix would have
stamped every development file as production, confidently.

So the contract inverts:

> **A producer states an IDENTITY it cannot falsify. It says nothing about what that identity
> means. The consumer maps identity to meaning, in its own registry.**

The identity is minted where the DATA lives, not by the process: a copied configuration carries
none, so a second machine writing its own archive mints its own and arrives as unregistered. That
is also why it beats deriving an identity from the machine — a container renews its hostname on
every rebuild, and an identity that changes on every rebuild is worse than none.

## What a file carries

```json
"origin": {
  "instance_id":      "a7f21c0b4e88",
  "collected_on":     "collector-prod",
  "producer":         "finiex-data-collector",
  "producer_version": "1.1.0"
}
```

| Field | What it is | Who decides on it |
|---|---|---|
| `instance_id` | 12 lowercase hex, minted once at the producer's data root | the consumer, through its registry |
| `collected_on` | hostname | **nobody** — compared against the last value seen for that identity, never interpreted |
| `producer` · `producer_version` | which program wrote it | nobody |

Only `instance_id` is decided on. The rest is forensic, and a free string that something branches
on becomes a vocabulary nobody agreed.

A second block, `transport`, is reserved for the case where one producer CARRIES another's files —
a collector serving terminal exports it did not write. `origin` names whoever wrote the bytes; a
carrier never overwrites it.

## What this side decides

The registry is `configs/data_origins.json`, with the usual cascade to `user_configs/`. The
tracked copy carries the schema and development entries; production entries belong in the
workspace copy, because a name describes topology.

```json
{
  "origins": {
    "a7f21c0b4e88": { "class": "production", "role": "primary", "name": "collector prod" }
  },
  "attestations": [
    { "scope": { "broker_type": "kraken_spot", "up_to_format": "1.5.0" },
      "class": "production", "attested_by": "operator", "attested_at": "2026-09-19",
      "basis": "everything this archive held before the origin block existed" }
  ]
}
```

**Only `class` is read by code.** `role`, `name`, `note` and `registered_at` are for people, and
nothing branches on them — which is what makes a standby's promotion to primary a one-line edit
that reinterprets every historical file correctly, because no role was ever written into the data.

**An identity that is not listed resolves to `unknown`, and that is a rule rather than a setting.**
The one alternative anybody would ever configure is the one that must not be possible.

**N producers cost N lines and no design.** Two collectors on two servers are two entries with
`class: production`; both pass every gate. That is the payoff of an opaque, unbounded identity
beside a small closed classification.

### The attestation — a predicate, never a file list

Files written before their producer could state an identity are covered by a dated claim. It is
keyed on the archive plus the last format version written without an identity, and it closes
itself: once a producer ships the block, every new file carries an identity and none can fall
under the claim again. A list would be stale the moment another file was written.

It keys on the archive rather than on a producer name for a reason that is easy to miss: a file
without an origin block has no producer field either — that absence is the whole reason the claim
exists — so the scope has to be checkable against what such a file actually carries.

**Which is why a scope names exactly one of two keys.** The same rule, applied to two archives
that answer it differently: a tick file carries its `broker_type` and a `data_format_version`,
while a signal envelope carries neither — it carries a `pipeline_id` and calls its version
`schema_version`. A single key covered one archive and matched nothing in the other, silently,
which is the same failure the identity-key shape check exists to prevent one level up: an entry
that looks present and can never fire. A scope naming neither archive, or both, is refused when
the registry loads.

An attestation carries no evidence grade. It can only ever BE attested, and a field that accepts
one value is a field somebody can eventually use to write a lie.

**The boundary is the LAST version this side actually holds, not the last one the producer shipped
without a block.** They are usually the same number and were not here: the collector moves from
1.5.0 straight to 1.7.0, so no production file will ever carry 1.6.0 — but development files at
1.6.0 do exist, and a boundary of 1.6.0 would sweep them in and stamp them as production. A
producer's own reading of the boundary describes its archive; ours has to describe what we hold.

## Three grades, and why the middle one exists

| Grade | Means | Admissible for a parity measurement |
|---|---|---|
| `stamped` | the producer wrote its identity and we resolved it | **yes** |
| `attested` | we recorded a dated claim about files written before it could | no |
| `unknown` | nobody has said anything | no |

`production` resolved from a producer's own stamp and `production` resolved from a claim we
recorded are the same word and a different fact. Collapsing them would let a claim present itself
as a measurement, which is the property this whole contract exists to keep.

## Where each answer is recorded

```
raw file (origin block)
      │  the block travels verbatim as `source_meta_origin`
      ▼
tick importer ──► parquet metadata:  origin_instance_id · origin_class · origin_evidence
      │                              resolved ONCE, here
      ▼
tick index ─────► the same three columns, read back from the stamp
      ▼
scenario mount ─► per-scenario lists on the loaded files
      ▼
run admission ──► admitted, or the scenario is excluded
```

**Resolved once at import and never re-resolved.** A registry is a judgement that can be edited,
so a surface asking it again would report today's meaning against a file imported under the
meaning of the day it arrived. The same argument the price basis already rests on (§31c): during a
re-render, configuration describes what a render *would* produce while half the files on disk
still hold the previous answer.

**The identity travels beside the resolved class, verbatim.** A class can be re-derived later only
if the identity survived; a resolved class alone is a conclusion whose premise has been discarded.

**Never on a tick row.** A live tick has no origin — it comes from a socket, and this side is the
source. A field present in the archive and absent live is a parity break, so origin is file
metadata plus an index column and never a row.

### The signal archive records the same answer in a different place

```
archived envelope (top-level instance_id)
      ▼
signal importer ──► three parquet COLUMNS, resolved per ENVELOPE
      ▼
signal index ─────► the same three, collapsed per file
      ▼
scenario mount ───► the same per-scenario lists the ticks land in
```

Three differences from the tick path, each one following from how that archive is already built
rather than from a second opinion about provenance.

**The identity is top-level, not nested in an `origin` block.** The signal producer already
carries `data_origin` — whether the data is live or synthetic — at the top level, and an
`origin.instance_id` block beside a `data_origin` field is two things called origin with two
meanings, one line apart. Agreed with the producer rather than imposed.

**It is a row column, not file metadata.** Every provenance fact in that archive already is one:
`schema_version`, `pipeline_id`, `data_origin`. The tick side uses file metadata because §41
forbids a column repeated across fifty thousand ticks; a signal file holds orders of magnitude
fewer rows and parquet dictionary-encodes a constant column to almost nothing. Like `data_origin`,
the three stay **out** of `SIGNAL_RUNTIME_COLUMNS`, so no worker can reach them.

**It is resolved per envelope, not per file.** A producer may legitimately re-mint its identity
when an instance is cloned, and a daily bucket can straddle that moment; resolved per file, one of
the two writers would be recorded under the other's identity. The index entry is per file, so a
file whose envelopes disagree collapses to `unknown` rather than to either answer — the weakest
element governs admissibility, because part of that file was written by somebody nobody has
adjudicated.

**And the gate asks one question about both.** A run that consumed development signal data is
exactly as incomparable as one that consumed development ticks, so the per-scenario lists hold the
inputs of both archives and one rule reads them.

## The gate, and why it starts open

Two questions, two places:

| Question | Where | Refuses on |
|---|---|---|
| may these bytes enter the archive? | import | a block missing at or above its producer's boundary |
| may a run USE data of this class? | scenario admission | a class outside `admitted_origin_classes` |

`backtesting.data_validation.admitted_origin_classes` starts **open**, listing every class. That is
deliberate rather than timid: until a producer stamps an identity and the legacy archive is
attested, every file resolves to `unknown` — so a strict default would refuse every scenario on the
day it shipped, which is how a gate gets switched off permanently instead of being narrowed once.

Meanwhile the run reports how much of what it read carries no production stamp of its own — one
finding for the whole run, in the post-run validator, beside the same question for
`data_format_version`. That measurement is what has to exist before the gate is armed: it says what
a narrower list would refuse, so arming it costs a number rather than a surprise.

It is deliberately **not** a per-scenario warning. Until a producer stamps an identity, every file
is unstamped, so a per-scenario warning would fire on every scenario of every run — and a warning
that always fires teaches people to skip warnings, which costs more than the measurement is worth.
The gate decides per scenario because exclusion is per scenario; the distance to a comparable
archive is a property of the archive.

## What is deliberately not here

- **Deduplication across two collectors capturing one symbol.** The origin stamp is its
  prerequisite and cannot be added to data already on disk; the policy itself is a separate topic.
- **A backfill of the identity into old files.** A value invented for an old file is
  indistinguishable from one that was measured, which is the property this exists to protect. A
  claim may be written — but only where the record says it is a claim.
- **The signal archive.** Its `data_origin` field already separates generated from live envelopes,
  and no signal producer states an identity yet. The columns follow when one does.

**One known limit, recorded because a consumer depends on it.** Where a producer derives its
identity rather than minting one, the derivation has to distinguish the thing that WRITES, not the
thing it writes into. A fingerprint of a PostgreSQL cluster's `system_identifier` identifies the
cluster — so two instances writing to two databases inside one cluster produce the SAME identity,
and this registry cannot tell them apart. That is exactly the case a test instance beside a
production one creates. A derived identity has to be checked against the instances that will
actually exist, not against the ones that exist today.
