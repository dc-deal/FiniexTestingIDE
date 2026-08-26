# Release Certificates

**Four artifacts prove a release. They share one identity and one rule.**

Each certificate answers a different question, but all four must first answer the same one:
*what am I certifying, and from what code and environment?* That half is built once, in
[`certificate_identity_builder.py`](../../python/framework/reporting/certificates/certificate_identity_builder.py),
and spread into every certificate body.

---

## The four

| Certificate | Proves | Costs | Producer |
|---|---|---|---|
| **Benchmark** | Throughput has not regressed on registered hardware | nothing | `tests/simulation/benchmark/conftest.py` |
| **Live Adapter** | The broker adapter really places, modifies, cancels and fills orders | ~$0.01 in fees | `tests/live_adapters/conftest.py` |
| **Field Study** | The full live execution stack works on a funded account | ~$0.08–0.20 | `certificates/field_study_certificate.py` |
| **Signal Feed** | The producer contract holds against the running production journal | nothing (3 free GETs) | `certificates/signal_feed_certificate.py` |

Each has a validation test that reads the committed artifact without re-running anything:
`test_benchmark_certificate.py`, `test_live_adapter_certificate.py`,
`test_field_study_certificate.py`, `test_signal_feed_certificate.py`. All four suites are
excluded from the daily runner, so those validators run at release time via the checklist.

---

## The shared identity

`CertificateIdentity` ([`certificate_types.py`](../../python/framework/types/certificate_types.py))
carries what every certificate needs:

```json
{
  "record_kind": "certificate",
  "release_version": "1.4.0",          // DECLARED on the command line
  "app_version": "1.4.0",              // MEASURED from configs/app_config.json
  "timestamp": "...", "valid_until": "...",
  "git_commit": "37469ab", "git_branch": "dev-v-1-4",
  "git_dirty": false, "uncommitted_count": 0,
  "comment": null,
  "isolation_active": true,
  "workspace_overrides": {"files_present": ["app_config.json"], "unnamed_files": 0, "applied": false}
}
```

Two guards come with it, and both exempt `dev` because a rehearsal declares nothing:

- **`version_mismatch()`** — the declared release must equal what the tree says. Without it a
  certificate can name a release it was never taken from.
- **`dirty_tree_warning()`** — a declared release must not come from uncommitted work. The
  certificate records a commit; on a dirty tree that commit does not contain the code that
  produced the artifact. **Commit before taking any certificate.**

`isolation_active` and `workspace_overrides` describe the *environment*, which means the same
thing for all four. What was *exercised* — a scenario set, a broker, a profile, a producer —
stays with the producer that knows it.

---

## The rule: record what was observed, never re-read a declaration

This is the failure the identity exists to prevent, and every certificate has hit some version
of it:

- The benchmark recorded only the declared version, so an artifact could name a release taken
  from a different tree.
- The live-adapter certificate re-read `configs/broker_settings/kraken_spot.json` at write time
  and published `dry_run: true`, while its two decisive tests set `dry_run = False` on their own
  adapter and placed real orders. The artifact understated exactly what it was taken to prove.

So: a value reaches a certificate from the thing that used it. The adapter fixtures call
`record_observed_adapter()` at construction; the benchmark reads the *merged* app config, never
the base file; a contract check records **both** halves (`expected` and `effective`) so a
certificate that never ran the comparison cannot be mistaken for one that passed it.

Shared mechanism:
[`certificate_config_utils.py`](../../python/framework/reporting/certificates/certificate_config_utils.py)
— `effective_config_value()` and `compare_config_contract()`.

---

## Privacy: certificates are committed

Every certificate lands in the public repository, so anything it records is published.
`workspace_overrides` therefore carries **names and a count only** — never key paths, never
values — and only names that already exist in `configs/`. A file with no committed counterpart
is counted, not named, which makes the listing structurally incapable of disclosing what the
private workspace holds. Each validation test pins this as a shape assertion, because the
failure mode is a well-meant extension that adds "just the keys" to be more informative.

---

## Taking them

Order matters. Commit first — the dirty-tree guard will otherwise fail every declared
certificate — then bump the version, then take them:

```bash
# 1. benchmark (idle machine, nothing else running, one run per sitting)
pytest tests/simulation/benchmark/test_throughput_regression.py -v --release-version X.Y.Z
pytest tests/simulation/benchmark/test_benchmark_certificate.py -v

# 2. live adapters — PLACES REAL ORDERS (~$0.01 fees, ~$10 briefly reserved)
pytest tests/live_adapters/ -v -m live_adapter --release-version X.Y.Z
pytest tests/live_adapters/test_live_adapter_certificate.py -v

# 3. signal feed — needs the production endpoint, so isolation must be OFF
FINIEX_CONFIG_ISOLATION=0 pytest tests/live_signal_feed/ -v -m live_signal_feed --release-version X.Y.Z
pytest tests/live_signal_feed/test_signal_feed_certificate.py -v

# 4. field study — REAL MONEY on a funded account, operator only
#    (launch.json → 🧪 AutoTrader: Field Study, then generate + validate)
python python/cli/field_study_certificate_cli.py generate --latest --release-version X.Y.Z
pytest tests/live_field_study/test_field_study_certificate.py -v
```

Commit the generated artifacts from each suite's `reports/` directory.

**Changing the identity invalidates all four**, because every artifact carries it. That is the
cost of the shared shape, and it is the right cost: four certificates that disagree about what
they are describing is the state this replaced.
