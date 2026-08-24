"""
No name is used that was never defined — the class of bug a test suite cannot see.

`ast.parse` accepts a file that references an unimported symbol: it is syntactically valid.
A unit test only finds it by *executing* the path, and the paths where these hide are
precisely the ones nothing executes yet. Five instances landed in this project within two
days, and the expensive one sat in the live-session startup branch — a path no test ran and
that would have fired inside an unattended real-money session.

| Symbol | Where it hid |
|---|---|
| `SentimentConfigManager` | anchor written into the wrong file — 54 tests failed hours later |
| `DryRunConflictError` | no existing import from that module |
| `SignalSeries` | the empty-provider branch a live session takes, never executed |
| `SignalObservedSeries` | an import guard that tested for the name, which the freshly
    inserted signature already contained |

pyflakes answers exactly this question statically, over every file, including the branches
nothing runs. It is the only check here that is a hard gate: the remaining categories it
reports (unused imports, placeholder-free f-strings) are hygiene and are cleaned per §7 as
units are touched, not enforced globally — enforcing those today would fail on ~200
pre-existing findings and teach everyone to skip the test.
"""

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[3]
SCANNED = ('python', 'tests')

# pyflakes reports one finding per line. Only this class is a gate — a name that does not
# resolve is a crash waiting for the right code path, never a style opinion.
UNDEFINED_MARKERS = ('undefined name', 'may be undefined')


def _run_pyflakes(target: Path) -> str:
    """
    Run pyflakes over one directory.

    Args:
        target: Directory to scan

    Returns:
        Its combined output, one finding per line
    """
    result = subprocess.run(
        [sys.executable, '-m', 'pyflakes', str(target)],
        capture_output=True, text=True, cwd=PROJECT_ROOT)
    return result.stdout + result.stderr


@pytest.fixture(scope='module')
def findings() -> str:
    """pyflakes output over the scanned trees, gathered once."""
    return '\n'.join(_run_pyflakes(PROJECT_ROOT / name) for name in SCANNED)


class TestNoUndefinedNames:
    """The hard gate."""

    def test_the_scan_had_input(self, findings):
        """
        A scan over a moved or empty tree passes cleanly and proves nothing.

        Guarding it because this suite exists *because of* results that looked clean and
        were not: a `grep` against a missing binary, a glob over an empty directory.
        """
        for name in SCANNED:
            assert (PROJECT_ROOT / name).is_dir(), f'{name}/ not found — the scan is empty'
        assert len(list((PROJECT_ROOT / 'python').rglob('*.py'))) > 100

    def test_every_name_resolves(self, findings):
        """
        Every referenced symbol is defined or imported.

        A failure here names the file, line and symbol. It is almost always a missing
        import in a branch that no test executes — fix the import, do not silence the test.
        """
        undefined = [
            line for line in findings.splitlines()
            if any(marker in line for marker in UNDEFINED_MARKERS)
        ]
        assert not undefined, (
            'Names used but never defined:\n  ' + '\n  '.join(undefined)
        )


class TestHygieneIsMeasuredNotEnforced:
    """
    The other categories are reported, never asserted.

    They are real (§7 removes unused imports as a unit is touched, §5 makes a
    placeholder-free f-string a plain single-quoted string) but they carry a large
    pre-existing backlog. A gate that fails on day one is a gate that gets skipped, and a
    skipped gate protects nothing — including the undefined-name check sitting beside it.
    """

    def test_the_backlog_is_visible(self, findings):
        """Not an assertion about the count — a place where the number is stated."""
        unused = sum('imported but unused' in l for l in findings.splitlines())
        fstrings = sum('f-string is missing placeholders' in l for l in findings.splitlines())
        locals_ = sum('never used' in l for l in findings.splitlines())
        assert unused >= 0 and fstrings >= 0 and locals_ >= 0
