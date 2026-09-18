"""
A live bot's sessions in sequence, and the identity that travels between them (#497).

This is the only test in the project that runs the deployment mechanism end to end. Everything
else pins a piece: the loader refuses an undeclared profile, `_resolve_deployment` picks the
identity, the history reader groups the rows. None of them prove the identity actually TRAVELS
— out of one session's carry-over, through a process that has ended, into the next session's
ledger row. That journey crosses two stores and a shutdown, and when this was written no live
ledger row had ever carried a deployment at all.

**Why the sessions have to be armed, and why that is safe here.** A dry run writes no
carry-over. The rule is older than this feature and it is a good one: a dry run sent no order to
any venue, so its session key is not one this bot "sent orders under", and appending it would
let a restart loop evict the key that owns a real resting order (#355). The consequence for a
deployment is that a dry run cannot hand one on either — and a mock session is ALWAYS a dry run,
`_is_dry_run` decides that on the adapter type before it looks at anything else. So a mock
profile cannot reach this path on its own, and the test resolves the session as armed instead.
What that changes is exactly one thing: whether the carry-over may be written. The tick loop
stays on its mock path either way (it takes `adapter_type == 'mock'` as its own answer), and the
mock adapter has no transport to arm — it mutates its own dictionaries and cannot reach a
network. `configs/market_config.json` is never read differently.

The chain runs ONCE for the whole module: a session costs about eleven seconds, nearly all of it
warmup, and the four properties below are four questions about one sequence rather than four
sequences. The carry-over goes to the test's own directory (§34); the ledger is already
redirected for the whole suite by `tests/conftest.py`.
"""

from pathlib import Path

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.reporting.console.deployment_history_summary import (
    build_deployment_histories,
)
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from tests.shared.fixture_helpers import remove_run_dir

PROFILE = 'configs/autotrader_profiles/backtesting/deployment_continuity_test.json'


def run_session(carry_over_dir: Path, armed: bool = True, **flags) -> AutotraderMain:
    """
    Run one full session of the deployment profile and hand back the finished object.

    Args:
        carry_over_dir: Where this session reads and writes its cold-start document —
            shared across a chain, which is what makes the sessions a sequence
        armed: Whether the session resolves as a non-dry run, which is what allows the
            carry-over to be written at all
        flags: `one_off` / `new_deployment`, the two narrowing CLI flags

    Returns:
        The finished session — its resolved identity, run id and run directory
    """
    config = load_autotrader_config(PROFILE)
    config.cold_start.path = str(carry_over_dir)
    trader = AutotraderMain(config, **flags)
    if armed:
        trader._is_dry_run = lambda: False
    trader.run()
    return trader


@pytest.fixture(scope='module')
def chain(tmp_path_factory):
    """
    One carry-over directory, four sessions through it, in this order:

        1. `first`     — ordinary: nothing to inherit, so it mints
        2. `second`    — ordinary: inherits what `first` left
        3. `detached`  — `--one-off`: stands alone, and must not END the deployment
        4. `fresh`     — `--new-deployment`: begins a second history

    A chain rather than four independent cases, because the interesting properties are all
    about what the PREVIOUS session left behind.
    """
    carry_over = tmp_path_factory.mktemp('cold_start_state')
    sessions = {
        'first': run_session(carry_over),
        'second': run_session(carry_over),
        'detached': run_session(carry_over, one_off=True),
        'fresh': run_session(carry_over, new_deployment=True),
    }
    yield sessions
    for session in sessions.values():
        remove_run_dir(session._run_dir)


@pytest.fixture(scope='module')
def rows(chain):
    """
    Each session's own ledger row, typed.

    The ledger is shared with every other test in the run, so the rows are picked by run id
    rather than read wholesale — anything else would read somebody else's session.
    """
    by_run = {row.run_id: row for row in
              RunResultsLedger(Path(AppConfigManager().get_run_ledger_path())).read_rows()}
    picked = {}
    for name, session in chain.items():
        assert session._run_id in by_run, (
            f'no ledger row for the {name} session ({session._run_id})')
        picked[name] = by_run[session._run_id]
    return picked


class TestTheIdentityTravels:
    """Mint, carry, inherit — the journey the whole feature rests on."""

    def test_the_first_session_mints_one(self, chain):
        assert chain['first']._deployment_id

    def test_the_second_session_inherits_it(self, chain):
        assert chain['second']._deployment_id == chain['first']._deployment_id, (
            'the carry-over did not reach the successor')

    def test_both_ledger_rows_name_it(self, chain, rows):
        """
        The identity has to reach the RECORD, not only the running process. The ledger row
        is what survives the session, and a deployment nothing recorded is not one.
        """
        assert rows['first'].deployment_id == chain['first']._deployment_id
        assert rows['second'].deployment_id == chain['first']._deployment_id

    def test_the_rows_read_back_as_one_history(self, chain, rows):
        history = build_deployment_histories(
            [rows['first'], rows['second']])[chain['first']._deployment_id]
        assert [s.run_id for s in history] == [chain['first']._run_id, chain['second']._run_id]
        assert history[0].gap_hours is None
        assert history[1].gap_hours is not None


class TestNothingChangedSoNothingIsReported:
    """
    The direction that fails silently.

    A fingerprint that moves on its own would mark every restart as a parameter change — and
    the marks would then mean nothing on the one restart where a parameter really did move.
    """

    def test_the_two_fingerprints_hold_across_a_restart(self, rows):
        assert rows['first'].profile_hash
        assert rows['first'].profile_hash == rows['second'].profile_hash
        assert rows['first'].param_hash == rows['second'].param_hash

    def test_the_history_marks_no_change(self, chain, rows):
        history = build_deployment_histories(
            [rows['first'], rows['second']])[chain['first']._deployment_id]
        assert history[1].strategy_changed is False
        assert history[1].operation_changed is False


class TestTheFlagsNarrowARealSession:
    """What the command line does to a profile that declares a deployment."""

    def test_a_one_off_start_records_no_deployment(self, chain, rows):
        assert chain['detached']._deployment_id == ''
        assert rows['detached'].deployment_id == ''
        assert build_deployment_histories([rows['detached']]) == {}

    def test_a_one_off_start_does_not_end_the_deployment(self, chain):
        """
        A debugging start must stay OUT of the history without destroying it. The proof is
        the session after it: `fresh` was asked for a new deployment, so what it must not
        do is mint one because the carry-over was wiped — it must mint one although the
        carry-over still holds the old identity.
        """
        assert chain['fresh']._deployment_id != chain['detached']._deployment_id

    def test_new_deployment_begins_a_second_history(self, chain, rows):
        assert chain['fresh']._deployment_id
        assert chain['fresh']._deployment_id != chain['first']._deployment_id
        assert len(build_deployment_histories(
            [rows['first'], rows['second'], rows['fresh']])) == 2


class TestADryRunHandsNothingOn:
    """
    The limit of the mechanism, written down here rather than discovered on a live machine.

    A dry run writes no carry-over at all, so its successor finds nothing and mints its own
    identity. Two dry-run sessions therefore never form a history — which is the older rule
    (#355) doing its job and not a defect here, but it IS the first thing an operator would
    try the feature with. The end-user guide says the same sentence.
    """

    def test_a_dry_run_session_leaves_no_carry_over(self, tmp_path):
        session = run_session(tmp_path / 'cold_start_state', armed=False)
        try:
            assert session._deployment_id, 'it still resolves an identity for its own row'
            assert not list((tmp_path / 'cold_start_state').glob('*.json')), (
                'a dry run wrote a carry-over — it sent no order to any venue')
        finally:
            remove_run_dir(session._run_dir)
