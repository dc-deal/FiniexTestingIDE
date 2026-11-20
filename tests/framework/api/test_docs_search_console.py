"""
The terminal search over the whole documentation tree, and the extra searches an installation
sets up for itself.

The served search reads one flat folder; the terminal search reads every level of the tree with
the same ranking, then runs each configured extra command with the search term appended.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from python.cli.docs_search_cli import DocsSearchCli
from python.framework.docs_search.docs_search_console import DocsSearchConsole
from python.framework.docs_search.docs_search_index import DocsSearchIndex

REPO_ROOT = Path(__file__).resolve().parents[3]

_FILLER = 'This paragraph only exists so the passage is long enough to be ranked at all.'


def _write(root: Path, relative: str, body: str) -> None:
    """
    Write one markdown document below a root, creating its folder.

    Args:
        root: The documentation root of the case
        relative: The document's path below the root
        body: Its text below a level-one heading
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'# {Path(relative).stem}\n\n{body} {_FILLER}\n', encoding='utf-8')


class TestTheWholeTreeIsSearchable:
    """A document in a sub-folder is found, named by its path below the root."""

    def test_a_nested_document_is_found_by_its_path(self, tmp_path: Path):
        _write(tmp_path, 'top.md', 'Nothing about the subject here.')
        _write(tmp_path, 'architecture/deep.md', 'The quokka ledger explains itself.')
        found, _unknown = DocsSearchIndex(tmp_path, recursive=True).search('quokka', 5)
        assert [passage.document for _score, passage in found] == ['architecture/deep']

    def test_the_served_set_still_reads_one_level(self, tmp_path: Path):
        _write(tmp_path, 'top.md', 'Nothing about the subject here.')
        _write(tmp_path, 'architecture/deep.md', 'The quokka ledger explains itself.')
        found, unknown = DocsSearchIndex(tmp_path).search('quokka', 5)
        assert found == []
        assert unknown == ['quokka']

    def test_a_nested_document_reads_back_by_its_name(self, tmp_path: Path):
        _write(tmp_path, 'architecture/deep.md', 'The quokka ledger explains itself.')
        index = DocsSearchIndex(tmp_path, recursive=True)
        assert 'quokka' in index.read_document('architecture/deep')


class TestTheExtraSearches:
    """Each configured command runs after the documentation, with the term as its last argument."""

    def test_each_command_receives_the_term_after_the_documentation(self, tmp_path: Path, capfd):
        _write(tmp_path, 'top.md', 'The quokka ledger explains itself.')
        echo = [sys.executable, '-c', 'import sys; print("extra got:", sys.argv[-1])']
        exit_code = DocsSearchConsole(tmp_path, [echo]).run('quokka ledger', 3)
        out = capfd.readouterr().out
        assert exit_code == 0
        assert 'extra got: quokka ledger' in out
        assert out.index('DOCS') < out.index('extra got')

    def test_a_command_that_cannot_start_is_reported_not_raised(self, tmp_path: Path, capfd):
        _write(tmp_path, 'top.md', 'The quokka ledger explains itself.')
        exit_code = DocsSearchConsole(
            tmp_path, [['no-such-search-command-for-this-test']]).run('quokka', 3)
        assert exit_code == 0
        assert 'not run' in capfd.readouterr().out

    def test_a_fresh_clone_sets_up_no_extra_search(self):
        tracked = json.loads((REPO_ROOT / 'configs' / 'app_config.json').read_text())
        assert tracked['docs_search']['extra_commands'] == []


class TestOneFolderAlone:
    """A folder searched with --root is read one level deep and runs no extra search of its own."""

    def test_a_root_is_searched_one_level_deep(self, tmp_path: Path, capsys):
        _write(tmp_path, 'top.md', 'The quokka ledger explains itself.')
        _write(tmp_path, 'closed/deep.md', 'Another quokka ledger lives one level down.')
        exit_code = DocsSearchConsole(tmp_path, []).run_folder('quokka', 5)
        out = capsys.readouterr().out
        assert exit_code == 0
        assert 'top.md' in out
        assert 'deep.md' not in out

    def test_a_folder_alone_runs_no_extra_search(self, tmp_path: Path, capfd):
        _write(tmp_path, 'top.md', 'The quokka ledger explains itself.')
        echo = [sys.executable, '-c', 'print("extra ran")']
        DocsSearchConsole(tmp_path, [echo]).run_folder('quokka', 3)
        assert 'extra ran' not in capfd.readouterr().out

    def test_the_cli_searches_a_given_root_alone(self, tmp_path: Path, capsys):
        _write(tmp_path, 'only_here.md', 'The quokka ledger explains itself.')
        exit_code = DocsSearchCli().cmd_search('quokka', 3, root=tmp_path)
        out = capsys.readouterr().out
        assert exit_code == 0
        assert 'only_here.md' in out
        assert 'SEARCH' not in out

    def test_an_extra_search_by_root_is_announced_by_its_folder(self, tmp_path: Path, capfd):
        _write(tmp_path, 'top.md', 'The quokka ledger explains itself.')
        command = [sys.executable, '-c', 'print("collection searched")',
                   '--root', str(tmp_path / 'my_collection')]
        DocsSearchConsole(tmp_path, [command]).run('quokka', 3)
        out = capfd.readouterr().out
        assert 'MY_COLLECTION' in out
        assert out.index('MY_COLLECTION') < out.index('collection searched')


class TestASnapshotSaysHowOldItIs:
    """
    A folder kept current from elsewhere — a snapshot — prints its age on its count line, so a copy
    that stopped being kept current shows where it is read (`docs_search.snapshot_stamps`).
    """

    def _snapshot(self, root: Path, fetched_at: datetime) -> dict:
        _write(root, 'top.md', 'The quokka ledger explains itself.')
        (root / 'meta.json').write_text(
            json.dumps({'fetched_at': fetched_at.strftime('%Y-%m-%dT%H:%M:%SZ')}), encoding='utf-8')
        return {str(root): 'meta.json::fetched_at'}

    def test_a_fresh_snapshot_prints_its_age(self, tmp_path: Path, capsys):
        stamps = self._snapshot(tmp_path, datetime.now(timezone.utc) - timedelta(hours=2))

        DocsSearchConsole(tmp_path, [], stamps).run_folder('quokka', 3)

        out = capsys.readouterr().out
        assert '(snapshot 2h' in out
        assert 'stopped' not in out

    def test_a_snapshot_older_than_a_day_is_flagged(self, tmp_path: Path, capsys):
        stamps = self._snapshot(tmp_path, datetime.now(timezone.utc) - timedelta(hours=30))

        DocsSearchConsole(tmp_path, [], stamps).run_folder('quokka', 3)

        assert 'it has stopped being kept current' in capsys.readouterr().out

    def test_a_folder_that_is_no_snapshot_prints_no_age(self, tmp_path: Path, capsys):
        _write(tmp_path, 'top.md', 'The quokka ledger explains itself.')

        DocsSearchConsole(tmp_path, [], {}).run_folder('quokka', 3)

        assert 'snapshot' not in capsys.readouterr().out

    def test_an_unreadable_stamp_is_said_not_guessed(self, tmp_path: Path, capsys):
        _write(tmp_path, 'top.md', 'The quokka ledger explains itself.')

        DocsSearchConsole(tmp_path, [], {str(tmp_path): 'meta.json::fetched_at'}).run_folder('quokka', 3)

        assert 'snapshot age unknown' in capsys.readouterr().out
