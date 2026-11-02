"""
Searches the documentation tree from a terminal, and whatever else a maintainer has set up.

The API's search reads only the served set, because it answers readers who cannot open the
repository. A maintainer at the terminal reads everything: every document under `docs/`, ranked
by the same search, followed by the output of each further search command configured for this
installation (`app_config.json::docs_search.extra_commands`). Collections that cannot live in the
repository are searched there, so the repository never has to name them.
"""
import os
import subprocess
from pathlib import Path
from typing import List

from python.framework.docs_search.docs_search_index import DocsSearchIndex


class DocsSearchConsole:
    """
    Prints the documentation passages that answer a search term, then runs the extra searches.

    Args:
        docs_root: The documentation tree
        extra_commands: Further search commands, each run with the term appended
    """

    # One search prints several blocks under each other, and several searches scroll into one
    # another. Two weights of rule keep them apart: the heavy one opens a search, the light one
    # separates the corpora within it.
    _HEAVY = '\u2550' * 78
    _LIGHT = '\u2500' * 78

    def __init__(self, docs_root: Path, extra_commands: List[List[str]]):
        self._docs_root = docs_root
        self._extra_commands = extra_commands

    def run(self, query: str, hits: int) -> int:
        """
        Search the documentation, then every extra command, and print what each found.

        Args:
            query: The search term
            hits: How many documentation passages to show at most

        Returns:
            Process exit code — 0 also when nothing matched, because an empty answer is an answer
        """
        print(f'\n{self._HEAVY}')
        print(f'  SEARCH  {query!r}')
        print(self._HEAVY)
        self._print_documentation_hits(query, hits)
        for command in self._extra_commands:
            self._run_extra_search(command, query)
        # Printed once here rather than by each command, which would repeat it per corpus.
        print(f'\n{self._LIGHT}')
        print('  open <path>:<line> in the editor \u00b7 a book page in full: '
              '_books/tools/books_search.py --open <key> <page>')
        return 0

    def _print_documentation_hits(self, query: str, hits: int) -> None:
        """
        Rank every document of the tree and print the best passages, each with a line to open.

        Args:
            query: The search term
            hits: How many passages to show at most
        """
        index = DocsSearchIndex(self._docs_root, recursive=True)
        found, unknown = index.search(query, hits)
        print(f'\n{self._LIGHT}')
        print(f'  DOCS \u2014 {index.get_passage_count()} passages, best {len(found)}\n')
        for score, passage in found:
            # Relative to where the search was started — in the workspace that is the path an
            # editor's terminal turns into a link.
            location = Path(os.path.relpath(self._docs_root / f'{passage.document}.md')).as_posix()
            print(f'  {score:5.1f}  {location}:{passage.line}  § {passage.heading}')
            print(f'         {index.snippet_for(passage, query)}')
        if not found:
            print('  nothing matched')
        if unknown:
            print(f'\n  no document carries: {", ".join(unknown)}')

    def _run_extra_search(self, command: List[str], query: str) -> None:
        """
        Run one configured search command with the term appended; its output goes straight to the
        terminal.

        A command that cannot be started is reported and skipped — the documentation hits above it
        are still the answer, and a missing private tool must not turn a search into a crash.

        Args:
            command: The command as its argument list
            query: The search term
        """
        # Flushed, because the command writes to the terminal on its own: anything this process
        # still holds in its buffer would otherwise appear AFTER the command's output.
        corpus = command[command.index('--corpus') + 1] if '--corpus' in command else ' '.join(command)
        print(f'\n{self._LIGHT}')
        print(f'  {corpus.upper()}\n', flush=True)
        try:
            subprocess.run([*command, query], check=False)
        except OSError as error:
            print(f'  not run: {error}')
