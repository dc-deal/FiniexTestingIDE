"""
FiniexTestingIDE - AutoTrader Loader Field Coverage Tests

Guards ONE property of `load_autotrader_config`: every field of every config block is
reachable from JSON. A field that exists in the model, is mirrored in `app_config.json`
(§28), is allowed through `check_unknown_keys` and is read at runtime — but never
transferred by the loader — is invisible in the worst way: the profile setting it passes
validation and is then ignored.

Two fields were in exactly that state (measured 2026-09-03, both since their own feature
shipped): `cold_start.book_drift_interval_ticks` and `clipping_monitor.warn_above_ratio`.
Both happened to agree with their model default, so nothing looked wrong from outside. It
is the same shape as the `dry_run` near-miss (#304): declared, documented, parsed, read by
nothing.

The field list is DERIVED from the models, never written down here. A static fixture would
have to be extended for every new field — which is the maintenance trap this test exists to
close, so it must not reproduce it. The derivation goes as deep as the blocks do — a nested
block, a dict of blocks — and a field whose type it cannot probe fails the guard, so a new type
extends the probe rather than slipping past it.
"""

import dataclasses
import enum
import json
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.connection_types import GiveUpAction

_BASE_PROFILE = (
    Path(__file__).resolve().parents[2]
    / 'fixtures' / 'autotrader_profiles' / 'loader_coverage' / 'base_profile.json'
)

# The two names the property is asserted for by hand as well. A generic failure says
# "something is no longer reachable"; these say which — and these two really happened.
_HISTORICAL_LOSSES = [
    ('cold_start', 'book_drift_interval_ticks'),
    ('clipping_monitor', 'warn_above_ratio'),
]

# Values a block refuses by design — operator_alerts.telegram.connection refuses `abort`, which
# would end the alert thread. A probe needs any value but the default, so it takes another.
_REFUSED_PROBES = {GiveUpAction.ABORT}

# The key a dict of blocks is probed with — one the defaults do not hold
_PROBE_ENTRY = 'probe_entry'


def _is_config_block(annotation) -> bool:
    """
    Whether a field of AutoTraderConfig is a config block the loader builds from JSON.

    Args:
        annotation: The resolved type annotation

    Returns:
        True for a Pydantic model or a dataclass block; False for scalars and for
        Optional[...] fields (scenario_settings has its own lane and may legitimately be None)
    """
    if not isinstance(annotation, type):
        return False
    return issubclass(annotation, BaseModel) or dataclasses.is_dataclass(annotation)


def _model_defaults(model: type) -> dict:
    """
    The declared default of every field of one config block.

    Args:
        model: A Pydantic model or dataclass config block

    Returns:
        field name → declared default
    """
    if issubclass(model, BaseModel):
        return {name: info.default for name, info in model.model_fields.items()}
    return {f.name: f.default for f in dataclasses.fields(model)}


def _probe_value(annotation, default):
    """
    A value that differs from the declared default, so an ignored field is visible.

    Args:
        annotation: The field's resolved type annotation
        default: Its declared default

    Returns:
        The probe value, or None when the type offers no second value to choose
    """
    if typing.get_origin(annotation) is typing.Literal:
        alternatives = [a for a in typing.get_args(annotation) if a != default]
        return alternatives[0] if alternatives else None
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        # JSON carries the value; the loaded block holds the member
        alternatives = [m for m in annotation if m != default and m not in _REFUSED_PROBES]
        return alternatives[0].value if alternatives else None
    if annotation is bool:
        return not default
    if annotation is int:
        return int(default) + 7
    if annotation is float:
        # Ratios and rates live in (0, 1]; halving stays inside every such range, and
        # adding would risk a value a future field constraint rejects.
        return round(default / 2 + 0.01, 4) if default <= 1.0 else default + 0.5
    if annotation is str:
        return f'{default}_probe'
    return None


def _probes(model: type, current, path: str, unprobed: list) -> dict:
    """
    A probe value for every field of one block, nested blocks and dicts of blocks included.

    Args:
        model: The block's class
        current: The block as the defaults build it — a nested block's default comes from its
            parent (the Telegram ladder waits 2 s where the ladder's own default is 1 s)
        path: The block's dotted path, naming a field that cannot be probed
        unprobed: Collects the fields no probe type covers

    Returns:
        field name → probe value; a nested dict for a nested block or a dict of blocks
    """
    probes = {}
    hints = typing.get_type_hints(model)
    for name in _model_defaults(model):
        annotation = hints[name]
        value = getattr(current, name)
        if _is_config_block(annotation):
            # The nested block travels as a dict and Pydantic builds it
            probes[name] = _probes(annotation, value, f'{path}.{name}', unprobed)
            continue
        if typing.get_origin(annotation) is dict and _is_config_block(
                typing.get_args(annotation)[1]):
            # A dict of blocks (telegram.endpoints): a new key, every field of it probed
            entry = typing.get_args(annotation)[1]
            probes[name] = {_PROBE_ENTRY: _probes(entry, entry(), f'{path}.{name}', unprobed)}
            continue
        probe = _probe_value(annotation, value)
        if probe is None:
            unprobed.append(f'{path}.{name}')
            continue
        probes[name] = probe
    return probes


def _blocks() -> typing.Tuple[list, list]:
    """
    Every config block of AutoTraderConfig with its probe values.

    Returns:
        (blocks, unprobed): (section name, model, {field: probe value}) per block — the section
        name in JSON equals the attribute name on AutoTraderConfig, which is the loader's own
        convention — and the dotted path of every field no probe type covers
    """
    defaults = AutoTraderConfig()
    blocks, unprobed = [], []
    hints = typing.get_type_hints(AutoTraderConfig)
    for field in dataclasses.fields(AutoTraderConfig):
        model = hints[field.name]
        if not _is_config_block(model):
            continue
        probes = _probes(model, getattr(defaults, field.name), field.name, unprobed)
        blocks.append((field.name, model, probes))
    return blocks, unprobed


def _not_arrived(actual, expected, path: str) -> list:
    """
    The dotted paths whose probe value did not reach the loaded configuration.

    Args:
        actual: What the loader built — a block, a dict of blocks, or a value
        expected: The probe — a dict for a block, else the JSON value
        path: Where in the profile the probe was set

    Returns:
        The paths that were ignored
    """
    if isinstance(expected, dict):
        missing = []
        for key, nested in expected.items():
            if isinstance(actual, dict):
                if key not in actual:
                    missing.append(f'{path}.{key}')
                    continue
                child = actual[key]
            else:
                child = getattr(actual, key)
            missing.extend(_not_arrived(child, nested, f'{path}.{key}'))
        return missing
    if isinstance(actual, enum.Enum):
        actual = actual.value
    return [] if actual == expected else [path]


def _load_with(sections: dict, tmp_path: Path) -> AutoTraderConfig:
    """
    Load the base profile with the given sections overlaid.

    Args:
        sections: section name → the block's raw values
        tmp_path: pytest temp directory the probe profile is written to

    Returns:
        The loaded configuration
    """
    profile = json.loads(_BASE_PROFILE.read_text(encoding='utf-8'))
    profile.update(sections)
    probe = tmp_path / 'probe_profile.json'
    probe.write_text(json.dumps(profile, indent=2), encoding='utf-8')
    return load_autotrader_config(str(probe))


class TestAutotraderLoaderFieldCoverage:
    """Every config-block field set in a profile must reach the loaded object."""

    def test_every_field_of_every_block_is_reachable_from_json(self, tmp_path):
        """A profile value differing from the default arrives — for every field of every block."""
        blocks, unprobed = _blocks()
        assert blocks, 'no config blocks discovered — the derivation itself is broken'
        assert not unprobed, (
            f'no probe value for these fields — extend _probe_value for their type, or the '
            f'guard is blind to them: {unprobed}')

        config = _load_with({name: probes for name, _, probes in blocks}, tmp_path)

        ignored = []
        for name, _, probes in blocks:
            ignored.extend(_not_arrived(getattr(config, name), probes, name))

        assert not ignored, (
            'the loader ignored these profile values — they are declared in the model, '
            f'allowed by check_unknown_keys and read at runtime: {ignored}'
        )

    @pytest.mark.parametrize('section,field', _HISTORICAL_LOSSES)
    def test_the_two_fields_that_were_actually_lost(self, section, field, tmp_path):
        """Named regression: both were silently ignored until 2026-09-03."""
        block_model = typing.get_type_hints(AutoTraderConfig)[section]
        annotation = typing.get_type_hints(block_model)[field]
        expected = _probe_value(annotation, _model_defaults(block_model)[field])

        config = _load_with({section: {field: expected}}, tmp_path)

        assert getattr(getattr(config, section), field) == expected

    def test_the_mock_auto_disable_still_wins_when_the_profile_stays_silent(self, tmp_path):
        """
        The blocks that auto-disable for a mock adapter keep doing so.

        The coverage test above sets `enabled` explicitly, which the loader honours by
        design (the provenance check asks whether the PROFILE set it). This is the other
        half: a profile that says nothing still gets the mock auto-disable. The operator
        alerts auto-disable too, and are held where it shows: their defaults are off, so a
        silent profile proves nothing here — `test_operator_alerts_loading.py` switches the
        defaults on first.
        """
        config = _load_with({}, tmp_path)

        assert config.adapter_type == 'mock'
        assert not config.drift_audit.enabled
        assert not config.reconciliation.enabled
        assert not config.api_monitor.enabled
        assert not config.state_persistence.enabled
