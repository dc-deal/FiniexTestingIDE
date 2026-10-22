"""
Shared test helper: the heading anchors of a Markdown document, the way GitHub derives them — so a
help link a declaration carries is held to a heading that exists.
"""

import re
from pathlib import Path


def heading_anchors(doc: Path) -> set:
    """
    The anchors GitHub derives from a document's headings, fenced code excluded.

    Args:
        doc: The Markdown file

    Returns:
        One anchor per heading: lower-cased, punctuation dropped, spaces turned into hyphens
    """
    anchors, fenced = set(), False
    for line in doc.read_text(encoding='utf-8').splitlines():
        if line.lstrip().startswith('```'):
            fenced = not fenced
            continue
        heading = re.match(r'#{1,6}\s+(.+?)\s*$', line)
        if heading and not fenced:
            anchors.add(re.sub(r'[^\w\- ]', '', heading.group(1).lower()).replace(' ', '-'))
    return anchors
