# Signal Import Tests

**Suite:** `tests/data/signal_import/` · **Mark:** `data` · **Issue:** #429

Validates the signal data source pipeline — JSONL import → columnar parquet → index → projected
reader — and, the key guarantee, **bit-identical parity with the v0 JSONL path** on the consumed
fields.

## What it covers

| Area | Checks |
|---|---|
| Import / explode | row counts (one row per `(collected_msc, symbol)` + one envelope sentinel per envelope), parquet schema + dtypes, lean projection (heavy provenance `sources`/`metadata`/`errors` dropped; `basis` + `prompt_id`/`prompt_hash` persisted) |
| Index | sources + symbols, whole-file coverage per symbol (incl. symbols absent in some envelopes), range resolution, unknown symbol → empty |
| Reader projection | one snapshot per envelope for the projected symbol; audit-only `sources` dropped from the runtime series |
| v0 parity | `SignalDataProvider` over the raw JSONL vs. over the parquet resolve identically across the range, for a symbol present in every envelope AND one absent in `partial`/`error` envelopes (defensive HOLD) |
| Import guards | mixed `pipeline_id` in one file → `SignalSchemaError` |
| Finished archive | imported JSONL moves to `data/finished/signals/` with its structure intact; no `finished_dir` → the file stays; a re-run without `--override` finds nothing and reports no error; `--override` reads the archive back; a re-exported day supersedes its archived copy; a failed import is not moved |

### Why the archive tests matter

Two of them guard decisions that are easy to undo by accident. `--override` reading the
finished archive is what keeps the flag meaningful once the inbox is empty — without it,
"rebuild everything" would silently rebuild nothing. And the move is deliberately **not**
rebuilt from the resolved `pipeline_id` but kept relative to the root the file came from:
a file in a folder that disagrees with its own `pipeline_id` is an anomaly (it has
happened), and normalizing it on the way out would hide it.

### test_signal_stream_validation.py (#141 Part 2a)

The importer's guard on the producer's stream identity. The split is deliberate:

| Condition | Action | Why |
|---|---|---|
| contiguous `seq` per epoch | import | nothing to say |
| clean epoch bump (`seq` restarts) | import | a reset legitimately renumbers — not a hole |
| no identity at all (pre-stream archive) | import | *unverifiable*, a distinct state from verified-contiguous, and not a defect |
| missing `seq` | import **and report** | the file is incomplete, not wrong; refusing would discard the envelopes we do have |
| `stream_epoch` steps backwards | **refuse** | the series was rewound |
| `seq` steps backwards inside one epoch | **refuse** | the epoch was reissued for a second series — two series would merge under one key |

The last row is the one worth remembering: a reissued epoch does not show up as a backwards
*epoch*, it shows up as a backwards *`seq`* within the same epoch. Only the second check catches it.

The same file also pins **`trigger_reason` across the promotion boundary**: the producer moved it out
of `metadata` to the top level, so both locations must land in one column — and an absent value must
stay `''` (unknown) rather than being guessed as `'scheduled'`, which would render a boot pass as a
grid point. This one fails silently if it regresses: an unread trigger looks like the pre-contract
era rather than like a bug.

### Schema major gate

Both contract eras load through one reader: `1.x` (the original) and `2.x` (the stream contract).
The producer spent a **major** version on an otherwise additive field group for one reason —
`trigger_reason` left `metadata` — precisely because this reader gates on the major, so a minor
would not have fired the branch the fallback lives behind. An unknown major is refused rather than
guessed at: it may carry a changed `result` structure.

---

## Fixture

`tests/fixtures/signals/signal_import_sample.jsonl` — 6 envelopes (`pipeline_id = test_sentiment`,
symbols BTCUSD + ETHUSD) covering `success`, `partial` (one symbol absent) and `error` (empty
result). The `imported_signals` module fixture imports it into a temp parquet tree + index once.

## Run

```bash
pytest tests/data/signal_import/ -v
```

Related: the SIGNAL worker capability itself is covered by `tests/framework/signal_workers/`
([Signal Worker Tests](../framework/signal_workers_tests.md)); the data source is documented in
[Signal Data Source](../../data_pipeline/signal_data_source.md).
