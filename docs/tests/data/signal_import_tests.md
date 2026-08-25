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

From the producer's #65 note onward the rule is stated on their side too — **MINOR for an additive
field, MAJOR for a breaking one** — so pinning the major is the supported way to stay readable while
the shape grows. The supported set now lives in `signal_data_types.py` and is shared with the live
transport, which gates on it as well: two copies would have let the archive path and the live path
disagree about what we can read.

### test_signal_frame_sample.py

The producer's committed stream-frame sample parsed through the **production model**, not by eye.

It exists because reading it by eye already cost us once: reissue 5 carried
`breaking_episode_start: false` on 2026-08-21, three days before the field went live, while our
declaration typed it as a timestamp. Every live envelope was then rejected and the rejection was
misfiled as the producer's outage. Nothing had ever run the sample through the reader.

Pinned: every `signal` frame validates as a `SignalSnapshot` (with `collected_msc` supplied the way
the transport supplies it — it is absent on the wire by contract); both episode fields keep their
shape, the flag as a `bool` and the id as a `str`; and a populated id carries more colons than its
three segments, which is the documentation-by-assertion of why the contract calls it **opaque**.

A reissued sample therefore checks itself. The producer's reissue 6 will carry an opener, a
continuation and a hold-band pass — the three shapes a consumer can get wrong.

**The opacity rule is pinned apart from the sample, and the reason is a mistake worth keeping
visible.** The first version asserted it by looping over the sample's populated ids — of which
reissue 5 has none, so the loop body ran zero times and the test passed while proving nothing. It now
**skips** with a reason when the sample carries no id, and `TestIdOpacity` pins the rule
unconditionally against the two forms the producer published:

| Form | Example | Why both |
|---|---|---|
| production | `forex_macro_sentiment:US Dollar Canadian Dollar USD/CAD Bank of Canada BOC:…` | the episode key is the retrieval query — free text with spaces and a slash |
| mock | `crypto_sentiment_mock:BTC:…` | the mock keys on the base currency: same contract, no spaces, no slash |

Pinned: splitting on `:` yields more parts than the contract's three segments (both forms); the
production form is not path-safe while the mock form is; and the production form is more than twice
the mock's length and past 64 characters. That last pair exists so nobody calibrates escaping or
column width on the mock and calls it covered — the narrow form is the easy case.

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
