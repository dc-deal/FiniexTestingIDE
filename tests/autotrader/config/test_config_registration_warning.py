"""
A session whose configuration the run-config store cannot record says so in its log.

Both registrations stay non-fatal: a store that cannot be written costs the run its join keys,
never its trading. What they no longer do is fail silently — an empty `config_id` or
`rendered_config_id` read afterwards cannot tell a store that failed from one never asked, so
the session's log carries the cause at the moment it happened.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

from python.framework.autotrader.startup.autotrader_startup import (
    _register_profile_config,
    _register_rendered_profile,
)
from python.framework.exceptions.file_lock_errors import FileLockHeldError
from python.framework.store.abstract_store_index import SESSION_INDEX_LOCK_WAIT_S

_STORE = 'python.framework.autotrader.startup.autotrader_startup.RunConfigStore'
_PROFILE = Path('configs/autotrader_profiles/mock/mock_session_test.json')


def _failing_store() -> MagicMock:
    """
    A store class whose every instance fails to write.

    Returns:
        The patched class
    """
    store_class = MagicMock()
    store_class.return_value.register.side_effect = OSError('No space left on device')
    store_class.return_value.register_document.side_effect = OSError('No space left on device')
    return store_class


def test_a_profile_the_store_cannot_register_is_reported_with_its_cause():
    session_logger = MagicMock()
    with patch(_STORE, _failing_store()):
        config_id = _register_profile_config(_PROFILE, session_logger)

    assert config_id == ''
    session_logger.warning.assert_called_once()
    message = session_logger.warning.call_args.args[0]
    assert 'mock_session_test.json' in message
    assert 'OSError: No space left on device' in message
    assert 'config_id' in message


def test_a_rendered_configuration_the_store_cannot_freeze_is_reported_with_its_cause():
    session_logger = MagicMock()
    with patch(_STORE, _failing_store()):
        rendered_config_id = _register_rendered_profile({'strategy_config': {}}, session_logger)

    assert rendered_config_id == ''
    session_logger.warning.assert_called_once()
    message = session_logger.warning.call_args.args[0]
    assert 'OSError: No space left on device' in message
    assert 'rendered_config_id' in message


def test_a_registration_that_succeeds_says_nothing():
    session_logger = MagicMock()
    store_class = MagicMock()
    store_class.return_value.register.return_value.config_id = 'abc123'
    store_class.return_value.register_document.return_value.config_id = 'def456'
    with patch(_STORE, store_class):
        assert _register_profile_config(_PROFILE, session_logger) == 'abc123'
        assert _register_rendered_profile({'strategy_config': {}}, session_logger) == 'def456'

    session_logger.warning.assert_not_called()


def test_nothing_to_register_is_not_a_failure():
    session_logger = MagicMock()
    assert _register_profile_config(None, session_logger) == ''
    assert _register_rendered_profile(None, session_logger) == ''
    session_logger.warning.assert_not_called()


def test_a_held_run_config_index_costs_the_registration_never_the_start():
    """The store's index lock is waited for briefly at a start (#650); a holder past that is a warning."""
    session_logger = MagicMock()
    store_class = MagicMock()
    held = FileLockHeldError(Path('run_configs/run_configs_index.lock'),
                             note='pid 4711 · store_cli.py rebuild run_configs', waited_s=SESSION_INDEX_LOCK_WAIT_S)
    store_class.return_value.register.side_effect = held
    store_class.return_value.register_document.side_effect = held
    with patch(_STORE, store_class):
        assert _register_profile_config(_PROFILE, session_logger) == ''
        assert _register_rendered_profile({'strategy_config': {}}, session_logger) == ''

    store_class.return_value.set_index_lock_wait_s.assert_called_with(SESSION_INDEX_LOCK_WAIT_S)
    assert session_logger.warning.call_count == 2
    message = session_logger.warning.call_args.args[0]
    assert 'FileLockHeldError' in message
    assert 'pid 4711' in message
    assert 'trading is not affected' in message
