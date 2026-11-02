"""
The check at a session's start, and the settings it reads (#630).

A stop whose settings cannot keep what they promise is said before the first order and never
refuses one. The findings are read by their id, the way a consumer filters them; the one sentence
a person acts on is asserted for the figures it has to carry.
"""

import json
from pathlib import Path
from typing import List

import pytest
from pydantic import ValidationError

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.configuration.composite_definition_manager import CompositeDefinitionManager
from python.framework.autotrader.run_record.rendered_profile_builder import (
    render_autotrader_profile,
)
from python.framework.types.composite_types import CompositeDocument, RunAxes
from python.framework.types.config_types.autotrader_defaults_config_types import ShutdownDefaults
from python.framework.types.run_kind_types import OrdersTo, TicksFrom
from python.framework.types.validation_types import Severity, ValidationDomain
from python.framework.utils.composite_key_builder import build_composite_key
from python.framework.validators.stop_budget_validator import check_stop_budget
from python.framework.validators.validation_check_catalog import VALIDATION_CHECKS_BY_ID

_ROOT = Path(__file__).resolve().parents[3]
_PROFILE = 'configs/autotrader_profiles/mock/session_stop_test.json'
_COVERAGE_PROFILE = _ROOT / 'tests' / 'fixtures' / 'autotrader_profiles' / 'loader_coverage' / 'base_profile.json'

_WAITS_BEYOND_KILL = 'shutdown_waits_beyond_kill'
_KILL_UNDECLARED = 'shutdown_kill_undeclared'
_ORDER_WAIT_BELOW_POLL = 'shutdown_order_wait_below_poll'

_POLL_MS = 5000
_SCOPE = 'my_profile'
# Settings of the test's own, spending 20 s at most: the waits 5 s, the order wait 10 s, the reserve 5 s
_WAITS_OF_TWENTY_SECONDS = {
    'order_wait_s': 10.0, 'tick_thread_join_s': 1.0, 'signal_transport_join_s': 1.0,
    'display_join_s': 1.0, 'request_worker_join_s': 1.0, 'alert_flush_s': 1.0,
    'reserve_for_records_s': 5.0,
}
# The fields of the block that are no wait of the stop's own threads
_NOT_A_BOUNDED_WAIT = ('order_wait_s', 'supervisor_kill_after_s', 'reserve_for_records_s')


def _checks(
    settings: ShutdownDefaults,
    poll_interval_ms: int = _POLL_MS,
    live_session: bool = True,
    attended: bool = True,
) -> List[str]:
    """
    The ids of the findings one start would leave.

    Args:
        settings: The session's stop settings
        poll_interval_ms: The broker's poll interval
        live_session: Whether the session runs the live adapter
        attended: Whether a human declared they are watching

    Returns:
        The check ids, in the order they were found
    """
    findings = check_stop_budget(
        settings, poll_interval_ms, live_session=live_session, attended=attended, scope=_SCOPE)
    return [finding.check for finding in findings]


class TestTheWaitsAgainstTheKill:

    def test_the_defaults_fit_a_docker_stop_of_eighty_seconds(self):
        assert _checks(ShutdownDefaults(supervisor_kill_after_s=80.0)) == []

    def test_waits_that_fill_the_kill_exactly_still_fit(self):
        settings = ShutdownDefaults(**_WAITS_OF_TWENTY_SECONDS, supervisor_kill_after_s=20.0)
        assert _checks(settings) == []

    def test_waits_beyond_the_kill_are_named_with_their_figures(self):
        settings = ShutdownDefaults(**_WAITS_OF_TWENTY_SECONDS, supervisor_kill_after_s=19.5)
        findings = check_stop_budget(settings, _POLL_MS, True, True, _SCOPE)
        assert [finding.check for finding in findings] == [_WAITS_BEYOND_KILL]
        assert '= 20 s' in findings[0].message
        assert '19.5 s' in findings[0].message

    def test_a_declared_kill_is_held_against_the_waits_in_a_mock_session_too(self):
        settings = ShutdownDefaults(**_WAITS_OF_TWENTY_SECONDS, supervisor_kill_after_s=19.5)
        assert _checks(settings, live_session=False) == [_WAITS_BEYOND_KILL]


class TestAnUnattendedSession:

    def test_it_is_told_when_it_declares_no_kill(self):
        assert _checks(ShutdownDefaults(), attended=False) == [_KILL_UNDECLARED]

    def test_a_declared_kill_answers_it(self):
        assert _checks(ShutdownDefaults(supervisor_kill_after_s=80.0), attended=False) == []

    def test_an_attended_session_has_nothing_to_declare(self):
        assert _checks(ShutdownDefaults(), attended=True) == []

    def test_a_mock_session_has_no_supervisor_to_declare(self):
        assert _checks(ShutdownDefaults(), live_session=False, attended=False) == []


class TestTheOrderWaitAgainstThePoll:

    def test_a_wait_below_the_poll_and_a_second_can_miss_the_second_read(self):
        assert _checks(ShutdownDefaults(order_wait_s=5.9)) == [_ORDER_WAIT_BELOW_POLL]

    def test_a_wait_of_the_poll_and_a_second_does_not(self):
        assert _checks(ShutdownDefaults(order_wait_s=6.0)) == []

    def test_the_broker_sets_the_poll(self):
        assert _checks(ShutdownDefaults(order_wait_s=5.9), poll_interval_ms=1000) == []

    def test_a_mock_venue_has_no_poll_to_miss(self):
        assert _checks(ShutdownDefaults(order_wait_s=0.0), live_session=False) == []


class TestWhatAFindingIs:

    @pytest.mark.parametrize('settings, attended', [
        (ShutdownDefaults(supervisor_kill_after_s=60.0), True),
        (ShutdownDefaults(), False),
        (ShutdownDefaults(order_wait_s=1.0), True),
    ])
    def test_a_warning_on_the_session_with_a_declared_id(self, settings, attended):
        findings = check_stop_budget(settings, _POLL_MS, True, attended, _SCOPE)
        assert findings
        for finding in findings:
            assert finding.severity is Severity.WARNING
            assert finding.domain is ValidationDomain.SETUP
            assert finding.scope == _SCOPE
            assert finding.check in VALIDATION_CHECKS_BY_ID


class TestTheSettings:

    def test_the_sum_of_the_waits_counts_every_join_and_the_flush(self):
        waits = [name for name in ShutdownDefaults.model_fields if name not in _NOT_A_BOUNDED_WAIT]
        assert waits
        # A power of two per wait: a sum that misses one or counts one twice cannot come out right
        values = {name: float(2 ** position) for position, name in enumerate(waits)}
        others = {name: 1000.0 for name in _NOT_A_BOUNDED_WAIT}
        assert ShutdownDefaults(**values, **others).bounded_waits_s() == sum(values.values())

    def test_the_defaults_are_mirrored_in_the_config_file(self):
        shipped = json.loads((_ROOT / 'configs' / 'app_config.json').read_text(encoding='utf-8'))
        assert shipped['autotrader']['shutdown'] == ShutdownDefaults().model_dump()

    def test_a_misspelled_key_is_refused_at_load(self, tmp_path):
        profile = json.loads(_COVERAGE_PROFILE.read_text(encoding='utf-8'))
        profile['shutdown'] = {'order_wait': 5.0}
        path = tmp_path / 'my_profile.json'
        path.write_text(json.dumps(profile), encoding='utf-8')
        with pytest.raises(ValueError, match='order_wait'):
            load_autotrader_config(str(path))

    @pytest.mark.parametrize('field', [
        name for name in ShutdownDefaults.model_fields if name != 'supervisor_kill_after_s'])
    def test_a_wait_below_zero_is_refused(self, field):
        with pytest.raises(ValueError, match=field):
            ShutdownDefaults(**{field: -1.0})

    def test_a_wait_of_zero_means_no_wait(self):
        assert ShutdownDefaults(order_wait_s=0.0).order_wait_s == 0.0

    def test_a_kill_at_no_time_is_refused(self):
        with pytest.raises(ValueError, match='supervisor_kill_after_s'):
            ShutdownDefaults(supervisor_kill_after_s=0.0)

    def test_a_rendered_session_keeps_its_key_whatever_its_stop_settings_say(self):
        CompositeDefinitionManager.reload()
        rules = CompositeDefinitionManager(
            config_path='configs/composite_definition.json').get_rules(CompositeDocument.SESSION)
        axes = RunAxes('autotrader', TicksFrom.ARCHIVE, OrdersTo.SIMULATED)
        config = load_autotrader_config(_PROFILE)
        shipped = render_autotrader_profile(config)
        config.shutdown = ShutdownDefaults(order_wait_s=5.0, supervisor_kill_after_s=80.0)
        retimed = render_autotrader_profile(config)
        assert retimed['blocks']['shutdown'] != shipped['blocks']['shutdown']

        def key(document):
            return build_composite_key(
                document, CompositeDocument.SESSION, rules, 'definition', 'USD', axes)
        assert key(retimed) == key(shipped)


class TestTheSignalStreamJoin:

    def test_zero_is_refused_at_load(self):
        """Not waited for at all, the stream's thread would be reported abandoned on every stop."""
        with pytest.raises(ValidationError, match='signal_transport_join_s'):
            ShutdownDefaults(signal_transport_join_s=0.0)

    def test_the_other_joins_may_be_zero(self):
        settings = ShutdownDefaults(
            tick_thread_join_s=0.0, display_join_s=0.0, request_worker_join_s=0.0)
        assert settings.bounded_waits_s() == pytest.approx(
            settings.signal_transport_join_s + settings.alert_flush_s)
