"""
`carry_over_from` — a profile's statement that it continues another profile's carry-over (#358).

It is read from the PROFILE alone. A continuation is a statement one profile makes about one
other; a default merged in from app_config would make every profile claim it, while the boot
check, which reads the raw files, would not see it — the two would disagree about who declares
what.
"""

import json
from pathlib import Path

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.types.config_types.autotrader_defaults_config_types import (
    AutotraderDefaultsConfig,
)

PROFILE_ROOT = Path(__file__).resolve().parents[3] / 'configs' / 'autotrader_profiles'
A_LOADABLE_PROFILE = PROFILE_ROOT / 'mock' / 'minimal_warmup_test.json'
_OMIT = object()


def _load(tmp_path: Path, carry_over_from=_OMIT):
    """
    Load a real profile with the declaration set, or left out.

    Args:
        tmp_path: The test's own directory
        carry_over_from: The value to write, or _OMIT to leave the key out

    Returns:
        The loaded config
    """
    raw = json.loads(A_LOADABLE_PROFILE.read_text(encoding='utf-8'))
    if carry_over_from is not _OMIT:
        raw['carry_over_from'] = carry_over_from
    path = tmp_path / 'profile_under_test.json'
    path.write_text(json.dumps(raw), encoding='utf-8')
    return load_autotrader_config(str(path))


def test_a_profile_without_one_continues_nothing(tmp_path):
    assert _load(tmp_path).carry_over_from == ''


def test_a_declared_one_is_read_as_written(tmp_path):
    assert _load(tmp_path, 'security_cert_mock_soft').carry_over_from == (
        'security_cert_mock_soft')


@pytest.mark.parametrize('value', [True, 7, ['a'], {'name': 'a'}])
def test_anything_but_a_name_is_refused_at_load(tmp_path, value):
    with pytest.raises(ValueError, match='`carry_over_from`'):
        _load(tmp_path, value)


def test_app_config_cannot_declare_it_for_every_profile():
    assert 'carry_over_from' not in AutotraderDefaultsConfig.model_fields
