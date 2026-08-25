# Static Analysis Tests

`tests/framework/static_analysis/test_undefined_names.py` — the undefined-name gate.
Runs in every suite.

**Total Tests:** 3

Static analysis in this project has **two tiers**, and only the first one is a gate.

## Tier 1 — the gate: no undefined names

A name that is used but never imported is *syntactically valid*. `ast.parse` accepts it, the
module imports fine, and only executing that exact line reveals it — so the bug hides
precisely in the branches nothing runs yet. pyflakes answers the question statically, over
every file, including those branches.

| Test | Description |
|------|-------------|
| `test_the_scan_had_input` | The scanned trees exist and contain files — a scan over a moved or empty directory passes cleanly and proves nothing |
| `test_every_name_resolves` | No pyflakes finding of class `undefined name` / `may be undefined` in `python/` or `tests/`. Failure names file, line and symbol |
| `test_the_backlog_is_visible` | Counts the hygiene categories (unused imports, placeholder-free f-strings, unused locals) without asserting on them — a place where the number is stated |

A failure is almost always a missing import in a path no test executes. **Fix the import,
never silence the test.**

## Tier 2 — the backlog: measured, not gated

Unused imports (§7), placeholder-free f-strings (§5) and dead code (§19) are real findings
with a large pre-existing backlog. They are cleaned as a unit is touched and swept
project-wide at release — deliberately **not** a daily gate, because a gate that is red on
day one gets skipped, and a skipped gate stops protecting the undefined-name check beside it.

| Tool | Config | Covers | When |
|---|---|---|---|
| `pyflakes` | — | undefined names (gate) + the hygiene count | every suite |
| `ruff` | `ruff.toml` | unused imports, empty f-strings, redefinitions, unused locals; import order and quote style at release | release sweep |
| `vulture` | `vulture.toml` | dead code **across** module boundaries — what pyflakes cannot see | release sweep |

### Why vulture is not redundant

pyflakes reports disuse *within* one module. A public getter that no caller anywhere
invokes is used nowhere and reported by nobody — that gap is vulture's entire job.

**Never run either tool bare.** `ruff`'s default quote style is double, the opposite of §5
(~6900 findings pointing the wrong way). `vulture` without its config reports 353 findings
including every pytest fixture and FastAPI route.

### Reading a vulture run

Order matters: run it **after** `ruff check --fix`, or the unused imports ruff is about to
delete are reported a second time and bury everything else. Measured 2026-08-24 after the
first full sweep (292 findings under `vulture.toml`):

| Bucket | Count | What to do with it |
|---|---|---|
| `python/framework/types/` | 126 | Mostly dataclass / Pydantic **fields**. Serialization reads them by name at dump time, so vulture cannot see the use — a judgement pass, not a delete list. §19 still applies: a config key with no consumer is dead |
| unused imports | 48 | Mostly the 21 test modules whose imports ARE pytest's collection mechanism (see below) — those stay |
| everything else | 118 | The actionable core — methods, attributes, classes, functions, properties |

### The test tree is not ruff's to clean

21 test modules exist to re-export shared test classes so pytest collects them under that
suite (`from tests.shared.shared_x import TestY`). Those imports look unused and are load
bearing: removing them deleted 226 tests on 2026-08-24 while the suite stayed green, because
the tests were gone rather than failing. `ruff.toml` ignores F401 across `tests/**` for
exactly this reason, and any cleanup of test imports must protect anything from
`tests.shared`, from a `conftest`, or named `Test*`.

Three blind spots are stated in `vulture.toml` and repeated here because deleting on a false
positive is the expensive mistake: **`user_algos/` is not scanned** (separate gitignored
workspace — a framework method only a private algo calls reads as dead), **serialization hides
reads**, and — the one that bites hardest — **configuration selects by string**. A decision
logic is a path in a profile, a worker is a `USER/name` type, an adapter is a `broker_type`:
none of that is an import, so no config-selected class is visible to the tool. Grep the JSON
under `configs/` and `tests/fixtures/` before deleting a class.

## Related

- `ruff.toml`, `vulture.toml` — tool configuration, both at project root
- [test_taxonomy.md](../test_taxonomy.md) — where this suite sits in the test map
