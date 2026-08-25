"""
FiniexTestingIDE - Signal Feed Gate (#466)

One test that fails when ANY check failed, whatever it was.

The tests beside this one each assert a named check, which is how a failure gets a readable
diagnosis. This one exists for the gap that leaves: a check nobody named would fail into the
certificate while the suite stayed green, and a release gate that exits 0 while its own
artifact says FAILED certifies nothing (#372). It also means a check added to the validator
later cannot be silently unasserted.
"""

import re
from pathlib import Path

from tests.live_signal_feed.signal_feed_assertions import assert_no_failures

SUITE_DIR = Path(__file__).parent
# Prefixes asserted as a group, e.g. assert_group(assessment, 'envelope_field_').
_GROUP_ASSERTION = re.compile(r"assert_group\(\s*assessment\s*,\s*'([^']+)'")


class TestGate:
    """The run as a whole."""

    def test_no_check_failed(self, assessment):
        """Every assertion held — the certificate's own verdict, as an exit code."""
        assert_no_failures(assessment)

    def test_every_check_is_covered_by_a_named_test(self, assessment):
        """
        Guards the diagnosis, not the outcome.

        A failing check whose name no test mentions still turns the gate red through
        test_no_check_failed — but with no test naming it, the operator gets a bare check
        name instead of the sentence explaining why it matters. This keeps the two in step:
        every check the validator can emit is either named by a test or covered by one of
        the prefix assertions (the field table is forty checks and is asserted as a group).
        """
        suite_text = ' '.join(
            path.read_text(encoding='utf-8')
            for path in SUITE_DIR.glob('test_*.py'))
        prefixes = tuple(_GROUP_ASSERTION.findall(suite_text))
        unnamed = sorted({
            check.name for check in assessment.checks
            if check.name not in suite_text and not check.name.startswith(prefixes)})
        assert not unnamed, (
            f'checks no test in this suite names: {unnamed} — add an assertion so a '
            f'failure arrives with an explanation instead of only a name')
