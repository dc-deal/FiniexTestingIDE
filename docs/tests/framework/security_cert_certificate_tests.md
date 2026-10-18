# Security Study Certificate Tests

`tests/framework/security_cert_certificate/` — the certificate that judges the security study's
stages together, and the catalog that says how it stands behind each rule (#358). Offline: the
stage runs are written to a temporary directory from the REAL stage profiles.

The certificate is what a release is held to, so a study that should fail has to fail — and fail
exactly the check that names why. A test takes a passing study and changes one thing.

Not here: the phase machine, which is the [Security Study Tests](../autotrader/security_study_tests.md);
the validation of a committed certificate, which runs at release time from `tests/security_cert/`
([Security Study Guide](../security_cert/security_cert_guide.md)).

Run: `python -m pytest tests/framework/security_cert_certificate/ -v`
Launch entry: `🧩 Pytest: Security Study Certificate (All)`

| File | Covers |
|---|---|
| `conftest.py` | the stage runs a passing study leaves on disk — capture, run header and the breaker's record per stage — built from the real stage profiles, so a profile the analyzer cannot follow fails here first |
| `test_security_cert_analyzer.py` | a passing study passes with every live rule held; a missing or out-of-order stage, two commits, uncommitted code and simulated orders fail the `study:*` check that names them; a phase verdict the order stream contradicts fails its phase; a stop-awaiting phase fails when the stop never came, when the breaker does not confirm it, and when it has no result; a breaker record that does not say what the stage provoked fails its `safety:*` check; anything left at the venue fails `study:flat_at_end` |
| `test_security_rule_catalog.py` | the catalog complete in both directions: every rule declared, every live rule naming a stage and a check the analyzer computes — and a phase its stage's profile declares — every offline rule naming a test that exists, every margin rule naming the issue that carries it |
