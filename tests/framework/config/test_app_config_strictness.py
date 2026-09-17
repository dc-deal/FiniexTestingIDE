"""
FiniexTestingIDE - App Config Strictness Tests

Guards the other half of §28's field-coverage property. The AutoTrader suite beside this one
asks whether every field a MODEL declares is reachable from JSON; this one asks the opposite
question about the shared `app_config.json`: whether a key the FILE declares and no model
knows is refused rather than dropped.

It was not. `AppConfig` and its whole tree inherited a plain `BaseModel`, whose default is
`extra='ignore'` — so a section added to the config file but never declared in the model was
allowed through, merged, and read by nobody. The failure is invisible from every side: the
file looks configured, validation passes, and the value simply never arrives. It is the exact
shape `market_config.json` was fixed for, and `StrictConfigModel` exists for it.

The models are WALKED rather than listed. A fixture naming today's models would be the
maintenance trap this test exists to close.
"""

import inspect

import pytest
from pydantic import BaseModel, ValidationError

from python.configuration.config_file_loader import ConfigFileLoader
from python.framework.types.config_types.app_config_types import AppConfig
from python.framework.utils.config_merge_utils import is_meta_key

# Nothing is exempt, and the one candidate is worth naming because it looks like it should be.
# `TradeSimulatorDefaults` is the base layer of a three-level cascade, and `account_currency` —
# a real setting that decides tick_value, so a money path — is NOT one of its fields. It is
# safe anyway: that key lives on the SCENARIO level, in a raw dict `set_scenario_account_currency`
# reads directly, which never passes through this model. This model only ever receives
# `app_config.json`'s own block. So the guard covers every model reachable from AppConfig.
_DELIBERATELY_PERMISSIVE: set = set()


def _tree(model, seen=None):
    """
    Every Pydantic model reachable from AppConfig.

    Args:
        model: The model to descend from
        seen: Models already visited, so a shared type is reported once

    Returns:
        The set of reachable models
    """
    seen = seen if seen is not None else set()
    if not (isinstance(model, type) and issubclass(model, BaseModel)) or model in seen:
        return seen
    seen.add(model)
    for field in model.model_fields.values():
        _tree(field.annotation, seen)
    return seen


def _real_config() -> dict:
    """
    The merged configuration this installation actually loads.

    Building a minimal config by hand would test a shape nothing runs; mutating the real one
    tests the file the operator edits.

    Returns:
        The merged raw config
    """
    raw, _ = ConfigFileLoader.get_config()
    return raw


class TestEveryAppConfigModelRefusesAnUnknownKey:
    """A key no model declares must fail loudly, never be dropped."""

    def test_the_whole_tree_forbids_extras(self):
        loose = sorted(
            f'{m.__name__} ({inspect.getfile(m).split("config_types/")[-1]})'
            for m in _tree(AppConfig)
            if m.model_config.get('extra') != 'forbid'
            and m.__name__ not in _DELIBERATELY_PERMISSIVE)

        assert not loose, (
            'these config models still drop an unknown key silently — a section set in '
            f'app_config.json and absent from the model would never arrive: {loose}')

    def test_the_real_config_file_still_loads(self):
        """Strictness is only worth having if the config it guards still passes it."""
        assert AppConfig(**_real_config()).version

    def test_a_misspelled_top_level_section_is_refused(self):
        raw = dict(_real_config())
        raw['backtestingg'] = raw.get('backtesting', {})

        with pytest.raises(ValidationError, match='backtestingg'):
            AppConfig(**raw)

    def test_a_misspelled_nested_key_is_refused(self):
        """The nested level is where it actually happened, so it is asserted separately."""
        raw = dict(_real_config())
        backtesting = dict(raw['backtesting'])
        backtesting['data_validation'] = {
            **backtesting.get('data_validation', {}),
            'admitted_origin_classe': ['production'],
        }
        raw['backtesting'] = backtesting

        with pytest.raises(ValidationError, match='admitted_origin_classe'):
            AppConfig(**raw)


class TestAConfigFileMayStillExplainItself:
    """§28 makes `_comment` how a config file documents itself — strictness must not break it."""

    @pytest.mark.parametrize('key', ['_comment', '_comment_admitted_origin_classes',
                                     '_comment_render_timeframes'])
    def test_a_comment_key_passes_at_every_level(self, key):
        raw = dict(_real_config())
        raw[key] = 'documentation'
        backtesting = dict(raw['backtesting'])
        backtesting[key] = 'documentation'
        backtesting['data_validation'] = {
            **backtesting.get('data_validation', {}), key: 'documentation'}
        raw['backtesting'] = backtesting

        assert AppConfig(**raw).version

    @pytest.mark.parametrize('key,meta', [
        ('_comment', True), ('_comment_why', True),
        ('comment', False), ('_commitment', False), ('_', False),
    ])
    def test_the_prefix_rule_is_what_decides(self, key, meta):
        """A prefix, because one `_comment` per section cannot explain several settings."""
        assert is_meta_key(key) is meta
