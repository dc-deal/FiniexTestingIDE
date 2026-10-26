"""
FiniexTestingIDE - Carry-Over Configuration Check (#626)

A session that takes over what the venue still holds — our resting orders, the position book —
finishes a trade an earlier session opened. When it runs another composite than that session, the
trade ends under a configuration that did not open it, and its P&L is the result of neither. This
holds the starting session's configuration against the one its carry-over's claims about the venue
were made under, both projected onto the composite key under the definition the start runs with:
a definition changed in between is no change of what ran, and a neutral value is none either.

Whether the start then refuses is the cold start's call, because only it knows what is still open.
This unit compares, and words what the start says about it.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.profile_value_origin import value_origins
from python.configuration.composite_definition_manager import CompositeDefinitionManager
from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.autotrader_types.cold_start_types import (
    ConfigurationComparison,
    ConfigurationVerdict,
    CurrentConfiguration,
)
from python.framework.types.composite_types import (
    CompositeDocument,
    CompositeKey,
    CompositePart,
    ConfigChange,
    RunAxes,
    ValueOrigin,
)
from python.framework.types.config_types.composite_definition_config_types import CompositeTier
from python.framework.types.persistence_types import ColdStartPayload, ConfigurationCarryOver
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
)
from python.framework.utils.composite_key_builder import (
    build_composite_key,
    config_changes,
    document_leaves,
)

# Stable ids of the two findings a start can leave on its record
_COMPOSITE_CHANGE_ACCEPTED_CHECK = 'composite_change_accepted'
_CONFIGURATION_NOT_COMPARED_CHECK = 'carried_configuration_not_compared'
# Scope of a session-global finding
_RUN_SCOPE = 'run'
# A value longer than this is shortened in a message: a list or a path reads as noise in a table
_MAX_SHOWN = 60
# How much of the new composite key a refusal prints for `--accept-composite-change`, and the
# shortest prefix the flag accepts — enough that two composites of one bot never share it
_ACCEPT_KEY_LENGTH = 8
# What accepting a change costs beyond the trade that crosses it — said wherever a change is accepted
_TWO_SERIES = ('From here the deployment is two series: figures that need one long unbroken series '
               '— Sharpe, profit factor, the drawdown of one composite, a backtest of the same '
               'period — start over, or have to be rebuilt piece by piece.')

# How each origin reads beside a changed value
_ORIGIN_WORDS = {
    ValueOrigin.PROFILE: 'your profile',
    ValueOrigin.APP_CONFIG: 'app_config.json as shipped',
    ValueOrigin.APP_CONFIG_WORKSPACE: 'app_config.json in your workspace',
    ValueOrigin.MARKET_CONFIG: 'market_config.json as shipped',
    ValueOrigin.MARKET_CONFIG_WORKSPACE: 'market_config.json in your workspace',
    ValueOrigin.CODE_DEFAULT: 'code default — set in no file',
    ValueOrigin.RESOLVED: 'resolved at the start',
}
# The part of the key each fingerprint of a CompositeKey stands for
_PART_FINGERPRINTS = (
    (CompositePart.STRATEGY, 'composite_strategy'),
    (CompositePart.CONDITIONS, 'composite_conditions'),
    (CompositePart.ACCOUNT, 'composite_account'),
    (CompositePart.RUN_AXES, 'composite_run_axes'),
)


def compare_carried_configuration(
    payload: ColdStartPayload,
    current: CurrentConfiguration,
) -> ConfigurationComparison:
    """
    Hold a starting session's configuration against the one its carry-over's venue claims carry.

    Args:
        payload: The carry-over the cold start read
        current: What the starting session runs with

    Returns:
        The comparison — CHANGED names every changed value that feeds the key, with its origin
    """
    carried = payload.venue_claims_configuration
    if carried is None:
        if not payload.session_keys and not payload.open_positions:
            return ConfigurationComparison(ConfigurationVerdict.NOTHING_CARRIED, current)
        return _not_compared(
            current, None, 'the carry-over was written before it recorded the configuration '
            'its claims on the venue were made under')
    if current.carry_over_from and carried.profile_name == current.carry_over_from:
        return ConfigurationComparison(ConfigurationVerdict.PARTNER, current, carried)
    if current.rendered_profile is None:
        return _not_compared(current, carried, "this session's configuration could not be rendered")
    if not carried.rendered_config_id:
        return _not_compared(
            current, carried, f'run {carried.run_id} kept no record of what it ran with')
    before = _frozen_document(carried.rendered_config_id)
    if before is None:
        return _not_compared(
            current, carried, f'the record of what run {carried.run_id} ran with '
            f'({carried.rendered_config_id[:12]}) cannot be read from the run-config store')

    try:
        definition = CompositeDefinitionManager()
    except (OSError, ValueError) as error:
        # A definition that cannot be loaded is a configuration error the comparison cannot get
        # past — and like a store that cannot answer, it must not lock the bot out of its position
        return _not_compared(
            current, carried, f'the composite definition could not be loaded ({error})')
    rules = definition.get_rules(CompositeDocument.SESSION)
    fingerprint = definition.get_fingerprint(CompositeDocument.SESSION)
    carried_axes = RunAxes(run_type=current.run_axes.run_type, ticks_from=carried.ticks_from,
                           orders_to=carried.orders_to)
    before_key = build_composite_key(before, CompositeDocument.SESSION, rules, fingerprint,
                                     carried.account_currency, carried_axes)
    after_key = build_composite_key(current.rendered_profile, CompositeDocument.SESSION, rules,
                                    fingerprint, current.account_currency, current.run_axes)
    parts = _changed_parts(before_key, after_key)
    if not parts:
        return ConfigurationComparison(ConfigurationVerdict.SAME, current, carried,
                                       current_key=after_key.composite_key)

    changes = [change for change in config_changes(
        before, current.rendered_profile, CompositeDocument.SESSION, rules)
        if change.composite_part is not None]
    before_paths = set(document_leaves(before, CompositeDocument.SESSION))
    after_paths = set(document_leaves(current.rendered_profile, CompositeDocument.SESSION))
    one_sided = [change.setting for change in changes
                 if (change.setting in before_paths) != (change.setting in after_paths)]
    changes += _resolved_changes(carried, current)
    try:
        origins = value_origins([change.setting for change in changes],
                                current.profile_path, current.broker_type)
    except (OSError, ValueError, TypeError):
        # Where a value comes from labels the message and decides nothing — a layer that cannot
        # be read leaves the label unknown rather than the start undone
        origins = {}
    return ConfigurationComparison(
        ConfigurationVerdict.CHANGED, current, carried, changes, parts, origins, one_sided,
        current_key=after_key.composite_key)


def accepts_composite(offered: Optional[str], comparison: ConfigurationComparison) -> bool:
    """
    Whether `--accept-composite-change <key>` names the composite this session runs.

    The flag names one composite rather than switching the check off: left in a service unit, it
    can never accept a later change, which is refused again.

    Args:
        offered: The key the start was given; None when the flag was not given
        comparison: A CHANGED comparison

    Returns:
        True when the offered key is a prefix of at least eight characters of this session's key
    """
    if not offered or len(offered) < _ACCEPT_KEY_LENGTH or not comparison.current_key:
        return False
    return comparison.current_key.startswith(offered.lower())


def acceptance_command(comparison: ConfigurationComparison) -> str:
    """
    The flag that accepts exactly this change, as the refusal prints it.

    Args:
        comparison: A CHANGED comparison

    Returns:
        For example '--accept-composite-change 7f3a9c2e'
    """
    return f'--accept-composite-change {comparison.current_key[:_ACCEPT_KEY_LENGTH]}'


def composite_change_refusal(
    comparison: ConfigurationComparison,
    still_open: List[str],
    offered: Optional[str] = None,
) -> str:
    """
    The refusal of a start that would continue what is open under another composite.

    Args:
        comparison: A CHANGED comparison
        still_open: What the venue still holds, one line each
        offered: The key `--accept-composite-change` was given, when it names another composite

    Returns:
        The message, as the session log and the operator alert show it
    """
    current = comparison.current
    continued = (f'deployment {current.deployment_id} (bot {current.profile_name})'
                 if current.deployment_id else f'bot {current.profile_name}')
    command = acceptance_command(comparison)
    mismatch = ([f'--accept-composite-change {offered} does not name the composite this session '
                 f'runs ({command.split()[-1]}): the',
                 'configuration changed again after the refusal that printed it.', '']
                if offered else [])
    lines = [
        '❌ CONFIGURATION CHANGED WHILE POSITIONS ARE OPEN — start refused',
        '',
        *mismatch,
        f'This session continues {continued}.',
        f'What the venue still holds was carried over from run {_run_of(comparison)}, and this '
        "session's configuration",
        f'defines another composite ({_parts_of(comparison)} changed):',
        '',
        *_describe_changes(comparison),
        '',
        'Still open at the venue:',
        *[f'  · {line}' for line in still_open],
        '',
        'A trade opened under one configuration and closed under another is the result of '
        'neither, so the start stops.',
        'Ways on:',
        '  1. Undo the change — restore the values above; for a shipped or a code default, '
        'write the old value into',
        '     the profile. The session continues as one series.',
        *(["     A value marked 'not set' cannot be restored in a file: when its new value keeps "
           'the old behaviour,',
           '     take way 3.'] if comparison.one_sided else []),
        '  2. Finish under the old configuration — run it until nothing is open, then switch. '
        'Resting orders alone',
        "     can be cancelled at the venue; a holding sold by hand stays in this bot's position "
        'book (#627), so at',
        '     spot the bot has to close it itself.',
        f'  3. Accept it for this start — run again with {command}. What is open continues '
        'under the',
        '     new configuration, and the run records that it crossed. From here the deployment is '
        'two series:',
        '     figures that need one long unbroken series — Sharpe, profit factor, the drawdown of '
        'one composite,',
        '     a backtest of the same period — start over, or have to be rebuilt piece by piece.',
    ]
    return '\n'.join(lines)


def _describe_changes(comparison: ConfigurationComparison) -> List[str]:
    """
    The changed values as a table: setting, before → after, the part of the key, the origin.

    Args:
        comparison: A CHANGED comparison

    Returns:
        One line per value
    """
    rows = [(change.setting,
             f'{_shown(change.before)} → {_shown(change.after)}',
             change.composite_part.value.replace('_', ' ') if change.composite_part else '',
             _origin_words(comparison.origins.get(change.setting)))
            for change in comparison.changes]
    if not rows:
        return ['  (the composite key differs, but no single value could be named)']
    widths = [max(len(row[column]) for row in rows) for column in range(3)]
    lines = [f'  {setting:<{widths[0]}}   {change:<{widths[1]}}   {part:<{widths[2]}}   {origin}'
             for setting, change, part, origin in rows]
    if comparison.one_sided:
        lines += [
            '',
            "  A value marked 'not set' exists in only one of the two sessions' configurations: "
            'the code changed',
            '  between them — an update added or dropped it.',
        ]
    return lines


def new_stretch_notice(comparison: ConfigurationComparison) -> str:
    """
    What a start says when its configuration changed and nothing of the bot is open.

    Args:
        comparison: A CHANGED comparison

    Returns:
        The notice for the session log
    """
    return '\n'.join([
        f'🧬 Configuration changed since run {_run_of(comparison)} ({_parts_of(comparison)}) — '
        f'nothing of this bot is open at the venue, so the session starts and begins a new '
        f'stretch:',
        *_describe_changes(comparison),
    ])


def accepted_notice(comparison: ConfigurationComparison, still_open: List[str]) -> str:
    """
    What a start says when `--accept-composite-change` lets it continue what is open.

    Args:
        comparison: A CHANGED comparison
        still_open: What the venue still holds, one line each

    Returns:
        The notice for the session log, the console and the run's record
    """
    return '\n'.join([
        f'⚠️  CONFIGURATION CHANGE ACCEPTED ({acceptance_command(comparison)}): what the venue '
        f'still holds, carried over from run {_run_of(comparison)}, continues under another '
        f'composite ({_parts_of(comparison)} changed):',
        *_describe_changes(comparison),
        *[f'  · {line}' for line in still_open],
        f'  {_TWO_SERIES}',
    ])


def not_compared_notice(comparison: ConfigurationComparison) -> str:
    """
    What a start says when it could not compare its configuration with the carry-over's.

    Args:
        comparison: A NOT_COMPARED comparison

    Returns:
        The notice
    """
    return (f'🧬 Configuration not compared with the carry-over: {comparison.reason}. Whether this '
            f'session runs the composite that opened what it takes over is unknown.')


def partner_notice(comparison: ConfigurationComparison) -> str:
    """
    What a start says when a declared partner made the claims it takes over.

    Args:
        comparison: A PARTNER comparison

    Returns:
        The notice
    """
    partner = comparison.carried.profile_name if comparison.carried else '?'
    return (f'🧬 Configuration not compared: the claims on the venue this session takes over were '
            f"written by its declared partner '{partner}' (carry_over_from), a stage that changes "
            f'the configuration on purpose.')


def composite_change_accepted_finding(
    comparison: ConfigurationComparison,
    still_open: List[str],
) -> ValidationFinding:
    """
    The record that `--accept-composite-change` let a start continue what is open.

    Args:
        comparison: A CHANGED comparison
        still_open: What the venue still held

    Returns:
        A run-scoped warning
    """
    return ValidationFinding(
        severity=Severity.WARNING, check=_COMPOSITE_CHANGE_ACCEPTED_CHECK,
        domain=ValidationDomain.SETUP, scope=_RUN_SCOPE,
        message=(f'{acceptance_command(comparison)} let this session continue what the venue '
                 f'still held under another composite than run {_run_of(comparison)} ran '
                 f'({_parts_of(comparison)} changed: '
                 f'{", ".join(change.setting for change in comparison.changes)}). Still open: '
                 f'{"; ".join(still_open)}. A trade that crossed the change is the result of '
                 f'neither configuration. {_TWO_SERIES}'))


def configuration_not_compared_finding(comparison: ConfigurationComparison) -> ValidationFinding:
    """
    The record that a start took over what is open without being able to compare configurations.

    Args:
        comparison: A NOT_COMPARED comparison

    Returns:
        A run-scoped warning
    """
    return ValidationFinding(
        severity=Severity.WARNING, check=_CONFIGURATION_NOT_COMPARED_CHECK,
        domain=ValidationDomain.SETUP, scope=_RUN_SCOPE,
        message=(f'This session took over what the venue still held without comparing its '
                 f'configuration with the one that opened it: {comparison.reason}.'))


def _not_compared(
    current: CurrentConfiguration,
    carried: Optional[ConfigurationCarryOver],
    reason: str,
) -> ConfigurationComparison:
    """
    A comparison that could not be made, and why.

    Args:
        current: The starting session's side
        carried: The carry-over's side, when it recorded one
        reason: Why the comparison could not be made

    Returns:
        A NOT_COMPARED comparison
    """
    return ConfigurationComparison(
        ConfigurationVerdict.NOT_COMPARED, current, carried, reason=reason)


def _frozen_document(config_id: str) -> Optional[Dict[str, Any]]:
    """
    A rendered profile frozen in the run-config store.

    Args:
        config_id: Its content id

    Returns:
        The document; None when the store does not hold it or it cannot be read
    """
    try:
        path = RunConfigStore(Path(AppConfigManager().get_run_configs_path())).frozen_path_of(
            config_id)
        if path is None:
            return None
        with open(path, 'r') as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _changed_parts(before: CompositeKey, after: CompositeKey) -> List[CompositePart]:
    """
    The parts of the key that differ between two projections.

    Args:
        before: The carry-over's key
        after: The starting session's key

    Returns:
        The differing parts, in the key's order
    """
    return [part for part, field in _PART_FINGERPRINTS
            if getattr(before, field) != getattr(after, field)]


def _resolved_changes(
    carried: ConfigurationCarryOver,
    current: CurrentConfiguration,
) -> List[ConfigChange]:
    """
    The changes of what the start resolves rather than reads: the account currency, the run axes.

    Args:
        carried: The carry-over's side
        current: The starting session's side

    Returns:
        One change per differing value
    """
    pairs = (
        ('account_currency', carried.account_currency, current.account_currency,
         CompositePart.ACCOUNT),
        ('ticks_from', carried.ticks_from.value, current.run_axes.ticks_from.value,
         CompositePart.RUN_AXES),
        ('orders_to', carried.orders_to.value,
         current.run_axes.orders_to.value if current.run_axes.orders_to else None,
         CompositePart.RUN_AXES),
    )
    return [ConfigChange(setting=setting, before=before, after=after,
                         composite_tier=CompositeTier.FLOOR, composite_part=part)
            for setting, before, after, part in pairs if before != after]


def _origin_words(origin: Optional[ValueOrigin]) -> str:
    """
    Where a value comes from, as the message says it.

    Args:
        origin: The layer that sets it; None when the layers could not be read

    Returns:
        The words
    """
    return _ORIGIN_WORDS[origin] if origin is not None else 'origin unknown — a layer could not be read'


def _run_of(comparison: ConfigurationComparison) -> str:
    """
    The run whose claims on the venue the start takes over.

    Args:
        comparison: The comparison

    Returns:
        Its id, or a placeholder when the carry-over recorded none
    """
    return comparison.carried.run_id if comparison.carried else 'an earlier session'


def _parts_of(comparison: ConfigurationComparison) -> str:
    """
    The changed parts of the key, as words.

    Args:
        comparison: A CHANGED comparison

    Returns:
        For example 'strategy, conditions'
    """
    return ', '.join(part.value.replace('_', ' ') for part in comparison.changed_parts)


def _shown(value: Any) -> str:
    """
    A value as a message shows it — written the way a profile writes it (`false`, `"leave"`),
    compact, and shortened when long.

    Args:
        value: The value

    Returns:
        Its text; 'not set' for a value one side does not hold
    """
    if value is None:
        return 'not set'
    text = json.dumps(value, separators=(',', ':'), ensure_ascii=False)
    return text if len(text) <= _MAX_SHOWN else f'{text[:_MAX_SHOWN - 1]}…'
