"""
FiniexTestingIDE - Release Certificate Check (#640)

A family's own check judges its newest report alone, so a report from another version or other code
stays green. These hold the check that asks once, before the tag, whether all four describe this
release and this code: every way one of them can fail to, and that only reports may change since.

The git answers are stood in for except in the last class, which asks a throwaway repository: what is
asserted here is the rule, and this repository's real history would pass or fail by the day.
"""

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import python.framework.reporting.certificates.release_certificate_check as check_module
import python.framework.utils.git_info_utils as git_info_module
from python.framework.reporting.certificates.certificate_index import CertificateIndex
from python.framework.reporting.certificates.release_certificate_check import (
    RELEASE_CERTIFICATE_FAMILIES,
    ReleaseCertificateCheck,
)
from python.framework.types.certificate_types import CertificateStatus
from python.framework.utils.git_info_utils import changed_paths_since

_RELEASE = '1.5.0'
_COMMIT = 'a1b2c3d4'
_NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
_REPORT = 'tests/live_adapters/reports/live_adapter_report_1.5.0_2026-11-14.json'


def _write_certificate(root: Path, family: str, name: str = 'x_report_a.json', **overrides) -> Path:
    """
    Write one certificate that stands, changed by the given fields.

    Args:
        root: The tests tree of this test
        family: The family's directory name
        name: The file name
        overrides: Fields to replace; a value of None removes the field

    Returns:
        The file's path
    """
    body = {
        'release_version': _RELEASE,
        'overall_status': 'PASSED',
        'git_commit': _COMMIT,
        'git_dirty': False,
        'timestamp': '2026-11-14T10:00:00+00:00',
        'valid_until': '2027-02-12T10:00:00+00:00',
        'observed': {'phases': [{'phase': 'real_orders', 'dry_run': False}]},
    }
    body.update(overrides)
    body = {key: value for key, value in body.items() if value is not None}
    path = root / family / 'reports' / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding='utf-8')
    return path


@pytest.fixture
def root(tmp_path, monkeypatch) -> Path:
    """A tests tree with four certificates that stand, on a clean tree where nothing changed."""
    for family in RELEASE_CERTIFICATE_FAMILIES:
        _write_certificate(tmp_path, family)
    monkeypatch.setattr(check_module, 'get_git_commit', lambda: _COMMIT)
    monkeypatch.setattr(check_module, 'get_git_info',
                        lambda: SimpleNamespace(dirty=False, uncommitted_count=0))
    monkeypatch.setattr(check_module, 'changed_paths_since', lambda commit: [])
    return tmp_path


def _problems(root: Path, family: str) -> list:
    """
    The problems the check names for one family.

    Args:
        root: The tests tree of this test
        family: The family's directory name

    Returns:
        Its verdict's problems
    """
    result = ReleaseCertificateCheck(root, now=_NOW).run(_RELEASE)
    return next(v for v in result.verdicts if v.family == family).problems


class TestFourThatStandPass:

    def test_the_release_may_be_tagged(self, root):
        result = ReleaseCertificateCheck(root, now=_NOW).run(_RELEASE)
        assert result.passed()
        assert [v.family for v in result.verdicts] == list(RELEASE_CERTIFICATE_FAMILIES)
        assert all(v.status is CertificateStatus.PASSED for v in result.verdicts)

    def test_it_says_so(self, root, capsys):
        check = ReleaseCertificateCheck(root, now=_NOW)
        check.print_result(check.run(_RELEASE))
        printed = capsys.readouterr().out
        assert 'may be tagged' in printed
        assert 'REFUSED' not in printed

    def test_the_families_are_directories_of_the_tests_tree(self):
        for family in RELEASE_CERTIFICATE_FAMILIES:
            assert Path('tests', family).is_dir(), family


class TestEachCertificateIsHeldToTheRelease:

    @pytest.mark.parametrize('overrides, word', [
        ({'release_version': 'dev'}, 'names dev'),
        ({'release_version': None}, 'names None'),
        ({'overall_status': 'FAILED'}, 'verdict is FAILED'),
        ({'overall_status': 'MAYBE'}, 'unknown'),
        ({'overall_status': None}, 'unknown'),
        ({'git_dirty': True}, 'clean tree'),
        ({'git_dirty': None}, 'clean tree'),
        ({'valid_until': '2026-11-01T00:00:00+00:00'}, 'expired'),
        ({'valid_until': None}, 'no validity'),
        ({'git_commit': None}, 'no commit'),
    ])
    def test_it_is_refused_and_named(self, root, overrides, word):
        _write_certificate(root, 'security_cert', **overrides)
        problems = _problems(root, 'security_cert')
        assert any(word in problem for problem in problems), problems
        assert not ReleaseCertificateCheck(root, now=_NOW).run(_RELEASE).passed()

    def test_a_family_with_no_certificate_is_refused(self, root):
        (root / 'security_cert' / 'reports' / 'x_report_a.json').unlink()
        assert _problems(root, 'security_cert') == ['It has no certificate.']

    def test_one_that_cannot_be_read_is_refused(self, root):
        (root / 'security_cert' / 'reports' / 'x_report_a.json').write_text('{', encoding='utf-8')
        assert any('cannot be read' in p for p in _problems(root, 'security_cert'))

    def test_the_newest_counts_not_any_that_stands(self, root):
        _write_certificate(root, 'security_cert', name='x_report_b.json', release_version='dev',
                           timestamp='2026-11-14T11:00:00+00:00')
        assert any('names dev' in p for p in _problems(root, 'security_cert'))

    def test_a_live_adapter_run_with_no_real_order_proves_too_little(self, root):
        _write_certificate(root, 'live_adapters',
                           observed={'phases': [{'phase': 'validate_only', 'dry_run': True}]})
        assert any('real-order' in p for p in _problems(root, 'live_adapters'))

    def test_the_other_families_observe_no_phases(self, root):
        _write_certificate(root, 'live_field_study', observed=None)
        assert _problems(root, 'live_field_study') == []


class TestTheCodeIsTheCodeItWasTakenOn:

    def test_reports_committed_since_change_nothing(self, root, monkeypatch):
        monkeypatch.setattr(check_module, 'changed_paths_since', lambda commit: [_REPORT])
        result = ReleaseCertificateCheck(root, now=_NOW).run(_RELEASE)
        assert result.passed()
        assert all(v.changed_outside_reports == [] for v in result.verdicts)

    @pytest.mark.parametrize('path', [
        'python/framework/trading_env/live/live_trade_executor.py',
        'tests/live_adapters/conftest.py',
        'docs/reports/a_report.md',
    ])
    def test_anything_else_is_refused_and_named(self, root, monkeypatch, path):
        monkeypatch.setattr(check_module, 'changed_paths_since', lambda commit: [_REPORT, path])
        problems = _problems(root, 'live_field_study')
        assert len(problems) == 1
        assert '1 file(s)' in problems[0] and path in problems[0]

    def test_a_commit_the_history_does_not_know_is_refused(self, root, monkeypatch):
        monkeypatch.setattr(check_module, 'changed_paths_since', lambda commit: None)
        assert any('amend' in p for p in _problems(root, 'live_signal_feed'))


class TestTheTreeItself:

    def test_uncommitted_changes_are_refused(self, root, monkeypatch):
        monkeypatch.setattr(check_module, 'get_git_info',
                            lambda: SimpleNamespace(dirty=True, uncommitted_count=2))
        result = ReleaseCertificateCheck(root, now=_NOW).run(_RELEASE)
        assert not result.passed()
        assert '2 uncommitted' in result.tree_problem

    def test_a_tree_git_cannot_read_is_refused(self, root, monkeypatch):
        monkeypatch.setattr(check_module, 'get_git_info', lambda: None)
        assert not ReleaseCertificateCheck(root, now=_NOW).run(_RELEASE).passed()

    def test_a_refusal_says_not_to_tag(self, root, monkeypatch, capsys):
        monkeypatch.setattr(check_module, 'get_git_info', lambda: None)
        check = ReleaseCertificateCheck(root, now=_NOW)
        check.print_result(check.run(_RELEASE))
        assert 'REFUSED' in capsys.readouterr().out


class TestTheIndexKnowsWhatLeft:

    def test_a_removed_certificate_is_not_served(self, tmp_path):
        """A withdrawn report leaves no newer file behind, and the index read as current."""
        older = _write_certificate(tmp_path, 'security_cert', name='x_report_a.json',
                                   timestamp='2026-10-01T00:00:00+00:00')
        newer = _write_certificate(tmp_path, 'security_cert', name='x_report_b.json',
                                   timestamp='2026-11-01T00:00:00+00:00')
        index = CertificateIndex(tmp_path)
        assert index.find_latest('security_cert') == newer
        newer.unlink()
        assert index.find_latest('security_cert') == older
        older.unlink()
        assert index.find_latest('security_cert') is None


class TestChangedPathsSince:

    @pytest.fixture
    def repository(self, tmp_path, monkeypatch) -> dict:
        """A throwaway repository of two commits, standing in for this one."""
        def git(*args):
            return subprocess.run(['git', '-C', str(tmp_path), *args], check=True,
                                  capture_output=True, text=True).stdout.strip()

        git('init', '-q')
        git('config', 'user.email', 'a@example.invalid')
        git('config', 'user.name', 'a')
        (tmp_path / 'a.txt').write_text('a', encoding='utf-8')
        git('add', '.')
        git('commit', '-q', '-m', 'first')
        first = git('rev-parse', '--short', 'HEAD')
        (tmp_path / 'b.txt').write_text('b', encoding='utf-8')
        git('add', '.')
        git('commit', '-q', '-m', 'second')
        monkeypatch.setattr(git_info_module, 'get_framework_root', lambda: str(tmp_path))
        return {'first': first, 'head': git('rev-parse', '--short', 'HEAD')}

    def test_it_names_what_changed(self, repository):
        assert changed_paths_since(repository['first']) == ['b.txt']

    def test_nothing_changed_is_an_empty_list(self, repository):
        assert changed_paths_since(repository['head']) == []

    def test_an_unknown_commit_is_no_answer(self, repository):
        assert changed_paths_since('deadbeef') is None
