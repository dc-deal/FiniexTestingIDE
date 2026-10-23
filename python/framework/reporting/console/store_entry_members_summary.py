"""
FiniexTestingIDE - Store entry members views (#621)

What one entry of a store may hold: every declared file, its shape, which pipeline writes it and
when, and the run index column that lists it. Formatting only: the members come from the store's
declaration. The Markdown form is the table the architecture document carries, and a test holds
the two together.
"""

import os
from typing import Dict, List, Tuple

from python.framework.constants.run_layout_constants import (
    RUN_TYPE_AUTOTRADER,
    RUN_TYPE_SIMULATION,
)
from python.framework.store.store_descriptor import StoreDescriptor
from python.framework.types.store_member_types import EntryMember, EntryMemberMoment

# How each moment reads in a table cell.
MOMENT_LABELS: Dict[EntryMemberMoment, str] = {
    EntryMemberMoment.RUN_START: 'at start',
    EntryMemberMoment.AS_IT_RUNS: 'as it runs',
    EntryMemberMoment.RUN_END: 'at end',
    EntryMemberMoment.ON_DEMAND: 'on demand',
}

# The pipelines a table may show a column for, in the order they appear.
_RUN_TYPES = (RUN_TYPE_SIMULATION, RUN_TYPE_AUTOTRADER)

# Where the architecture document lies, so its links to each member's document resolve from there.
_DOC_DIR = 'docs/architecture'


def render_entry_members(descriptor: StoreDescriptor) -> None:
    """
    Print what one entry of a store may hold, one line per member, the conditions underneath.

    Args:
        descriptor: The store whose entry members are shown
    """
    members = descriptor.entry_members
    if not members:
        _render_no_members(descriptor)
        return
    run_types = _run_types_of(members)
    rows = [(member.member_id.value, member.path_pattern, _shape(member),
             *(_moment(member, run_type) for run_type in run_types),
             member.index_column.value if member.index_column else '—',
             member.served_route or '—')
            for member in members]
    headers = ('MEMBER', 'FILE', 'SHAPE', *(run_type.upper() for run_type in run_types),
               'LISTED IN', 'SERVED AT')
    widths = [max(len(text) for text in column) for column in zip(headers, *rows)]
    header = '  ' + '  '.join(f'{text:<{width}}' for text, width in zip(headers, widths)).rstrip()
    print('\n' + '=' * len(header))
    print(f'🗂️  {descriptor.store_id.value} — what ONE entry may hold: '
          f'{len(members)} declared file(s)')
    print('=' * len(header) + '\n')
    print(header)
    print('  ' + '-' * (len(header) - 2))
    for row in rows:
        print(('  ' + '  '.join(f'{text:<{width}}' for text, width in zip(row, widths))).rstrip())
    conditions = [(member, write) for member in members for write in member.written_by
                  if write.condition]
    if conditions:
        print('\n  Conditions:')
        id_width = max(len(member.member_id.value) for member, _ in conditions)
        for member, write in conditions:
            print(f'      {member.member_id.value:<{id_width}}  {write.run_type}: '
                  f'{write.condition}')
    print(f'\n  The rules: docs/architecture/store_entry_members.md · this table as Markdown: '
          f'store_cli.py entry-members {descriptor.store_id.value} --markdown\n')


def render_entry_members_markdown(descriptor: StoreDescriptor) -> None:
    """
    Print the Markdown table of what one entry of a store may hold.

    Args:
        descriptor: The store whose entry members are shown
    """
    if not descriptor.entry_members:
        _render_no_members(descriptor)
        return
    print(entry_members_markdown(descriptor.entry_members))


def entry_members_markdown(members: Tuple[EntryMember, ...]) -> str:
    """
    The members as the Markdown table the architecture document carries.

    Args:
        members: The declared members of one store

    Returns:
        The table, one line per member, without a trailing newline
    """
    run_types = _run_types_of(members)
    lines = ['| Member | File | Shape | ' + ' | '.join(run_types) + ' | Listed in | Served at |',
             '|' + '---|' * (5 + len(run_types))]
    for member in members:
        written = [_moment(member, run_type, with_condition=True) for run_type in run_types]
        listed = f'`{member.index_column.value}`' if member.index_column else '—'
        served = f'`…/{member.served_route}`' if member.served_route else '—'
        lines.append(f'| [`{member.member_id.value}`]({_doc_link(member.doc)}) | '
                     f'`{member.path_pattern}` | {_shape(member)} | '
                     + ' | '.join(written) + f' | {listed} | {served} |')
    return '\n'.join(lines)


def _render_no_members(descriptor: StoreDescriptor) -> None:
    """
    Say that a store declares no members, and why that is not an omission.

    Args:
        descriptor: The store asked about
    """
    print(f'\n  {descriptor.store_id.value}: one entry is a single file, so the store declares no '
          f'members. Entries are matched by {descriptor.entry_glob!r}.\n')


def _run_types_of(members: Tuple[EntryMember, ...]) -> List[str]:
    """
    The pipelines that write any of the members, in their fixed order.

    Args:
        members: The declared members

    Returns:
        The run types
    """
    return [run_type for run_type in _RUN_TYPES
            if any(member.is_written_by(run_type) for member in members)]


def _shape(member: EntryMember) -> str:
    """
    A member's shape, with a stream's schema version.

    Args:
        member: The member

    Returns:
        The cell text
    """
    shape = member.shape.value.replace('_', ' ')
    return f'{shape} · v{member.schema_version}' if member.schema_version is not None else shape


def _moment(member: EntryMember, run_type: str, with_condition: bool = False) -> str:
    """
    When a pipeline writes a member, or a dash when it never does.

    Args:
        member: The member
        run_type: The pipeline
        with_condition: Add the condition under which it is written

    Returns:
        The cell text
    """
    write = next((write for write in member.written_by if write.run_type == run_type), None)
    if write is None:
        return '—'
    label = MOMENT_LABELS[write.moment]
    return f'{label} — {write.condition}' if with_condition and write.condition else label


def _doc_link(doc: str) -> str:
    """
    A member's document as a link from the architecture document.

    Args:
        doc: The document, relative to the project root, with its heading anchor

    Returns:
        The link target
    """
    path, _, anchor = doc.partition('#')
    target = os.path.relpath(path, _DOC_DIR)
    return f'{target}#{anchor}' if anchor else target
