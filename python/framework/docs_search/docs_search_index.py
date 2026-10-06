"""
Ranked search over the served documentation (#568).

A consumer knows what they want to understand and not what the document is called. The index
answers in PASSAGES rather than documents, so the hit names the heading that carries the answer
and the line it starts at.

Okapi BM25, which is the default for exactly this shape of corpus: a few dozen short prose
documents, queries of a handful of words, no training data and nothing to tune. It weighs a rare
word far above a common one, which is what makes a query of ordinary words land on the one
passage that uses an unusual one.

**It is built once and then only read.** Building it is almost entirely file reading — measured
at 97 % of the work — and scoring one query against the built index is a fraction of a
millisecond. Checking whether the documents changed costs more than a query does, so the index is
built when the server starts and stays as it was: these documents ship with the server, so a
change to them is a deployment, and a deployment restarts the process.
"""

import collections
import math
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from python.framework.docs_search.docs_corpus import read_corpus
from python.framework.types.docs_search_types import DocPassage

# Two characters or more, and an underscore binds rather than separates — `run_id`, `p5_ms` and
# `mt5` are terms a consumer searches for, and the plainer pattern used over the book corpus
# drops every one of them: it keeps only runs of letters, so a digit ends a word and an
# underscore splits one.
_TOKEN = re.compile(r"[a-z0-9][a-z0-9_']+")

# Okapi BM25's two constants, at the values the literature uses by default. `K1` sets how fast a
# repeated term stops adding, `B` how hard a long passage is penalised for its length.
_K1 = 1.5
_B = 0.75

# How much of a passage a hit shows — enough to recognise the answer, not enough to replace
# fetching the document.
_SNIPPET_CHARS = 220


def _tokenize(text: str) -> List[str]:
    """
    A text's search terms, with every underscore name also indexed by its parts.

    Both forms are kept so the two ways of writing one thing meet: a document saying "the run id"
    answers a query for `run_id`, and a document saying `run_id` answers a query for "run id".
    Indexing only the parts would lose the exact name, which is the more precise query of the
    two.

    Args:
        text: What to tokenize

    Returns:
        Its terms, lowercased, in order and with repeats kept — BM25 counts them
    """
    terms: List[str] = []
    for word in _TOKEN.findall(text.lower()):
        terms.append(word)
        if '_' in word:
            terms.extend(part for part in word.split('_') if len(part) > 1)
    return terms


def _snippet(text: str, terms: List[str]) -> str:
    """
    The part of a passage around the first query term that occurs in it.

    Args:
        text: The passage
        terms: The query's terms

    Returns:
        A single line of at most `_SNIPPET_CHARS`
    """
    flat = ' '.join(text.split())
    lowered = flat.lower()
    start = 0
    for term in terms:
        found = lowered.find(term)
        if found > 0:
            start = max(0, found - 60)
            break
    return flat[start:start + _SNIPPET_CHARS]


class DocsSearchIndex:
    """
    The served documents, ranked on demand.

    Args:
        root: The directory holding the served documents
    """

    def __init__(self, root: Path):
        self._root = root
        self._passages: List[DocPassage] = []
        self._listing: List[Tuple[str, str, str]] = []
        self._frequency: List[collections.Counter] = []
        self._lengths: List[int] = []
        self._idf: Dict[str, float] = {}
        self._average_length = 1.0
        self._build()

    def _build(self) -> None:
        """Read the documents and compute what scoring needs, once."""
        self._passages, self._listing = read_corpus(self._root)
        tokenized = [_tokenize(passage.text) for passage in self._passages]
        self._frequency = [collections.Counter(terms) for terms in tokenized]
        self._lengths = [len(terms) for terms in tokenized]
        if not self._passages:
            return
        self._average_length = max(sum(self._lengths) / len(self._lengths), 1.0)
        appearances: collections.Counter = collections.Counter()
        for terms in tokenized:
            appearances.update(set(terms))
        total = len(self._passages)
        self._idf = {term: math.log(1 + (total - count + 0.5) / (count + 0.5))
                     for term, count in appearances.items()}

    def get_documents(self) -> List[Tuple[str, str, str]]:
        """
        Every served document, by name.

        Returns:
            One (name, title, summary) per document, in name order
        """
        return list(self._listing)

    def get_names(self) -> List[str]:
        """
        The served names.

        Returns:
            Every document's name, in name order
        """
        return [name for name, _title, _summary in self._listing]

    def get_passage_count(self) -> int:
        """
        How many passages a query is scored against.

        Returns:
            The number of indexed passages
        """
        return len(self._passages)

    def read_document(self, name: str) -> Optional[str]:
        """
        One served document's markdown.

        The name is checked against the index rather than used to build a path, so a name that
        is not a served document cannot reach the filesystem at all.

        Args:
            name: The document's served name

        Returns:
            Its markdown, or None when no document goes by that name
        """
        if name not in self.get_names():
            return None
        return (self._root / f'{name}.md').read_text(encoding='utf-8')

    def search(self, query: str, hits: int) -> Tuple[List[Tuple[float, DocPassage]], List[str]]:
        """
        The passages that best answer a query.

        Args:
            query: What to search for
            hits: How many passages to return at most

        Returns:
            The best passages as (score, passage), highest first, and the query's terms that
                appear in NO served document — which is what tells a caller to try another word,
                rather than leaving them to guess why a result looks wrong
        """
        terms = _tokenize(query)
        unknown = [term for term in dict.fromkeys(terms) if term not in self._idf]
        scored: List[Tuple[float, int]] = []
        for index, counts in enumerate(self._frequency):
            score = 0.0
            length = self._lengths[index]
            for term in terms:
                occurrences = counts.get(term, 0)
                if not occurrences:
                    continue
                weighting = _K1 * (1 - _B + _B * length / self._average_length)
                score += (self._idf[term] * occurrences * (_K1 + 1)
                          / (occurrences + weighting))
            if score > 0:
                scored.append((score, index))
        scored.sort(key=lambda pair: (-pair[0], self._passages[pair[1]].document))
        return [(score, self._passages[index]) for score, index in scored[:hits]], unknown

    def snippet_for(self, passage: DocPassage, query: str) -> str:
        """
        What to show of a passage so a caller recognises the answer without fetching it.

        Args:
            passage: The passage
            query: The query it was found with

        Returns:
            One line from around the first matching term
        """
        return _snippet(passage.text, _tokenize(query))


# The built index, per root, so the documents are read once per process. Keyed on the root
# because an isolated configuration environment serves a different tree, and a test that built
# one must not hand it to the next.
_INDEXES: Dict[str, DocsSearchIndex] = {}


def docs_search_index(root: Path) -> DocsSearchIndex:
    """
    The index over one documentation root, built on first use.

    Args:
        root: The directory holding the served documents

    Returns:
        The index, the same object on every later call for that root
    """
    key = str(root)
    if key not in _INDEXES:
        _INDEXES[key] = DocsSearchIndex(root)
    return _INDEXES[key]


def clear_docs_search_index() -> None:
    """Forget every built index, so the next call reads the documents again."""
    _INDEXES.clear()
