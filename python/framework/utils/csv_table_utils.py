"""
FiniexTestingIDE - CSV Table Utils
One table written as CSV — a header row, then one row per record — the way every table a run or a
sweep keeps is written.
"""

import csv
from pathlib import Path
from typing import Any, Dict, Iterable, List

from python.framework.utils.file_utils import create_directory


def write_csv_table(path: Path, columns: List[str], rows: Iterable[Dict[str, Any]],
                    ignore_extra_columns: bool = False) -> Path:
    """
    Write one table as CSV, replacing what was there.

    Args:
        path: The file; its directory is created
        columns: The column names, in order
        rows: One mapping of column to value per row
        ignore_extra_columns: Leave out a row's keys that are no column, instead of refusing
            the row

    Returns:
        The written file
    """
    create_directory(path.parent, holds=f'the table {path.name}')
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns,
                                extrasaction='ignore' if ignore_extra_columns else 'raise')
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return path
