"""
Fixtures for the activity list suite (#603).
"""

from pathlib import Path

import pytest

from python.configuration.app_config_manager import AppConfigManager


@pytest.fixture
def registry_dir(tmp_path, monkeypatch) -> Path:
    """
    A registry directory of the test's own, so no test sees another's entries.

    Returns:
        The directory every registry and reader in the test resolves to
    """
    directory = tmp_path / 'activities'
    monkeypatch.setattr(AppConfigManager, 'get_activity_registry_path', lambda self: directory)
    return directory
