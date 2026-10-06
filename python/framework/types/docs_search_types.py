"""
The pieces the documentation search is built from (#568).

One PASSAGE is the text of one served document under one heading — the unit that is ranked, and
the unit a hit names. A whole document is the wrong unit: the longest one in the served set holds
far more than the answer a caller wants, and the ranking normalises by length, so a long document
scores badly on the one paragraph that actually answers the question.

Called a passage and not a section on purpose. In this project a *section* is one section of a
RUN REPORT — trade history, portfolio, booking periods — which a consumer meets as a route and as
a name in a run's `artifacts`. Giving the word a second meaning here would put two contracts on
one term, and a reader who learned it in one place would carry the wrong one into the other.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class DocPassage:
    """
    One ranked passage of one served document.

    Args:
        document: The document's served name, as `/api/v1/docs/{name}` takes it
        heading: The heading the passage stands under, or the document's own opening for the text
            above the first heading
        line: Where the passage starts in the document, 1-based — what lets a caller open the
            served markdown at the answer instead of reading from the top
        text: The passage including its heading, which is what is ranked
    """
    document: str
    heading: str
    line: int
    text: str
