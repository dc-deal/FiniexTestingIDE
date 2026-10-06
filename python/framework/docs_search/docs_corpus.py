"""
Reads the served documentation into the passages the search ranks (#568).

The set it reads is the one the API serves — `docs/consumer/`, written for a reader who cannot
open this repository — never `docs/` as a whole. That is not a scoping convenience: the wider tree
is written for contributors and names things a consumer has no business reading, so a search over
it would answer with passages that cannot be served.

A passage is the text under one heading, and two rules shape where one ends. Both exist because of
something markdown does rather than something the ranking does: a `#` inside a fenced code block
is a shell comment and not a heading, and a passage long enough to hold several answers is split
again at its bold lead-ins, since one oversized passage ranks badly on every one of them.
"""

import re
from pathlib import Path
from typing import List, Optional, Tuple

from python.framework.types.docs_search_types import DocPassage

# Where the served documents live. Derived from this module's own location rather than read from
# configuration, because they ship WITH the server: a change to them is a deployment, and a path
# that could point somewhere else would let one installation serve another's answers.
DOCS_ROOT = Path(__file__).resolve().parents[3] / 'docs' / 'consumer'

# A heading, but only outside a code fence — see the fence tracking in `_heading_positions`.
_HEADING = re.compile(r'^(#{1,4}) +(.+?)\s*$')
_FENCE = re.compile(r'^\s*(?:```|~~~)')
_BOLD_LEAD = re.compile(r'^\*\*')

# Below this a passage carries no answer — a stub heading, a one-line pointer — and only adds a
# row a caller has to dismiss. Deliberately far lower than the book corpus uses: there a short
# page is a scanning artifact, here a short passage is somebody's choice.
MIN_PASSAGE_CHARS = 40

# Above this a passage is split again at its bold lead-ins. The ranking normalises by length, so
# one long passage scores worse on each of the several answers it holds than each would alone.
MAX_PASSAGE_CHARS = 4000

# What the text above a document's first heading is labelled with.
_OPENING = '(opening)'


def _heading_positions(lines: List[str]) -> List[Tuple[int, str]]:
    """
    Where a document's headings are, ignoring the ones inside code fences.

    Args:
        lines: The document's lines, without their endings

    Returns:
        One (line index, heading text) per heading, in order
    """
    positions: List[Tuple[int, str]] = []
    in_fence = False
    for index, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _HEADING.match(line)
        if match:
            positions.append((index, match.group(2)))
    return positions


def _split_oversized(document: str, heading: str, start_line: int,
                     lines: List[str]) -> List[DocPassage]:
    """
    One heading's passages — itself, or its bold lead-ins where it is long enough to hold several
    answers.

    The pieces keep the parent's heading, because that is what a caller needs in order to know
    where they are; the line number is each piece's own, so opening it lands on the answer rather
    than on the heading above it.

    Args:
        document: The served name of the document this text belongs to
        heading: The heading it stands under
        start_line: 1-based line of its first line
        lines: Its lines

    Returns:
        The passages this heading contributes
    """
    text = '\n'.join(lines)
    if len(text) <= MAX_PASSAGE_CHARS:
        return [DocPassage(document, heading, start_line, text)]

    passages: List[DocPassage] = []
    current: List[str] = []
    current_line = start_line
    in_fence = False
    for offset, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
        if not in_fence and _BOLD_LEAD.match(line) and current:
            body = '\n'.join(current)
            if len(body.strip()) >= MIN_PASSAGE_CHARS:
                passages.append(DocPassage(document, heading, current_line, body))
            current = []
            current_line = start_line + offset
        current.append(line)
    body = '\n'.join(current)
    if len(body.strip()) >= MIN_PASSAGE_CHARS:
        passages.append(DocPassage(document, heading, current_line, body))
    return passages


def read_passages(document: str, text: str) -> List[DocPassage]:
    """
    One document's ranked passages.

    Args:
        document: The served name — the file stem, which is what `/docs/{name}` takes
        text: The document's full markdown

    Returns:
        Its passages, in document order
    """
    lines = text.split('\n')
    positions = _heading_positions(lines)
    passages: List[DocPassage] = []

    first = positions[0][0] if positions else len(lines)
    if first > 0:
        passages.extend(_split_oversized(document, _OPENING, 1, lines[:first]))
    for index, (line_index, heading) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else len(lines)
        passages.extend(
            _split_oversized(document, heading, line_index + 1, lines[line_index:end]))
    return [passage for passage in passages
            if len(passage.text.strip()) >= MIN_PASSAGE_CHARS]


def document_title(text: str) -> Optional[str]:
    """
    A document's own title — its first level-one heading.

    Args:
        text: The document's markdown

    Returns:
        The title, or None when the document declares none
    """
    for line in text.split('\n'):
        if line.startswith('# '):
            return line[2:].strip()
    return None


def document_summary(text: str) -> str:
    """
    The one paragraph that says what a document is for — its first body paragraph.

    Taken from the document rather than declared beside it, so the list and the document itself
    cannot drift apart: the sentence a caller reads in the index is the one they then read again
    at the top of the document.

    Args:
        text: The document's markdown

    Returns:
        The paragraph as one line, or the empty string where the document opens with a heading
            and nothing else
    """
    paragraph: List[str] = []
    for line in text.split('\n'):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            if paragraph:
                break
            continue
        if stripped.startswith(('|', '```', '- ', '* ', '> ')):
            if paragraph:
                break
            continue
        paragraph.append(stripped)
    return ' '.join(paragraph)


def read_corpus(
    root: Path,
    recursive: bool = False,
) -> Tuple[List[DocPassage], List[Tuple[str, str, str]]]:
    """
    Every served document, split into passages, with what the index route lists.

    The served set is one flat folder, so the API reads one level. A maintainer searching the
    whole documentation tree from a terminal reads every level; a document is then named by its
    path below the root (`architecture/live_execution_architecture`), which is still the name
    `read_document` resolves.

    Args:
        root: The directory holding the documents
        recursive: Whether documents in sub-folders are read too

    Returns:
        The passages of every document, and one (name, title, summary) per document, by name
    """
    passages: List[DocPassage] = []
    listing: List[Tuple[str, str, str]] = []
    if not root.is_dir():
        return passages, listing
    paths = root.rglob('*.md') if recursive else root.glob('*.md')
    for path in sorted(paths):
        text = path.read_text(encoding='utf-8')
        name = path.relative_to(root).with_suffix('').as_posix()
        passages.extend(read_passages(name, text))
        listing.append((name, document_title(text) or name, document_summary(text)))
    return passages, listing
