"""
Composite key builder — which accounts ran the same strategy under the same conditions (#615).

A pure projection of one account's rendered document onto its composite key. The rendered document
(#547) holds the values the account ran with, every default filled in, so a value written out and
the same value taken as its default give one key. Each value is looked up in the composite
definition: a defining value enters its part of the key, a neutral one does not, an excluding one
makes the account an excluded member while it is switched on. The floor never asks the definition —
the decision logic and the account model come from the document, the resolved account currency and
the run's axes from the pipeline.

The key is built from parts so that two runs can be compared on everything except one part: a
sweep's fairness compares the strategy part without the swept values, the parity comparison the
strategy and account parts. The same projection yields the value-by-value difference of two
documents, which a refused set, a sweep and a deployment's change marks name.
"""

from typing import Any, Dict, List, Optional

from python.framework.constants.composite_constants import FLOOR_DOCUMENT_PATHS
from python.framework.factory.strategy_config_renderer import LOGIC_TYPE_KEY, WORKER_TYPE_KEY
from python.framework.types.composite_types import (
    CompositeDocument,
    CompositeKey,
    CompositePart,
    ConfigChange,
    RunAxes,
)
from python.framework.types.config_types.composite_definition_config_types import CompositeTier
from python.framework.types.trading_env_types.stress_test_types import StressTestConfig
from python.framework.utils.config_fingerprint_utils import generate_config_fingerprint
from python.framework.utils.config_merge_utils import is_meta_key

# The version of this projection, carried by every key it makes. Raised whenever the projection
# changes what a key covers, so a key never reads alike across two projections.
KEY_VERSION = 1

# The rendered profile's top level that is document metadata, not a configuration value.
_SESSION_DOCUMENT_METADATA = frozenset({'kind'})

# The last path segment of a stress block: an excluding rule over it asks the stress model whether
# any test is switched on — a block with every test off is not a stress test.
_STRESS_BLOCK = 'stress_test_config'


def document_leaves(document: Dict[str, Any], document_kind: CompositeDocument) -> Dict[str, Any]:
    """
    Flatten a rendered document into the values the composite key reads.

    A session's `blocks` level is lifted, so its paths read as the profile writes them. The type
    key a factory injected into a component's parameters is left out: the strategy names each
    component already, and the injected copy held, until 2026-10-25, the absolute path one machine
    resolved — so a document frozen before then reads like one frozen after. Comments are dropped
    at every level, and a number reads alike whether it was written as an integer or a float.

    Args:
        document: One scenario of a rendered scenario set, or a rendered AutoTrader profile
        document_kind: Which of the two it is

    Returns:
        Path → value for every leaf; a list is one leaf
    """
    view = _document_view(document, document_kind)
    leaves: Dict[str, Any] = {}
    _flatten(view, '', leaves)
    return leaves


def tier_of(path: str, rules: Dict[str, CompositeTier]) -> CompositeTier:
    """
    The composite tier of one value.

    The floor is decided here, before the definition is asked. Otherwise the longest rule that
    names the path or a prefix of it decides; a value no rule reaches counts as defining, so an
    unforeseen value can split a composite but never join two.

    Args:
        path: The value's path
        rules: The definition's rules for the document

    Returns:
        The value's tier
    """
    if path in FLOOR_DOCUMENT_PATHS:
        return CompositeTier.FLOOR
    rule = _matching_rule(path, rules)
    return rules[rule] if rule is not None else CompositeTier.DEFINING


def part_of(path: str) -> CompositePart:
    """
    The part of the key a value feeds.

    Args:
        path: The value's path

    Returns:
        ACCOUNT for the account model, STRATEGY for the strategy and its signal source, CONDITIONS
        for everything else
    """
    if path == 'market_entry.trading_model':
        return CompositePart.ACCOUNT
    if path == 'strategy_config' or path.startswith('strategy_config.'):
        return CompositePart.STRATEGY
    if path.split('.')[-1] == 'data_sentiment_type':
        return CompositePart.STRATEGY
    return CompositePart.CONDITIONS


def build_composite_key(
    document: Dict[str, Any],
    document_kind: CompositeDocument,
    rules: Dict[str, CompositeTier],
    definition_fingerprint: str,
    account_currency: str,
    run_axes: RunAxes,
) -> CompositeKey:
    """
    Project one account's rendered document onto its composite key.

    Args:
        document: The account's rendered document — one scenario, or one profile
        document_kind: Which of the two it is
        rules: The definition's rules for the document
        definition_fingerprint: The fingerprint of those rules
        account_currency: The account currency as the one account-currency rule resolved it
        run_axes: The resolved axes of the run the account belongs to

    Returns:
        The key with its parts
    """
    leaves = document_leaves(document, document_kind)
    strategy: Dict[str, Any] = {}
    conditions: Dict[str, Any] = {}
    for path, value in leaves.items():
        if tier_of(path, rules) not in (CompositeTier.FLOOR, CompositeTier.DEFINING):
            continue
        part = part_of(path)
        if part is CompositePart.STRATEGY:
            strategy[path] = value
        elif part is CompositePart.CONDITIONS:
            conditions[path] = value
    account = {'account_currency': account_currency,
               'account_model': leaves.get('market_entry.trading_model')}
    axes = {'run_type': run_axes.run_type,
            'ticks_from': run_axes.ticks_from.value,
            'orders_to': run_axes.orders_to.value if run_axes.orders_to is not None else None}
    parts = {
        CompositePart.STRATEGY.value: generate_config_fingerprint(strategy),
        CompositePart.CONDITIONS.value: generate_config_fingerprint(conditions),
        CompositePart.ACCOUNT.value: generate_config_fingerprint(account),
        CompositePart.RUN_AXES.value: generate_config_fingerprint(axes),
    }
    composite_key = generate_config_fingerprint(
        {**parts, 'definition': definition_fingerprint, 'version': KEY_VERSION})
    return CompositeKey(
        composite_key=composite_key,
        composite_strategy=parts[CompositePart.STRATEGY.value],
        composite_conditions=parts[CompositePart.CONDITIONS.value],
        composite_account=parts[CompositePart.ACCOUNT.value],
        composite_run_axes=parts[CompositePart.RUN_AXES.value],
        excluded_member=_is_excluded_member(document, document_kind, leaves, rules),
        composite_definition_fingerprint=definition_fingerprint,
        composite_key_version=KEY_VERSION,
    )


def config_changes(
    before: Dict[str, Any],
    after: Dict[str, Any],
    document_kind: CompositeDocument,
    rules: Dict[str, CompositeTier],
) -> List[ConfigChange]:
    """
    Every value that differs between two rendered documents of one kind, in path order.

    Args:
        before: The first document
        after: The second document
        document_kind: Which kind both are
        rules: The definition's rules for that kind

    Returns:
        One change per differing value, with its tier and the part of the key it feeds
    """
    before_leaves = document_leaves(before, document_kind)
    after_leaves = document_leaves(after, document_kind)
    changes: List[ConfigChange] = []
    for path in sorted(set(before_leaves) | set(after_leaves)):
        old, new = before_leaves.get(path), after_leaves.get(path)
        if old == new and (path in before_leaves) == (path in after_leaves):
            continue
        tier = tier_of(path, rules)
        in_key = tier in (CompositeTier.FLOOR, CompositeTier.DEFINING)
        changes.append(ConfigChange(
            setting=path, before=old, after=new, composite_tier=tier,
            composite_part=part_of(path) if in_key else None))
    return changes


def composite_groups(keys: Dict[str, CompositeKey]) -> Dict[str, List[str]]:
    """
    Group a run's accounts by their composite key; excluded members belong to no composite.

    Args:
        keys: Unit name → its composite key

    Returns:
        Composite key → the unit names that share it, in the order the units were given
    """
    groups: Dict[str, List[str]] = {}
    for unit_name, key in keys.items():
        if key.excluded_member:
            continue
        groups.setdefault(key.composite_key, []).append(unit_name)
    return groups


def _document_view(document: Dict[str, Any], document_kind: CompositeDocument) -> Dict[str, Any]:
    """
    The document as the key reads it: a session's blocks lifted, the injected type keys left out.

    Args:
        document: The rendered document
        document_kind: Which kind it is

    Returns:
        A shallow view; the document itself is never changed
    """
    if document_kind is CompositeDocument.SESSION:
        view = {key: value for key, value in document.items()
                if key != 'blocks' and key not in _SESSION_DOCUMENT_METADATA}
        view.update(document.get('blocks') or {})
    else:
        view = dict(document)
    strategy = view.get('strategy_config')
    if isinstance(strategy, dict):
        view['strategy_config'] = _strategy_without_injected_types(strategy)
    return view


def _strategy_without_injected_types(strategy: Dict[str, Any]) -> Dict[str, Any]:
    """
    Leave out the type key each factory injected into a component's parameters.

    Args:
        strategy: A rendered strategy section

    Returns:
        A copy without the injected type keys; the configured types stay where the strategy names them
    """
    projected = dict(strategy)
    logic_config = projected.get('decision_logic_config')
    if isinstance(logic_config, dict):
        projected['decision_logic_config'] = {
            key: value for key, value in logic_config.items() if key != LOGIC_TYPE_KEY}
    workers = projected.get('workers')
    if isinstance(workers, dict):
        projected['workers'] = {
            name: ({key: value for key, value in params.items() if key != WORKER_TYPE_KEY}
                   if isinstance(params, dict) else params)
            for name, params in workers.items()
        }
    return projected


def _flatten(node: Any, prefix: str, leaves: Dict[str, Any]) -> None:
    """
    Collect the leaves below one node, comments dropped and numbers normalised.

    Args:
        node: The node
        prefix: Its path
        leaves: Where the leaves are collected
    """
    if isinstance(node, dict) and node:
        for key, value in node.items():
            if is_meta_key(key):
                continue
            _flatten(value, f'{prefix}.{key}' if prefix else key, leaves)
        return
    leaves[prefix] = _normalized(node)


def _normalized(value: Any) -> Any:
    """
    A value as the key compares it: an integral float as an integer, inside lists too.

    Args:
        value: A leaf value

    Returns:
        The normalised value; a bool stays a bool
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, list):
        return [_normalized(item) for item in value]
    if isinstance(value, dict):
        return {key: _normalized(item) for key, item in value.items() if not is_meta_key(key)}
    return value


def _matching_rule(path: str, rules: Dict[str, CompositeTier]) -> Optional[str]:
    """
    The longest rule that names a path or a prefix of it.

    Args:
        path: The value's path
        rules: The definition's rules

    Returns:
        The rule, or None when none reaches the path
    """
    best: Optional[str] = None
    for rule in rules:
        if path == rule or path.startswith(rule + '.'):
            if best is None or len(rule) > len(best):
                best = rule
    return best


def _is_excluded_member(
    document: Dict[str, Any],
    document_kind: CompositeDocument,
    leaves: Dict[str, Any],
    rules: Dict[str, CompositeTier],
) -> bool:
    """
    Whether an excluding value is switched on for this account.

    A stress block counts only while one of its tests is on; any other excluding value counts
    while it holds something.

    Args:
        document: The rendered document
        document_kind: Which kind it is
        leaves: Its leaves
        rules: The definition's rules

    Returns:
        True when the account is an excluded member
    """
    view = _document_view(document, document_kind)
    for rule, tier in rules.items():
        if tier is not CompositeTier.EXCLUDING:
            continue
        if rule.split('.')[-1] == _STRESS_BLOCK:
            block = _subtree(view, rule)
            # The rendered block writes an absent test as null; the model reads absence.
            if isinstance(block, dict) and StressTestConfig.from_dict(
                    {name: test for name, test in block.items() if test is not None}
            ).has_any_enabled():
                return True
            continue
        if any(value for path, value in leaves.items()
               if _matching_rule(path, rules) == rule):
            return True
    return False


def _subtree(view: Dict[str, Any], path: str) -> Any:
    """
    The node at a path of the document view.

    Args:
        view: The document view
        path: A dotted path

    Returns:
        The node, or None when the path does not exist
    """
    node: Any = view
    for segment in path.split('.'):
        if not isinstance(node, dict):
            return None
        node = node.get(segment)
    return node
