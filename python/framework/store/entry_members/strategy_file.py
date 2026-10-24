"""
FiniexTestingIDE - Strategy File (#621)
One file a strategy keeps with its run, as the decision logic's base class hands it out.
"""

from typing import Any, Optional

from python.framework.store.entry_members.strategy_file_writer import StrategyFileWriter


class StrategyFile:
    """One file of the strategy's own folder in its run."""

    def __init__(self, name: str, writer: Optional[StrategyFileWriter]):
        """
        Args:
            name: The file's name inside the folder
            writer: The run's strategy file writer; None where the framework wired none, such as
                a logic built on its own in a test
        """
        self._name = name
        self._writer = writer

    def write_text(self, text: str) -> bool:
        """
        Write the file whole, replacing what was there.

        Args:
            text: The full content

        Returns:
            True when the file was written
        """
        return self._writer is not None and self._writer.write_text(self._name, text)

    def write_json(self, data: Any) -> bool:
        """
        Write the file as indented JSON, replacing what was there.

        Args:
            data: What to write; it has to be JSON

        Returns:
            True when the file was written
        """
        return self._writer is not None and self._writer.write_json(self._name, data)
