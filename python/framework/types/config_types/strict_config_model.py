"""
FiniexTestingIDE - Strict Config Model Base

The base a configuration model inherits when an unknown key in its file should be a hard
error rather than a silent drop.
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from python.framework.utils.config_merge_utils import without_meta_keys


class StrictConfigModel(BaseModel):
    """
    Base for a config model whose file must not silently swallow a typo.

    `market_config.json` had NO guard of any kind: `check_unknown_keys` is called for the
    AutoTrader profile and the scenario set, never for that file, and its models did not
    forbid extras — so a misspelled key was dropped in the one file carrying `dry_run`,
    `credentials_file` and `session_end_orders`. A posture setting that reads as absent
    because of a typo is the failure this closes.

    `ConnectionPolicy` inherits it too, and that is why the base lives in its own unit
    rather than inside one domain's type module: the policy is shared by three files (§43),
    so a second copy of this rule would be a rule written twice (§19).

    `_comment` still passes, because §28 makes it the way a config file explains itself. The
    whitelist is NOT re-declared here — it is taken from `without_meta_keys`, the same one
    `check_unknown_keys` honours, so the two cannot drift apart.
    """

    model_config = ConfigDict(extra='forbid')

    @model_validator(mode='before')
    @classmethod
    def _drop_meta_keys(cls, data: Any) -> Any:
        """
        Remove the documented meta keys before the strict check sees them.

        Args:
            data: The raw mapping under construction, or whatever pydantic passes through

        Returns:
            The mapping without its meta keys; anything not a mapping is returned unchanged
        """
        if isinstance(data, dict):
            return without_meta_keys(data)
        return data
