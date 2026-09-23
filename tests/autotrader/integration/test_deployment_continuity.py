"""
A live bot's sessions in sequence, and the identity that travels between them (#497).

This is the only test in the project that runs the deployment mechanism end to end. Everything
else pins a piece: the loader refuses an undeclared profile, `_resolve_deployment` picks the
identity, the history reader groups the rows. None of them prove the identity actually TRAVELS
— out of one session's carry-over, through a process that has ended, into the next session's
ledger row. That journey crosses two stores and a shutdown, and when this was written no live
ledger row had ever carried a deployment at all.

**These are ordinary mock sessions — nothing is armed and nothing is patched.** That became
possible when the carry-over's write gate was split by what the payload CLAIMS: a dry run may
not write the session key or the open position book, because those describe orders a venue does
not hold, but it DOES write the deployment identity, the risk baseline and the drawdown curve,
which are numbers this process computed and are true whether or not the venue was real. Before
that split a mock profile could not reach this path at all — `_is_dry_run` answers True on the
adapter type before it looks at anything else — and this test had to resolve the session as
armed to get there. It no longer does, and that is the point: the chain below is exactly what an
operator can run by hand.

The chain runs ONCE for the whole module: a session costs about eleven seconds, nearly all of it
warmup, and the four properties below are four questions about one sequence rather than four
sequences. The carry-over goes to the test's own directory (§34); the ledger is already
redirected for the whole suite by `tests/conftest.py`.
"""

import json
from pathlib import Path

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.exceptions.live_execution_errors import OneOffInsideDeploymentError
from python.framework.reporting.builders.deployment_history_builder import (
    build_deployment_histories,
)
from python.framework.reporting.store.run_results_ledger import RunResultsLedger
from tests.shared.fixture_helpers import remove_run_dir

PROFILE = 'configs/autotrader_profiles/backtesting/deployment_continuity_test.json'


def run_session(carry_over_dir: Path, **flags) -> AutotraderMain:
    """
    Run one full session of the deployment profile and hand back the finished object.

    Args:
        carry_over_dir: Where this session reads and writes its cold-start document —
            shared across a chain, which is what makes the sessions a sequence
        flags: `one_off` / `new_deployment`, the two narrowing CLI flags

    Returns:
        The finished session — its resolved identity, run id and run directory
    """
    config = load_autotrader_config(PROFILE)
    config.cold_start.path = str(carry_over_dir)
    trader = AutotraderMain(config, **flags)
    trader.run()
    return trader


@pytest.fixture(scope='module')
def chain(tmp_path_factory):
    """
    One carry-over directory, four sessions through it, in the order an operator actually
    works:

        1. `probe`   — `--one-off` on a profile that declares `continuous` but has never run.
                       The probe day. Nobody starts an algo for thirty days and then leaves
        2. `first`   — ordinary: nothing to inherit, so it mints
        3. `second`  — ordinary: inherits what `first` left
        4. `fresh`   — `--new-deployment`: begins a second history

    A chain rather than four independent cases, because the interesting properties are all
    about what the PREVIOUS session left behind — and because the ORDER is the contract:
    `--one-off` is refused once the deployment exists, which `TestTheFlagsNarrowARealSession`
    pins separately.
    """
    carry_over = tmp_path_factory.mktemp('cold_start_state')
    sessions = {
        'probe': run_session(carry_over, one_off=True),
        'first': run_session(carry_over),
        'second': run_session(carry_over),
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

    def test_the_probe_records_no_deployment(self, chain, rows):
        """
        A probe day before the bot is deployed: it runs, it produces a ledger row, and that
        row joins no history — because at that moment there is no history to join.
        """
        assert chain['probe']._deployment_id == ''
        assert rows['probe'].deployment_id == ''
        assert build_deployment_histories([rows['probe']]) == {}

    def test_the_probe_does_not_become_the_deployment(self, chain):
        """The session after it mints its own identity rather than adopting the probe's."""
        assert chain['first']._deployment_id
        assert chain['first']._deployment_id != chain['probe']._deployment_id

    def test_one_off_is_refused_once_the_deployment_exists(self, chain, tmp_path_factory):
        """
        The order is the contract. Past the first continuous start the same flag would let a
        session trade this account while leaving no mark on the history its own drawdown
        keeps running inside — so it is refused, with the three ways out named.
        """
        carry_over = tmp_path_factory.mktemp('refusal_carry_over')
        deployed = run_session(carry_over)
        try:
            assert deployed._deployment_id
            with pytest.raises(OneOffInsideDeploymentError) as caught:
                run_session(carry_over, one_off=True)
            assert deployed._deployment_id in str(caught.value)
        finally:
            remove_run_dir(deployed._run_dir)

    def test_new_deployment_begins_a_second_history(self, chain, rows):
        assert chain['fresh']._deployment_id
        assert chain['fresh']._deployment_id != chain['first']._deployment_id
        assert len(build_deployment_histories(
            [rows['first'], rows['second'], rows['fresh']])) == 2


class TestWhatADryRunMayNotHandOn:
    """
    The other half of the split, checked on a real session rather than on a double.

    These sessions ARE dry runs — every mock session is. What they write is the deployment
    identity and the risk records; what they must never write is a claim about a venue that
    holds nothing: the session key this bot supposedly sent orders under, and the open
    position book. A successor inheriting either would trade beside orders that do not exist.
    """

    def test_the_document_carries_the_identity_but_no_venue_claim(self, chain):
        carry_over = Path(chain['first']._config.cold_start.path)
        documents = sorted(carry_over.glob('*.json'))
        assert documents, 'the chain wrote no carry-over at all'

        # The LAST session's identity: the carry-over is keyed by the bot and overwrites, so
        # the document always describes the most recent start — here `fresh`, which was asked
        # for a new deployment and therefore wrote a different one than `first` minted.
        stored = json.loads(documents[0].read_text())['snapshot']
        assert stored['deployment_id'] == chain['fresh']._deployment_id
        assert stored['deployment_id'] != chain['first']._deployment_id
        assert stored['session_keys'] == [], (
            'a dry run recorded a key it never sent orders under')
        assert stored['open_positions'] == [], (
            'the successor would inherit a book the venue does not hold')


def _period_numbers(run_id: str) -> list:
    """
    The booking-period numbers one session left in the ledger.

    Args:
        run_id: The session whose periods to read

    Returns:
        Its `segment_no` values, ascending
    """
    ledger = RunResultsLedger(Path(AppConfigManager().get_run_ledger_path()))
    return sorted(row.segment_no for row in ledger.read_rows(run_id=run_id))


class TestThePeriodNumberingContinues:
    """
    A deployment's booking periods are numbered ACROSS its restarts, and no two of them may
    share a number (#537).

    #537 asked for exactly this test — "segment numbering across a restart" — and it was never
    written. What shipped instead persisted the carry-over floor BEFORE the last period was
    sealed, so every successor reopened at the number its predecessor had just closed, and a
    session booking a single period never advanced the floor at all. Measured 2026-09-23 on the
    demo deployment: five consecutive sessions each booked a period numbered 6.

    The cases below are that defect seen from both ends — the floor that is written, and the
    number the successor then hands out. Neither can be seen from a unit test of the recorder,
    which counts correctly in isolation; only the shutdown ORDER is wrong.
    """

    def test_each_session_books_at_least_one_period(self, chain):
        """Guards the cases below: over zero periods they would both pass vacuously."""
        for name in ('first', 'second'):
            assert _period_numbers(chain[name]._run_id), (
                f'the {name} session booked no period, so numbering cannot be checked')

    def test_the_successor_opens_above_its_predecessor(self, chain):
        first = _period_numbers(chain['first']._run_id)
        second = _period_numbers(chain['second']._run_id)
        assert max(first) < min(second), (
            f'the successor reopened at a number already in the books: '
            f'{first} then {second}')

    def test_no_two_periods_of_one_deployment_share_a_number(self, chain):
        # The consequence a consumer meets: two periods with one identity. The row key
        # carries `run_id` as well, so nothing is LOST — but the number stops being one.
        numbers = _period_numbers(chain['first']._run_id) + _period_numbers(
            chain['second']._run_id)
        assert len(numbers) == len(set(numbers)), f'a number was handed out twice: {numbers}'

    def test_the_carried_floor_is_the_number_actually_reached(self, chain):
        """
        The defect at its source. The floor on disk must be what the session REACHED, not what
        it had reached at the moment the carry-over happened to be written.
        """
        documents = sorted(Path(chain['fresh']._config.cold_start.path).glob('*.json'))
        stored = json.loads(documents[0].read_text())['snapshot']
        assert stored['highest_segment_no'] == max(_period_numbers(chain['fresh']._run_id)), (
            'the stored floor is not the last number this session sealed')


    def test_the_carried_drawdown_is_the_one_the_session_reported(self, chain):
        """
        The same ordering, one field over, and without a floor to blunt it: the store
        OVERWRITES the drawdown record. A successor inheriting a shallower curve than the
        session reported continues a thirty-day drawdown from a peak that was never real —
        the restart drift #497 removed.

        **This is an INVARIANT, not a reproduction — measured 2026-09-23 it passes with the
        closing write removed.** A mock session samples equity on every tick and ends when the
        tick source runs out, so its last sample holds nothing new. The divergence needs a fill
        resolving on a HEARTBEAT, the one pass that moves balances and never samples, with the
        session ending on the very next line (`is_session_end_requested`). That path is real and
        reachable live; it is not reachable from a replay. So this case guards the contract
        rather than demonstrating the defect, and it says so instead of implying otherwise.
        """
        session = chain['fresh']
        documents = sorted(Path(session._config.cold_start.path).glob('*.json'))
        stored = json.loads(documents[0].read_text())['snapshot']['account_drawdown']
        reported = session._executor.portfolio.get_account_drawdown_carry_over()
        if reported is None:
            pytest.skip('this session measured no drawdown, so there is nothing to compare')
        assert stored is not None, 'the successor inherits no drawdown at all'
        assert stored['max_drawdown'] == pytest.approx(reported.max_drawdown), (
            'the stored curve is not the one the session reported')
        assert stored['max_equity'] == pytest.approx(reported.max_equity)
