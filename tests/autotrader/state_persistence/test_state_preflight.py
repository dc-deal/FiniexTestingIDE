"""
Algo State Pre-Flight — snapshot serializability check (#354)

The first member of the algo pre-flight check family: assert that an algo opting
into persistence emits a JSON-serializable snapshot. No-op for algos that opt out.
Raises StatePersistenceError naming the offending key on failure.
"""

from datetime import datetime, timezone

import pytest

from python.framework.exceptions.persistence_errors import StatePersistenceError
from python.framework.validators.algo_state_preflight import validate_state_snapshot_serializable


class TestPreFlight:
    """validate_state_snapshot_serializable — opt-in gating + serializability."""

    def test_optout_is_noop_even_with_bad_snapshot(self, make_stub_logic):
        """An algo that does not opt in is never checked (subsystem bypassed)."""
        logic = make_stub_logic(uses=False, snapshot={'bad': {datetime.now(timezone.utc)}})
        validate_state_snapshot_serializable(logic)  # must not raise

    def test_serializable_snapshot_passes(self, make_stub_logic):
        logic = make_stub_logic(uses=True, snapshot={'count': 3, 'flag': True, 'list': [1, 2]})
        validate_state_snapshot_serializable(logic)  # must not raise

    def test_nonserializable_raises_naming_key(self, make_stub_logic):
        """A non-JSON value fails loudly and names the offending key + logic."""
        logic = make_stub_logic(
            name='my_bot',
            uses=True,
            snapshot={'ok': 1, 'when': datetime.now(timezone.utc)},
        )
        with pytest.raises(StatePersistenceError) as exc:
            validate_state_snapshot_serializable(logic)
        assert 'when' in str(exc.value)
        assert 'my_bot' in str(exc.value)
