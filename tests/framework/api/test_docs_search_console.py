"""
The terminal search over the whole documentation tree, and the extra searches an installation
sets up for itself.

The served search reads one flat folder; the terminal search reads every level of the tree with
the same ranking, then runs each configured extra command with the search term appended.
"""
import json
import sys
from pathlib import Path

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
        assert out.index('Documentation') < out.index('extra got')

    def test_a_command_that_cannot_start_is_reported_not_raised(self, tmp_path: Path, capfd):
        _write(tmp_path, 'top.md', 'The quokka ledger explains itself.')
        exit_code = DocsSearchConsole(
            tmp_path, [['no-such-search-command-for-this-test']]).run('quokka', 3)
        assert exit_code == 0
        assert 'not run' in capfd.readouterr().out

    def test_a_fresh_clone_sets_up_no_extra_search(self):
        tracked = json.loads((REPO_ROOT / 'configs' / 'app_config.json').read_text())
        assert tracked['docs_search']['extra_commands'] == []
