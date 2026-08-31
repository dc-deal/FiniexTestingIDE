"""
FiniexTestingIDE - Certificate Tree State
A certificate run must not be dirtied by its own artifact (#466, #468).

Found by running the release gate twice in one evening. `git status --porcelain` counts
UNTRACKED files, and every certificate run writes its artifact into its reports directory,
so the second run read the first run's output as an uncommitted change and recorded the
tree as dirty although every line of code was committed. Measured across three runs the
same night: 0 → 1 → 2 uncommitted, exactly tracking the artifacts written.

Harmless while rehearsing (`dev` is exempt from the dirty gate) and not harmless at all on
a declared release, which is precisely when it would fire: a release run that fails once
would fail its repeat for a second, unrelated reason — and the message points at the code,
which was never the problem.

Runs against a scripted `git`, not against this repository: the assertion is about the
rule, and a test that reads the real working tree would pass or fail by whatever the
developer happened to have open.
"""

import subprocess

import pytest

from python.framework.utils import git_info_utils
from python.framework.utils.git_info_utils import get_git_commit, get_git_info

REPORTS_DIR = 'tests/live_signal_feed/reports'


class FakeCompleted:
    """One scripted `git` answer."""

    def __init__(self, stdout: str):
        """
        Hold the output a scripted git call returns.

        Args:
            stdout: What the call writes to standard output
        """
        self.stdout = stdout
        self.returncode = 0


def scripted_git(status_output: str):
    """
    Build a `subprocess.run` stand-in answering the calls get_git_info makes.

    Args:
        status_output: What `git status --porcelain` returns

    Returns:
        A callable with subprocess.run's signature
    """
    answers = {
        ('git', '--version'): 'git version 2.43.0',
        ('git', 'rev-parse', '--short', 'HEAD'): 'abc1234',
        ('git', 'rev-parse', '--abbrev-ref', 'HEAD'): 'dev-v-1-4',
        ('git', 'log', '-1', '--format=%cI'): '2026-08-28T22:40:00+00:00',
        ('git', 'log', '-1', '--format=%s'): 'rework signal polling',
        ('git', 'status', '--porcelain'): status_output,
    }

    def run(command, **_kwargs):
        return FakeCompleted(answers[tuple(command)])

    return run


@pytest.fixture
def scripted(monkeypatch):
    """
    Install a scripted git for one test.

    Returns:
        A callable taking the porcelain output the test wants git to report
    """
    def install(status_output: str):
        # The reader caches per process (§41), so each scripted case must start from an
        # empty cache — otherwise the second test reads the first test's answer and the
        # suite goes green while asserting nothing.
        get_git_info.cache_clear()
        get_git_commit.cache_clear()
        monkeypatch.setattr(subprocess, 'run', scripted_git(status_output))
        monkeypatch.setattr(git_info_utils.subprocess, 'run', scripted_git(status_output))
    yield install
    # And clear it again on the way out: monkeypatch removes the scripted subprocess, but
    # the SCRIPTED ANSWER would stay in the cache and reach every later test in this
    # process — a fake commit hash nobody would think to look for.
    get_git_info.cache_clear()
    get_git_commit.cache_clear()


class TestOwnArtifactDoesNotDirtyTheTree:
    """The self-inflicted wound, pinned from both sides."""

    def test_an_untracked_artifact_in_the_reports_dir_is_not_a_dirty_tree(self, scripted):
        scripted(f'?? {REPORTS_DIR}/signal_feed_report_dev_2026-08-28_224049.json')

        info = get_git_info(ignore_untracked_under=REPORTS_DIR)

        assert info.dirty is False
        assert info.uncommitted_count == 0

    def test_without_the_exemption_the_same_artifact_still_counts(self, scripted):
        """The default is unchanged — every other consumer of this reader sees it."""
        scripted(f'?? {REPORTS_DIR}/signal_feed_report_dev_2026-08-28_224049.json')

        info = get_git_info()

        assert info.dirty is True
        assert info.uncommitted_count == 1


class TestTheExemptionStaysNarrow:
    """
    It drops ONE thing: untracked files under one directory.

    An exemption that swallowed more would be worse than the defect, because the check it
    weakens is the one asserting that a certificate names the commit which produced it.
    """

    def test_a_modified_tracked_file_in_the_reports_dir_still_counts(self, scripted):
        """A committed certificate someone edited is a real change, not an artifact."""
        scripted(f' M {REPORTS_DIR}/signal_feed_report_1.4.0_2026-08-26_203236.json')

        info = get_git_info(ignore_untracked_under=REPORTS_DIR)

        assert info.dirty is True
        assert info.uncommitted_count == 1

    def test_an_untracked_file_elsewhere_still_counts(self, scripted):
        """A new module that is not committed IS code the recorded commit does not have."""
        scripted('?? python/framework/signal_data/transport/signal_new_source.py')

        info = get_git_info(ignore_untracked_under=REPORTS_DIR)

        assert info.dirty is True
        assert info.uncommitted_count == 1

    def test_a_sibling_directory_sharing_the_prefix_is_not_exempt(self, scripted):
        """`reports_archive/` must not ride along on `reports/`."""
        scripted('?? tests/live_signal_feed/reports_archive/old.json')

        info = get_git_info(ignore_untracked_under=REPORTS_DIR)

        assert info.dirty is True

    def test_the_artifact_is_dropped_while_real_changes_survive_beside_it(self, scripted):
        """The mixed case is the realistic one: a run writing while work is in progress."""
        scripted(
            f'?? {REPORTS_DIR}/signal_feed_report_dev_2026-08-28_224049.json\n'
            ' M python/framework/signal_data/transport/signal_stream_source.py\n'
            '?? python/cli/signal_mock_stream_cli.py')

        info = get_git_info(ignore_untracked_under=REPORTS_DIR)

        assert info.dirty is True
        assert info.uncommitted_count == 2
