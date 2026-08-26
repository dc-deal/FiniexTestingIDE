"""
FiniexTestingIDE - Certificate Config Utilities
Reading EFFECTIVE configuration values for a certificate's contract check.

The mechanism is shared; the values are not. A certificate declares the settings its
reference was established under, and this reads back what the run actually saw — after the
user_configs/ cascade, never from the base file. What a file declares and what a run
measured are two different facts, and only the second one belongs in a certificate.
"""

from typing import Any, Dict, List, Tuple


def effective_config_value(config: Dict[str, Any], dotted_path: str) -> Any:
    """
    Read one value out of a merged configuration by dotted path.

    Args:
        config: The merged (effective) configuration
        dotted_path: Key path, e.g. 'backtesting.execution.max_parallel_scenarios'

    Returns:
        The value, or None when the path does not exist
    """
    node: Any = config
    for key in dotted_path.split('.'):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    return node


def compare_config_contract(
    config: Dict[str, Any],
    contract: Dict[str, Any],
) -> Tuple[Dict[str, Dict[str, Any]], List[str]]:
    """
    Compare a declared contract against the effective configuration.

    Both halves are returned regardless of the outcome: a certificate that recorded only
    the mismatches could not be told apart from one that never ran the comparison.

    Args:
        config: The merged (effective) configuration
        contract: Dotted path → the value the reference was established under

    Returns:
        Tuple of (path → {expected, effective}, one warning per deviation)
    """
    recorded: Dict[str, Dict[str, Any]] = {}
    warnings: List[str] = []

    for dotted_path, expected in contract.items():
        effective = effective_config_value(config, dotted_path)
        recorded[dotted_path] = {'expected': expected, 'effective': effective}
        if effective != expected:
            warnings.append(
                f'CONFIG CONTRACT: {dotted_path} is {effective!r}, but the reference was '
                f'established at {expected!r}. The deviations reported alongside are a '
                f'result of the configuration, not of the code.')

    return recorded, warnings
