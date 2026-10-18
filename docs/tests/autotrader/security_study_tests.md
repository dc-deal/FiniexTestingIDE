# Security Study Tests

`tests/autotrader/security_study/` — the security study's phase machine, the way a phase is declared,
and the order sizes its rules resolve to (#358). Offline: no broker, no network, no order placed.

The security study is a release gate that runs on a funded account, and most of its orders are
meant to be REFUSED — so a phase is judged by the outcome it names, and a wrong judgement would
pass a stage whose protective rule did not fire. Every verdict the machine can reach is reachable
here first, the failing ones included, which a real run should never produce.

Not here: the certificate that judges the stages together, which is the
[Security Study Certificate Tests](../framework/security_cert_certificate_tests.md); the mock
stages end to end, which is `test_security_study_stages.py` in the
[AutoTrader Integration Tests](integration_tests.md); running it with real money, which is the
[Security Study Guide](../security_cert/security_cert_guide.md).

Run: `python -m pytest tests/autotrader/security_study/ -v`
Launch entry: `🧩 Pytest: Security Study (All)`

| File | Covers |
|---|---|
| `test_security_phase_machine.py` | a submit judged by the outcome it names — a fill, a denial with its reason, a suppression — and failed with both outcomes in its reason otherwise; a stage abandoned when the account holds the base, naming the currency and what to do, every later phase skipped so nothing after it costs money; a wait; and the session's end concluding what the hard stop overtook: the buy judged on what was observed, the wait passed on an EMERGENCY end and failed on any other |
| `test_security_phase_spec.py` | a phase read from its profile, and the declarations refused at load because they would certify nothing: a submit with no expected outcome, a denial with no reason, a reason on an outcome that is not a denial, a submit with no side |
| `test_security_study_lots.py` | each lot rule resolved to what it promises — the smallest order, half of it, more than the quote balance buys (capped at the venue's maximum), more base than is held — every size on the venue's grid of the minimum plus whole steps |

## Why the session's end concludes phases

The hard stop ends the session itself, before the study sees another pass: the buy that tripped
it is left unjudged and the phase waiting for the stop is never entered. The session's end — whose
event arrives after every buffered fill — concludes both. The wait passes only on an EMERGENCY
end, which is what the safety layer's stop requests; the certificate holds that verdict against the
breaker's own record besides, so a session that ended for another reason cannot pass as a stop.
