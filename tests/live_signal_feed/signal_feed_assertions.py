"""
FiniexTestingIDE - Signal Feed Assertion Helpers (#466)

Every test in this suite makes the same move: look up a named check on the one reading the
session took, and fail with what that check measured. Centralized so a missing check is
distinguishable from a failing one — the first means the run never got far enough to
evaluate it, and reporting that as a contract violation would blame the producer for our
own transport failure.
"""

from typing import List

import pytest

from python.framework.types.signal_certificate_types import (
    FeedCheck,
    SignalFeedAssessment,
)


def named(assessment: SignalFeedAssessment, name: str) -> List[FeedCheck]:
    """
    Every instance of one check in the assessment.

    Envelope checks are evaluated once per observation, so a name legitimately appears
    more than once and all instances have to hold.

    Args:
        assessment: The session's reading
        name: The check's stable identifier

    Returns:
        Matching checks in evaluation order
    """
    return [check for check in assessment.checks if check.name == name]


def assert_check(assessment: SignalFeedAssessment, name: str) -> None:
    """
    Assert one named check held, in every instance of it.

    Args:
        assessment: The session's reading
        name: The check's stable identifier
    """
    checks = named(assessment, name)
    if not checks:
        pytest.fail(
            f"the run never evaluated '{name}' — the transport failures in the "
            f'certificate are the finding, not this contract check')
    failed = [check for check in checks if not check.ok]
    assert not failed, ' | '.join(check.detail for check in failed)


def assert_group(assessment: SignalFeedAssessment, prefix: str) -> None:
    """
    Assert every check whose name starts with a prefix held.

    Used for the field table, where one assertion exists per contracted field and the test
    should name all offenders at once rather than the first.

    Args:
        assessment: The session's reading
        prefix: Check-name prefix
    """
    checks = [c for c in assessment.checks if c.name.startswith(prefix)]
    if not checks:
        pytest.fail(f"the run never evaluated any '{prefix}*' check")
    failed = [f'{c.name}: {c.detail}' for c in checks if not c.ok]
    assert not failed, ' | '.join(failed)


def assert_no_failures(assessment: SignalFeedAssessment) -> None:
    """
    Assert the whole run is clean, naming every failure.

    The catch-all behind the named assertions: a check that no test mentions would
    otherwise fail into the certificate while the suite stayed green.

    Args:
        assessment: The session's reading
    """
    failed = assessment.get_failed()
    assert not failed, ' | '.join(f'{c.name}: {c.detail}' for c in failed)
