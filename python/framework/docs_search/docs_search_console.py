"""
Searches the documentation tree from a terminal, and whatever else a maintainer has set up.

The API's search reads only the served set, because it answers readers who cannot open the
repository. A maintainer at the terminal reads everything: every document under `docs/`, ranked
by the same search, followed by the output of each further search command configured for this
installation (`app_config.json::docs_search.extra_commands`). Collections that cannot live in the
repository are searched there, so the repository never has to name them. Such a command can be this
search itself, pointed at one folder (`--root`): it then searches that folder alone, one level deep,
and runs no extra search of its own. Where the folder is a snapshot kept current from elsewhere, its
count line says how old the snapshot is, so a copy that stopped being kept current shows where it is
read (`docs_search.snapshot_stamps`).
"""
import json
import os
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

from python.framework.docs_search.docs_search_index import DocsSearchIndex
from python.framework.utils.time_utils import format_duration, parse_iso_datetime

# A snapshot kept current every few hours that is older than this has stopped being kept current
_SNAPSHOT_STALE_AFTER = timedelta(days=1)


class DocsSearchConsole:
    """
    Prints the documentation passages that answer a search term, then runs the extra searches.

    Args:
        docs_root: The documentation tree
        extra_commands: Further search commands, each run with the term appended
        snapshot_stamps: Folder → '<file>::<key>' where a snapshot folder records when it was fetched
    """

    # One search prints several blocks under each other, and several searches scroll into one
    # another. Two weights of rule keep them apart: the heavy one opens a search, the light one
    # separates the corpora within it.
    _HEAVY = '\u2550' * 78
    _LIGHT = '\u2500' * 78

    def __init__(self, docs_root: Path, extra_commands: List[List[str]],
                 snapshot_stamps: Optional[Dict[str, str]] = None):
        self._docs_root = docs_root
        self._extra_commands = extra_commands
        self._snapshot_stamps = snapshot_stamps or {}

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
        self._print_hits(query, hits, recursive=True, heading='DOCS')
        for command in self._extra_commands:
            self._run_extra_search(command, query)
        # Printed once here rather than by each command, which would repeat it per corpus.
        print(f'\n{self._LIGHT}')
        print('  open <path>:<line> in the editor')
        return 0

    def run_folder(self, query: str, hits: int) -> int:
        """
        Search one folder alone, one level deep, and print what it found.

        This is the form an extra search takes, so it prints no rule and no label of its own (the
        search that runs it has printed both) and runs no extra search itself.

        Args:
            query: The search term
            hits: How many passages to show at most

        Returns:
            Process exit code, 0 also when nothing matched
        """
        self._print_hits(query, hits, recursive=False, heading=None)
        return 0

    def _print_hits(self, query: str, hits: int, recursive: bool, heading: Optional[str]) -> None:
        """
        Rank every document below the root and print the best passages, each with a line to open.

        Args:
            query: The search term
            hits: How many passages to show at most
            recursive: Whether documents in sub-folders are read too
            heading: The label printed under a rule above the hits; None for a folder searched alone,
                whose count line names the folder instead
        """
        index = DocsSearchIndex(self._docs_root, recursive=recursive)
        found, unknown = index.search(query, hits)
        if heading is not None:
            print(f'\n{self._LIGHT}')
            print(f'  {heading} \u2014 {index.get_passage_count()} passages, best {len(found)}\n')
        else:
            folder = Path(os.path.relpath(self._docs_root)).as_posix()
            print(f'  {index.get_passage_count()} passages in {folder}, best {len(found)}'
                  f'{self._snapshot_age()}\n')
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

    def _snapshot_age(self) -> str:
        """
        How old the searched folder's snapshot is, where the folder is declared one.

        The age is the wall clock against the instant the snapshot recorded — a point in time
        compared with now, the only clock both share.

        Returns:
            The suffix for the count line; '' where the folder is no declared snapshot
        """
        stamp = next((value for folder, value in self._snapshot_stamps.items()
                      if Path(folder).resolve() == self._docs_root.resolve()), None)
        if stamp is None:
            return ''
        file_name, _, key = stamp.partition('::')
        try:
            fetched_at = parse_iso_datetime(
                json.loads((self._docs_root / file_name).read_text(encoding='utf-8'))[key])
        except (OSError, ValueError, KeyError, TypeError) as error:
            return f'   (snapshot age unknown: {error})'
        age = datetime.now(timezone.utc) - fetched_at
        text = f'snapshot {format_duration(max(age.total_seconds(), 0.0))} old'
        if age > _SNAPSHOT_STALE_AFTER:
            return f'   ⚠️  ({text} — it has stopped being kept current)'
        return f'   ({text})'

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
        corpus = self._label_of(command)
        print(f'\n{self._LIGHT}')
        print(f'  {corpus.upper()}\n', flush=True)
        try:
            subprocess.run([*command, query], check=False)
        except OSError as error:
            print(f'  not run: {error}')

    @staticmethod
    def _label_of(command: List[str]) -> str:
        """
        The name an extra search is announced under: its `--corpus`, else the folder of its `--root`,
        else the command itself.

        Args:
            command: The command as its argument list

        Returns:
            The label, before upper-casing
        """
        for flag in ('--corpus', '--root'):
            if flag in command and command.index(flag) + 1 < len(command):
                value = command[command.index(flag) + 1]
                return Path(value).name if flag == '--root' else value
        return ' '.join(command)
