"""
Two runs' records held against each other (#641), one kind of record at a time, value by value
and to the last digit. Pure apart from reading the two run directories.

The rule is the A/A's (`aa_comparison_builder.py`): every value both runs write is compared, and a
field only one of them writes is a change of the record's shape — named, not compared. The A/A
compares flat records; this walks each document to its leaves, so a difference names the field it
sits in (`units[0].total_profit`) rather than the record around it. Lists are held position by
position: the same scenario set on the same data writes its records in one order, and an order
event stream is put in scenario and `seq` order first, because its scenarios run side by side.
"""

import csv
import json
from pathlib import Path
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Sequence

from python.framework.behaviour_comparison.compared_records import (
    COMPARED_RECORDS,
    IGNORED_IN_EVERY_RECORD,
    SHAPE_FIELDS,
)
from python.framework.store.entry_members.run_entry_members import RUN_ENTRY_MEMBERS_BY_ID
from python.framework.types.behaviour_comparison_types import (
    ComparedRecord,
    RecordComparison,
    RecordDifference,
)

# The keys a stream's document is laid out under
_HEADER = 'header'
_RECORDS = 'records'
_HEADER_KIND = 'header'


def compare_run_records(old_dir: Path, new_dir: Path,
                        records: Sequence[ComparedRecord] = COMPARED_RECORDS
                        ) -> List[RecordComparison]:
    """
    Every compared kind of record of two runs, held against each other.

    Args:
        old_dir: The first run's directory
        new_dir: The second run's directory
        records: The kinds of record compared; every declared one when omitted

    Returns:
        One comparison per kind of record, in declaration order
    """
    return [compare_record(record, _load(old_dir, record), _load(new_dir, record))
            for record in records]


def compare_record(record: ComparedRecord, old: Optional[Any],
                   new: Optional[Any]) -> RecordComparison:
    """
    One kind of record of two runs, held against each other.

    Args:
        record: The kind of record
        old: The first run's document of it; None where that run wrote none
        new: The second run's, the same way

    Returns:
        What the two agree and disagree on
    """
    comparison = RecordComparison(record=record, present_in_old=old is not None,
                                  present_in_new=new is not None)
    comparison.empty_in_both = (record.records_key is not None
                                and not _records_of(old, record.records_key)
                                and not _records_of(new, record.records_key))
    if old is None or new is None:
        return comparison
    ignored = frozenset(IGNORED_IN_EVERY_RECORD) | frozenset(record.ignored_fields)
    _walk(old, new, '', '', ignored, comparison)
    comparison.old_only_fields = _distinct(comparison.old_only_fields)
    comparison.new_only_fields = _distinct(comparison.new_only_fields)
    return comparison


def disagreeing_paths(comparisons: Sequence[RecordComparison]) -> Dict[str, List[str]]:
    """
    Every folded path two runs disagree on, by the kind of record — what a second run of one
    stand says is not the code's to answer for.

    Args:
        comparisons: The two runs' comparisons

    Returns:
        Each kind of record's member id → its folded paths, values, one-sided fields and schema
        versions alike; a kind they agree on is left out
    """
    found: Dict[str, List[str]] = {}
    for comparison in comparisons:
        paths = _distinct([difference.folded_path for difference in comparison.differences]
                          + comparison.old_only_fields + comparison.new_only_fields
                          + [difference.folded_path
                             for difference in comparison.shape_differences])
        if comparison.present_in_old != comparison.present_in_new:
            paths.append('')
        if paths:
            found[comparison.record.member_id.value] = paths
    return found


def _walk(old: Any, new: Any, path: str, folded: str, ignored: FrozenSet[str],
          comparison: RecordComparison) -> None:
    """
    Two values held against each other down to their leaves; what differs is added to the
    comparison.

    Args:
        old: The first run's value
        new: The second run's value
        path: Where they stand, positions included; '' at a document's top
        folded: The same, positions folded
        ignored: Field names never compared
        comparison: Where the differences are collected
    """
    if isinstance(old, dict) and isinstance(new, dict):
        for key in list(old) + [key for key in new if key not in old]:
            if key in ignored:
                continue
            key_path, key_folded = _join(path, key), _join(folded, key)
            if key not in new:
                comparison.old_only_fields.append(key_folded)
            elif key not in old:
                comparison.new_only_fields.append(key_folded)
            elif key in SHAPE_FIELDS and old[key] != new[key]:
                comparison.shape_differences.append(
                    _difference(key_path, key_folded, old[key], new[key]))
            else:
                _walk(old[key], new[key], key_path, key_folded, ignored, comparison)
        return
    if isinstance(old, list) and isinstance(new, list):
        for position in range(max(len(old), len(new))):
            item_path, item_folded = f'{path}[{position}]', f'{folded}[*]'
            if position >= len(old) or position >= len(new):
                comparison.differences.append(_difference(
                    item_path, item_folded,
                    old[position] if position < len(old) else None,
                    new[position] if position < len(new) else None,
                    old_missing=position >= len(old), new_missing=position >= len(new)))
                continue
            _walk(old[position], new[position], item_path, item_folded, ignored, comparison)
        return
    if type(old) is not type(new) or old != new:
        comparison.differences.append(_difference(path, folded, old, new))


def _difference(path: str, folded: str, old: Any, new: Any, old_missing: bool = False,
                new_missing: bool = False) -> RecordDifference:
    """
    One differing value, its two sides as JSON.

    Args:
        path: Where it stands
        folded: The same, positions folded
        old: The first run's value
        new: The second run's value
        old_missing: Whether the first run has no value there at all
        new_missing: Whether the second run has none

    Returns:
        The difference
    """
    return RecordDifference(path=path, folded_path=folded,
                            old_value=None if old_missing else _json(old),
                            new_value=None if new_missing else _json(new))


def _load(run_dir: Path, record: ComparedRecord) -> Optional[Any]:
    """
    One kind of record as a run wrote it.

    Args:
        run_dir: The run's directory
        record: The kind of record

    Returns:
        Its document; for a member with a variable part in its name, every matching file by its
        path inside the run; None where the run wrote none
    """
    pattern = RUN_ENTRY_MEMBERS_BY_ID[record.member_id].path_pattern
    if '{' not in pattern:
        path = run_dir / pattern
        return _load_file(path) if path.is_file() else None
    matches = sorted(run_dir.glob(_glob_of(pattern)))
    if not matches:
        return None
    return {str(path.relative_to(run_dir)): _load_file(path) for path in matches}


def _load_file(path: Path) -> Any:
    """
    One file of a run, read by its kind: a JSON document, a stream, a CSV table.

    Args:
        path: The file

    Returns:
        A JSON file as parsed; a stream as its header and its records, in scenario and `seq`
        order where every record carries a `seq`; a CSV file as its rows
    """
    text = path.read_text(encoding='utf-8')
    if path.suffix == '.json':
        return json.loads(text)
    if path.suffix == '.csv':
        return list(csv.DictReader(text.splitlines()))
    lines = [json.loads(line) for line in text.splitlines() if line.strip()]
    header = lines[0] if lines and lines[0].get('record_kind') == _HEADER_KIND else None
    records = lines[1:] if header is not None else lines
    if records and all('seq' in line for line in records):
        records = sorted(records, key=lambda line: (str(line.get('scenario_name', '')),
                                                    line['seq']))
    return {_HEADER: header, _RECORDS: records}


def _records_of(document: Optional[Any], records_key: str) -> List[Any]:
    """
    The records a document holds, for the question whether nothing of its kind happened.

    Args:
        document: The document; None where the run wrote none
        records_key: Its records list; '' for a stream

    Returns:
        The records; empty where the run wrote none
    """
    if document is None:
        return []
    documents: Iterable[Any] = [document]
    if isinstance(document, dict) and _RECORDS not in document and records_key == '':
        documents = document.values()
    found: List[Any] = []
    for each in documents:
        if isinstance(each, dict):
            found.extend(each.get(_RECORDS if records_key == '' else records_key) or [])
    return found


def _glob_of(pattern: str) -> str:
    """
    A member's path pattern as a glob: every `{name}` part matches any name.

    Args:
        pattern: The pattern

    Returns:
        The glob
    """
    parts: List[str] = []
    inside = False
    for character in pattern:
        if character == '{':
            inside = True
            parts.append('*')
        elif character == '}':
            inside = False
        elif not inside:
            parts.append(character)
    return ''.join(parts)


def _join(path: str, key: Any) -> str:
    """
    A path one field deeper.

    Args:
        path: The path; '' at a document's top
        key: The field

    Returns:
        The longer path
    """
    return f'{path}.{key}' if path else str(key)


def _distinct(paths: List[str]) -> List[str]:
    """
    Paths with each one kept once, in their first order.

    Args:
        paths: The paths

    Returns:
        The distinct paths
    """
    return list(dict.fromkeys(paths))


def _json(value: Any) -> str:
    """
    A value as JSON, keys sorted.

    Args:
        value: The value

    Returns:
        Its JSON text
    """
    return json.dumps(value, sort_keys=True, default=str)
