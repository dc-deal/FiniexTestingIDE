"""
FiniexTestingIDE - AutoTrader Startup Guards (#503, finding 205)

`setup_pipeline` is the one path every AutoTrader session is obliged to walk, and until now no
test imported it. Its abort conditions — the ones that decide whether a session is allowed
to start at all — were therefore unverified, including the three that predate this file.

That matters more here than in most places. §35 puts pre-run problems in the ABORT class
precisely because a session that starts wrong cannot be corrected later: it trades, or it
refuses to trade, for as long as nobody is watching. A guard that silently stopped working
would look exactly like a healthy start.

These tests do not build the whole pipeline. They drive real profiles through the real
loader into the real `setup_pipeline` and assert what it REFUSES — which is reachable
early, before the heavy construction, and is the half that carries the consequence.
"""

import json
from datetime import datetime, timezone

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.configuration.market_config_manager import MarketConfigManager
from python.framework.autotrader.autotrader_startup import setup_pipeline
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.decision_logic.core.cautious_macd import CautiousMacd
from python.framework.logging.scenario_logger import ScenarioLogger

_MOCK_PROFILE = 'configs/autotrader_profiles/mock/aggressive_trend_mock.json'
_CAUTIOUS_MACD_PROFILE = 'configs/autotrader_profiles/mock/cautious_macd_mock.json'


def _profile(tmp_path, **overrides) -> str:
    """
    A shipped mock profile with the given top-level keys replaced.

    Built from a real checked-in profile rather than a minimal dict on purpose: a guard
    that only ever sees a hand-made config is not proven against the shape the operator
    actually writes.

    Args:
        tmp_path: pytest temporary directory
        overrides: top-level profile keys to set or replace

    Returns:
        Path to the written profile
    """
    with open(_MOCK_PROFILE, encoding='utf-8') as handle:
        profile = json.load(handle)
    profile.update(overrides)
    path = tmp_path / 'startup_guard_profile.json'
    path.write_text(json.dumps(profile), encoding='utf-8')
    return str(path)


def _logger(tmp_path) -> ScenarioLogger:
    """
    A scenario logger writing into the test's own directory.

    Args:
        tmp_path: pytest temporary directory

    Returns:
        The logger
    """
    return ScenarioLogger(
        'startup_guards', 'startup_guards',
        datetime(2026, 1, 1, tzinfo=timezone.utc), 'run_startup_guards',
        log_root_override=tmp_path)


# What turns the shipped MOCK profile into a live-adapter one: the adapter alone is not enough,
# because a live adapter on replayed ticks — or with a replayed data window — is refused at load.
_LIVE_WIRING = {'adapter_type': 'live', 'tick_source': {'type': 'kraken'}, 'scenario_settings': None}


def _setup_error(tmp_path, **overrides) -> str:
    """
    Run setup_pipeline over the profile and return whatever it refused with.

    Args:
        tmp_path: pytest temporary directory
        overrides: top-level profile keys to set or replace

    Returns:
        The exception message, or '' when nothing was raised
    """
    config = load_autotrader_config(_profile(tmp_path, **overrides))
    try:
        setup_pipeline(config, _logger(tmp_path), 'run_startup_guards')
    except Exception as error:  # noqa: BLE001 — the refusal itself is under test
        return str(error)
    return ''


class TestAVenueThatCannotHoldAProtectiveOrder:
    """
    #503: a profile-wide opt-in is a STARTUP problem, not a per-order one.

    Left to the submit path it would reject every protected entry, one at a time, for the
    whole session — the bot would run, trade nothing, and each rejection would read as an
    isolated incident rather than as one wrong line in the profile.

    But the refusal belongs to a LIVE-ADAPTER session only. A mock session builds a
    MockBrokerAdapter whatever its profile's broker_type says, so it can never carry a
    protective order — refusing there would make an opted-in profile UNREHEARSABLE, which
    is the same mistake the simulation deliberately avoids by accepting the flag and
    changing nothing.
    """

    def test_a_live_session_refuses_to_start(self, tmp_path):
        message = _setup_error(
            tmp_path, broker_type='mt5', symbol='EURUSD', **_LIVE_WIRING,
            execution={'venue_held_protection': True})

        assert 'venue_held_protection' in message, (
            f'A live profile the venue cannot serve must not start: {message!r}')

    def test_the_refusal_names_both_sides(self, tmp_path):
        """The profile switch AND the broker — an operator has to know which to change."""
        message = _setup_error(
            tmp_path, broker_type='mt5', symbol='EURUSD', **_LIVE_WIRING,
            execution={'venue_held_protection': True})

        assert 'execution.venue_held_protection' in message
        assert 'send_order(venue_held_protection=True)' in message, (
            'The per-order route is refused the same way and the message should say so')

    def test_a_mock_rehearsal_is_not_stopped(self, tmp_path):
        """
        The rehearsal must still run. A profile is written for live and tried against
        recorded data first; a gate that refused it would remove the only cheap way to
        exercise everything ELSE in that profile.
        """
        message = _setup_error(
            tmp_path, broker_type='mt5', symbol='EURUSD',
            execution={'venue_held_protection': True})

        assert 'venue_held_protection' not in message, (
            f'A mock session has no venue and nothing to refuse: {message!r}')

    def test_the_switch_is_off_by_default(self, tmp_path):
        """
        A profile that never mentions it starts against any venue.

        The default matters as much as the guard: opting in changes what the bot does with
        real money, so a session must never acquire the behaviour by accident.
        """
        message = _setup_error(
            tmp_path, broker_type='mt5', symbol='EURUSD', **_LIVE_WIRING)

        assert 'venue_held_protection' not in message


class TestARestLadderThatNeverGivesUp:
    """
    #362: every read and write a live session sends goes through its broker's REST ladder, and a
    budget of 0 retries for ever — the session-end broker-truth read and the boot reads would wait
    through an outage for as long as it lasts. The model's default is 0, so a broker without a
    transport block of its own inherits it: MT5 here.
    """

    def test_a_live_session_refuses_to_start(self, tmp_path):
        message = _setup_error(tmp_path, broker_type='mt5', symbol='EURUSD', **_LIVE_WIRING)

        assert 'attempt_budget' in message and "'mt5'" in message, (
            f'A live venue that would be retried for ever must not start: {message!r}')

    def test_a_mock_rehearsal_is_not_stopped(self, tmp_path):
        """A mock session reaches no venue, so it has no request to hang on."""
        message = _setup_error(tmp_path, broker_type='mt5', symbol='EURUSD')

        assert 'attempt_budget' not in message, message

    def test_the_shipped_live_venue_gives_up(self):
        """The one live venue today passes the check — the guard is not a stop for Kraken."""
        connection = MarketConfigManager().get_broker_entry(
            'kraken_spot').broker_transport.connection

        assert connection.attempt_budget > 0


class TestTheGuardsThatPredateThisFile:
    """
    Opening the startup path means the older refusals get covered too. They were written
    with care and never once executed by a test.
    """

    def test_a_profile_that_resolves_no_balances_refuses(self, tmp_path):
        config_path = _profile(tmp_path, broker_type='mt5', symbol='EURUSD')
        with open(config_path, encoding='utf-8') as handle:
            profile = json.load(handle)
        profile['scenario_settings']['balances'] = {}
        with open(config_path, 'w', encoding='utf-8') as handle:
            json.dump(profile, handle)

        config = load_autotrader_config(config_path)
        with pytest.raises(ValueError, match='resolved no balances'):
            setup_pipeline(config, _logger(tmp_path), 'run_startup_guards')

    def test_a_resting_logic_without_a_cold_start_hook_refuses(self, tmp_path, monkeypatch):
        """
        #493's guard, proven at its WIRING rather than at its rule.

        The rule itself has its own suite (`tests/autotrader/cold_start/`); what was never
        executed is that `setup_pipeline` asks it at all. That distinction decides a
        thirty-day run: a bot whose logic can leave an order resting at a venue must answer
        for finding one there after a 03:00 restart, and the boot is the only place that
        refusal can still be made cheaply.

        The hook is put back to the base class's — which is exactly what "did not override
        it" means — rather than writing a fake logic, so the profile, the loader and the
        factory stay the real ones.
        """
        monkeypatch.setattr(
            CautiousMacd, 'on_cold_start', AbstractDecisionLogic.on_cold_start)

        config = load_autotrader_config(_CAUTIOUS_MACD_PROFILE)
        with pytest.raises(ValueError, match='on_cold_start'):
            setup_pipeline(config, _logger(tmp_path), 'run_startup_guards')

    def test_and_the_same_profile_passes_the_cold_start_guard(self, tmp_path):
        """
        The other direction, so the refusal above cannot be read as "this profile is
        broken". `CautiousMacd` declares STOP and overrides the hook — the guard lets it pass.

        It does not START in a mock session today, and the test says why instead of tolerating
        any error: the profile rests a STOP_LIMIT entry and the mock adapter carries market
        orders only, so the refusal that follows is the ORDER-TYPE one and names the mock. Once
        the mock rests orders (#556) that refusal is gone, and this becomes the plain "it starts" it
        was meant to be.
        """
        config = load_autotrader_config(_CAUTIOUS_MACD_PROFILE)
        with pytest.raises(ValueError) as refused:
            setup_pipeline(config, _logger(tmp_path), 'run_startup_guards')
        assert 'on_cold_start' not in str(refused.value), (
            f'A logic that DOES override the hook must not be refused for it: {refused.value}')
        assert 'The mock adapter (standing in for' in str(refused.value)
