"""
FiniexTestingIDE - Strategy File Writer (#621)
Writes a strategy's own files into its run, in the folder the run store declares for them.

A strategy file never ends a run: a name that would reach out of the folder, an unwritable disk
or a value that is no JSON is said once on the run's own channel, and the write answers False.
The strategy never sees a path, so whatever it writes lands where the declaration says.
"""

import json
from typing import Any, Optional, Set

from python.framework.exceptions.store_errors import EntryMemberRefusedError
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.store_member_types import RunEntryMemberId

# The file names a temporary write of the atomic writer takes, so a strategy cannot pick one.
_TEMPORARY_PREFIX = '.'
_TEMPORARY_SUFFIX = '.tmp'


class StrategyFileWriter:
    """A strategy's own files of one run — or of one scenario of a backtest."""

    def __init__(self, writer: Optional[RunEntryWriter], member_id: RunEntryMemberId,
                 logger: AbstractLogger, scenario: Optional[str] = None):
        """
        Args:
            writer: The run's writer; None when the run has no directory to write into
            member_id: The member the files are: a session's or a scenario's
            logger: The run's own channel, where a failed write is said once
            scenario: The scenario the files belong to, in a backtest; None in a session
        """
        self._writer = writer
        self._member_id = member_id
        self._logger = logger
        self._scenario = scenario
        self._said: Set[str] = set()

    def write_text(self, name: str, text: str) -> bool:
        """
        Write one file whole, replacing what was there.

        Args:
            name: The file's name inside the strategy's folder
            text: The full content

        Returns:
            True when the file was written
        """
        refusal = _refusal(name)
        if refusal:
            self._say_once(f'refused:{name}', f"Strategy file '{name}' refused: {refusal} — "
                                              f'nothing written')
            return False
        if self._writer is None:
            self._say_once('no_run_dir', 'Strategy files are not written: the run has no '
                                         'directory to write into')
            return False
        names = {'name': name} if self._scenario is None else {
            'name': name, 'scenario': self._scenario}
        try:
            self._writer.write_strategy_file(self._member_id, text, **names)
        except EntryMemberRefusedError as error:
            self._say_once(f'refused:{name}', f"Strategy file '{name}' refused: {error} — "
                                              f'nothing written')
            return False
        except OSError as error:
            self._say_once('os_error', f"Strategy file '{name}' could not be written ({error}); "
                                       f'the run goes on, and this is said once')
            return False
        except ValueError as error:
            # A character no file name or no UTF-8 text can hold — a NUL, a lone surrogate
            self._say_once(f'not_text:{name}', f"Strategy file '{name}' is not written: its name "
                                               f'or its text cannot be stored ({error})')
            return False
        return True

    def write_json(self, name: str, data: Any) -> bool:
        """
        Write one file as indented JSON.

        Args:
            name: The file's name inside the strategy's folder
            data: What to write; it has to be JSON

        Returns:
            True when the file was written
        """
        try:
            # NaN and Infinity are no JSON; a strict reader refuses a file that holds them
            text = json.dumps(data, indent=2, allow_nan=False)
        except (TypeError, ValueError) as error:
            self._say_once(f'not_json:{name}', f"Strategy file '{name}' is not written: its "
                                               f'content is no JSON ({error})')
            return False
        return self.write_text(name, text)

    def _say_once(self, key: str, message: str) -> None:
        """
        Put one failure on the run's channel, once per kind — a strategy that writes on every
        decision would otherwise repeat it on every decision.

        Args:
            key: What kind of failure it is
            message: What to say
        """
        if key in self._said:
            return
        self._said.add(key)
        self._logger.error(message)


def _refusal(name: str) -> str:
    """
    Why a file name is refused, beyond what a path part may never hold.

    Args:
        name: The name a strategy asked for

    Returns:
        The reason; empty when the name is fine
    """
    if name.startswith(_TEMPORARY_PREFIX):
        return 'a name may not start with a dot — a temporary write takes those names'
    if name.endswith(_TEMPORARY_SUFFIX):
        return f"a name may not end in '{_TEMPORARY_SUFFIX}' — a temporary write takes those names"
    return ''
