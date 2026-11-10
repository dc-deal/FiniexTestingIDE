"""
The verdict of an A/A (#512): whether two backtests of one window, on the release the sessions ran
and on the current tree, computed the same.

The verdict rules make the A/A a merge blocker, so it may only say IDENTICAL where the two runs read
the same inputs and ran the code they claim: the release's run on the sessions' own commit, the
current tree's run on a commit, both on the same frozen broker document, and no bar file rendered
between the two. Where any of that cannot be shown the window is not checked, with why — never a
pass, and never a difference either.
"""

from typing import List, Optional

from python.framework.types.api.divergence_report_types import (
    AaReport,
    AaVerdict,
    AaWindowResult,
    RenderStamp,
)

# The shortest prefix two commit hashes must share to name one commit
_COMMIT_PREFIX = 7


def judge_aa_window(window: AaWindowResult, session_commit: str) -> AaWindowResult:
    """
    One window's verdict.

    Args:
        window: The window with both runs and the differences found
        session_commit: The framework commit the deployment's sessions ran

    Returns:
        The window with its verdict, and why where it is not checked
    """
    reasons = [f'{run.stand.value}: {run.failed_because}'
               for run in (window.release, window.current) if run.failed_because]
    if not reasons:
        reasons = _input_reasons(window, session_commit)
    if reasons:
        return window.model_copy(update={'verdict': AaVerdict.NOT_CHECKED,
                                         'not_checked_because': '; '.join(reasons)})
    verdict = AaVerdict.DIFFERENT if window.difference_count else AaVerdict.IDENTICAL
    return window.model_copy(update={'verdict': verdict, 'not_checked_because': ''})


def aa_exit_code(report: AaReport) -> int:
    """
    The A/A's exit code.

    Args:
        report: The A/A

    Returns:
        0 every window identical · 3 a window differs · 2 refused, or a window not checked
    """
    verdicts = [window.verdict for window in report.windows]
    if AaVerdict.DIFFERENT in verdicts:
        return 3
    if report.refused_because or not verdicts or AaVerdict.NOT_CHECKED in verdicts:
        return 2
    return 0


def same_commit(left: Optional[str], right: Optional[str]) -> bool:
    """
    Whether two commit hashes, short or long, name one commit.

    Args:
        left: One hash
        right: The other

    Returns:
        True where both are known and one is a prefix of the other, at least seven characters long
    """
    if not left or not right or min(len(left), len(right)) < _COMMIT_PREFIX:
        return False
    return left.startswith(right) or right.startswith(left)


def _input_reasons(window: AaWindowResult, session_commit: str) -> List[str]:
    """
    Why the two runs cannot be held against each other, where they cannot.

    Args:
        window: The window with both runs
        session_commit: The framework commit the deployment's sessions ran

    Returns:
        The reasons; empty where the two read the same inputs on the code they claim
    """
    release, current = window.release, window.current
    reasons: List[str] = []
    if not same_commit(release.framework_commit, session_commit) or release.framework_dirty:
        reasons.append(
            f'the release\'s backtest ran {release.framework_commit or "an unknown commit"}'
            f'{" with uncommitted changes" if release.framework_dirty else ""}, not the sessions\' '
            f'{session_commit or "unknown commit"}')
    if current.framework_dirty is not False:
        reasons.append('the current tree\'s backtest ran no single commit (uncommitted changes, '
                       'or no header to say)')
    if not release.broker_config_id or release.broker_config_id != current.broker_config_id:
        reasons.append(f'the two runs froze different broker documents '
                       f'({release.broker_config_id or "none"}, '
                       f'{current.broker_config_id or "none"})')
    reasons.extend(_render_reasons(window.render_stamps_before, window.render_stamps_after))
    for run in (release, current):
        if run.order_events is None or run.trades is None:
            reasons.append(f'{run.stand.value}: its order events or its trades could not be read')
    return reasons


def _render_reasons(before: List[RenderStamp], after: List[RenderStamp]) -> List[str]:
    """
    Why the bar files may have changed between the two runs, where they may.

    Args:
        before: The symbol's bar files before the first run
        after: The same after the last

    Returns:
        The reasons; empty where every file kept its render stamp
    """
    if not before:
        return ['no bar file of the symbol is indexed, so nobody can say the two runs read the '
                'same bars']
    unknown = [stamp.timeframe for stamp in before + after if stamp.rendered_at is None]
    if unknown:
        return [f'the bar files of {", ".join(sorted(set(unknown)))} carry no render stamp']
    if before != after:
        moved = [f'{b.timeframe} {b.rendered_at} → {a.rendered_at}'
                 for b, a in zip(before, after) if b != a]
        return ['a bar file was rendered between the two runs: '
                + (', '.join(moved) or 'the indexed files changed')]
    return []
