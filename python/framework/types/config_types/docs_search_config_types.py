"""
FiniexTestingIDE - Documentation Search Configuration Types
Pydantic model for app_config.json::docs_search.
"""
from typing import List

from python.framework.types.config_types.strict_config_model import StrictConfigModel


class DocsSearchConfig(StrictConfigModel):
    """
    What the terminal search searches beyond the documentation tree.

    The documentation is searched in every installation. A maintainer may keep further
    collections that cannot live in the repository — notes, reference material — and searches
    them through a command of their own: each entry is one command, run with the search term
    appended as its last argument, its output shown below the documentation hits. Empty by
    default, so a fresh clone searches the documentation and nothing else.
    """
    extra_commands: List[List[str]] = []
