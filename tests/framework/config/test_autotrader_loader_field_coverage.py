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
close, so it must not reproduce it.
"""

import dataclasses
import json
import typing
from pathlib import Path

import pytest
from pydantic import BaseModel

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig

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


def _blocks() -> list:
    """
    Every config block of AutoTraderConfig with its probe-able fields.

    Returns:
        (section name, model, {field: probe value}) per block — the section name in JSON
        equals the attribute name on AutoTraderConfig, which is the loader's own convention
    """
    blocks = []
    hints = typing.get_type_hints(AutoTraderConfig)
    for field in dataclasses.fields(AutoTraderConfig):
        model = hints[field.name]
        if not _is_config_block(model):
            continue
        probes = {}
        field_hints = typing.get_type_hints(model)
        for name, default in _model_defaults(model).items():
            annotation = field_hints[name]
            if _is_config_block(annotation):
                # One level of nesting (execution.performance_tracking) — the nested block
                # travels as a dict and Pydantic builds it.
                nested = {
                    n: _probe_value(typing.get_type_hints(annotation)[n], d)
                    for n, d in _model_defaults(annotation).items()
                }
                probes[name] = {n: v for n, v in nested.items() if v is not None}
                continue
            probe = _probe_value(annotation, default)
            if probe is not None:
                probes[name] = probe
        blocks.append((field.name, model, probes))
    return blocks


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
        blocks = _blocks()
        assert blocks, 'no config blocks discovered — the derivation itself is broken'

        config = _load_with({name: probes for name, _, probes in blocks}, tmp_path)

        ignored = []
        for name, _, probes in blocks:
            block = getattr(config, name)
            for field, expected in probes.items():
                actual = getattr(block, field)
                if isinstance(expected, dict):
                    for nested_field, nested_expected in expected.items():
                        if getattr(actual, nested_field) != nested_expected:
                            ignored.append(f'{name}.{field}.{nested_field}')
                    continue
                if actual != expected:
                    ignored.append(f'{name}.{field}')

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
        The four blocks that auto-disable for a mock adapter keep doing so.

        The coverage test above sets `enabled` explicitly, which the loader honours by
        design (the provenance check asks whether the PROFILE set it). This is the other
        half: a profile that says nothing still gets the mock auto-disable.
        """
        config = _load_with({}, tmp_path)

        assert config.adapter_type == 'mock'
        assert not config.drift_audit.enabled
        assert not config.reconciliation.enabled
        assert not config.api_monitor.enabled
        assert not config.state_persistence.enabled
