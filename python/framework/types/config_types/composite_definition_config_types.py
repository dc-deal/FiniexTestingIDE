"""
Composite definition config types — which configuration values define a composite (#615).
"""

from enum import StrEnum
from typing import Any, Dict

from pydantic import Field, field_validator

from python.framework.constants.composite_constants import FLOOR_DOCUMENT_PATHS
from python.framework.types.config_types.strict_config_model import StrictConfigModel
from python.framework.utils.config_merge_utils import is_meta_key


class CompositeTier(StrEnum):
    """
    How one configuration value bears on the composite key — its composite tier.

    FLOOR: always defining, and no definition may loosen it; set in code, never in the file
    DEFINING: part of the key by default; a user may relax it
    NEUTRAL: not part of the key by default; a user may tighten it
    EXCLUDING: makes the account an excluded member while it is switched on
    """
    FLOOR = 'floor'
    DEFINING = 'defining'
    NEUTRAL = 'neutral'
    EXCLUDING = 'excluding'


class CompositeDefinitionConfig(StrictConfigModel):
    """
    The composite definition: a tier for every path of the rendered documents, per pipeline.

    A rule names a path or a prefix of one, and the longest rule that matches a value decides its
    tier — so a block's rule is the default for every value in it, and a rule below it is an
    exception. The floor is not configurable: a rule naming one of its paths, or the floor tier
    itself, is refused.

    Args:
        scenario: Rules for a backtest scenario, paths as the rendered scenario set holds them
        session: Rules for an AutoTrader session, paths as the rendered profile holds them,
            without the `blocks.` level
    """
    scenario: Dict[str, CompositeTier] = Field(default_factory=dict)
    session: Dict[str, CompositeTier] = Field(default_factory=dict)

    @field_validator('scenario', 'session', mode='before')
    @classmethod
    def _drop_section_comments(cls, rules: Any) -> Any:
        """
        Remove the comment a section may carry before its rules are read as tiers.

        Args:
            rules: One pipeline's raw rules

        Returns:
            The rules without meta keys
        """
        if isinstance(rules, dict):
            return {path: tier for path, tier in rules.items() if not is_meta_key(path)}
        return rules

    @field_validator('scenario', 'session')
    @classmethod
    def _floor_stays_in_code(cls, rules: Dict[str, CompositeTier]) -> Dict[str, CompositeTier]:
        """
        Refuse a rule that is no path, or that would loosen or restate the floor.

        Args:
            rules: One pipeline's rules

        Returns:
            The rules, unchanged
        """
        for path, tier in rules.items():
            if not path or path.startswith('.') or path.endswith('.') or '..' in path:
                raise ValueError(f"composite definition: '{path}' is not a path")
            if tier is CompositeTier.FLOOR:
                raise ValueError(
                    f"composite definition: '{path}' is set to 'floor' — the floor is fixed in "
                    f'code and cannot be declared in the file')
            if path in FLOOR_DOCUMENT_PATHS:
                raise ValueError(
                    f"composite definition: '{path}' belongs to the floor, which no definition "
                    f'may loosen — remove the rule')
        return rules
