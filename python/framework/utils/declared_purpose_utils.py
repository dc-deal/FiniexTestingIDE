"""
What a configuration declares about itself (#576), read the same way by both loaders.

A scenario set and an AutoTrader profile both carry `run_purpose` and `description` at their
top level. One reader, so the two pipelines cannot disagree about what a missing or misspelt
value means.

Only the repository's own configurations declare a purpose: its test folders `fixture`, its
field-study folder `certificate`. A configuration the user owns always runs as `regular`, so it
declares none — a declaration there is refused, whatever its value.
"""

from typing import Any, Dict

from python.framework.types.run_purpose_types import RunPurpose


def read_declared_purpose(raw: Dict[str, Any], source_name: str, user_owned: bool) -> RunPurpose:
    """
    Read what a configuration's runs are for, and check why it says it exists.

    The description is checked here and kept nowhere on the loaded configuration: nothing that
    runs reads it, and the configuration directory serves it from the file itself.

    Args:
        raw: The configuration's top-level JSON
        source_name: Its file name, named in a refusal
        user_owned: Whether it lives in a user algo directory or in `user_configs/`, where no
            purpose may be declared

    Returns:
        The declared purpose — REGULAR when the configuration says nothing
    """
    if user_owned and 'run_purpose' in raw:
        raise ValueError(
            f'{source_name}: run_purpose does not belong in a user algo directory or in '
            f'user_configs/ — a configuration there always runs as regular. Remove the key.')
    declared = raw.get('run_purpose', RunPurpose.REGULAR.value)
    try:
        run_purpose = RunPurpose(declared)
    except ValueError:
        allowed = ', '.join(purpose.value for purpose in RunPurpose)
        raise ValueError(
            f'{source_name}: run_purpose {declared!r} is not one of: {allowed}') from None
    description = raw.get('description')
    if description is not None and not isinstance(description, str):
        raise ValueError(f'{source_name}: description must be text')
    return run_purpose
