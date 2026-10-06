"""
The API's error vocabulary is declared once and held to its routes and its documentation.

Before the catalog, codes were string literals at the raising line: eight different absences on
the bars and broker routes shared one bare `not_found`, the same sentence stood in two routes, and
the documented error table listed four codes while the routes raised seventeen. A declaration
earns its place only with a test that it is COMPLETE (CLAUDE.md §49), so this file holds the
catalog to the routes and to the table, in both directions.
"""

import ast
import re
import string
from pathlib import Path
from typing import get_args

from finiex_auth.error_factory import AuthErrorCode

from python.api import api_error_catalog
from python.api.api_error_catalog import API_ERRORS, api_error
from python.framework.types.api.api_error_types import ApiErrorKind

_API_DIR = Path('python/api')
_CATALOG = _API_DIR / 'api_error_catalog.py'
# The one other place an ApiException may be built: the error factory the shared auth package
# raises through, whose codes are that package's own closed vocabulary.
_AUTH_FACTORY = _API_DIR / 'api_auth_setup.py'
# The error table lives in the document the API SERVES, because the people who branch on these
# codes are the ones who cannot read this repository.
_DOC = Path('docs/consumer/errors.md')


def _catalog_names() -> dict:
    """
    Every catalog constant by name.

    Returns:
        constant name → ApiErrorKind
    """
    return {name: value for name, value in vars(api_error_catalog).items()
            if isinstance(value, ApiErrorKind)}


def _route_sources() -> dict:
    """
    Every API module except the catalog itself.

    Returns:
        path → source text
    """
    return {path: path.read_text(encoding='utf-8') for path in _API_DIR.rglob('*.py')
            if path != _CATALOG}


def _documented_codes() -> set:
    """
    The codes the served error document lists.

    Returns:
        The backticked codes in the table's second column
    """
    return set(re.findall(r'^\| \d{3} \| `([a-z_]+)` \|',
                          _DOC.read_text(encoding='utf-8'), flags=re.M))


class TestTheCatalogIsOneVocabulary:

    def test_every_constant_is_in_the_tuple_and_every_code_is_unique(self):
        names = _catalog_names()
        assert names, 'the catalog declares entries'
        assert set(names.values()) == set(API_ERRORS), 'API_ERRORS lists every constant'
        codes = [kind.code for kind in API_ERRORS]
        assert len(codes) == len(set(codes)), 'one code, one cause'

    def test_no_code_is_a_bare_status(self):
        """A code must name a cause — `not_found` tells a consumer only the status it has."""
        assert 'not_found' not in {kind.code for kind in API_ERRORS}

    def test_every_message_fills_from_its_own_placeholders(self):
        for kind in API_ERRORS:
            fields = {field for _, field, _, _ in string.Formatter().parse(kind.message)
                      if field}
            error = api_error(kind, **{field: 'x' for field in fields})
            assert (error.status_code, error.error) == (kind.status, kind.code)
            assert '{' not in error.detail, kind.code

    def test_every_sentence_is_written_for_a_person(self):
        """
        `detail` is shown to whoever reads the answer, so it names what happened — never a field of
        a response in backticks, the mark of a sentence written for a developer (contract 10).
        """
        for kind in API_ERRORS:
            assert '`' not in kind.message, f'{kind.code}: {kind.message!r}'


class TestTheRoutesRaiseOnlyFromIt:

    def test_no_route_builds_an_exception_with_a_literal_code(self):
        offenders = []
        for path, source in _route_sources().items():
            if path == _AUTH_FACTORY:
                continue
            for node in ast.walk(ast.parse(source)):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                        and node.func.id == 'ApiException'):
                    offenders.append(f'{path}:{node.lineno}')
        assert offenders == [], 'raise api_error(KIND, …) instead'

    def test_every_entry_is_raised_somewhere(self):
        """An entry no route raises is a code a consumer handles for nothing."""
        sources = '\n'.join(_route_sources().values())
        unused = [name for name in _catalog_names()
                  if not re.search(rf'\b{name}\b', sources)]
        assert unused == []


class TestTheDocumentedTableIsTheVocabulary:

    def test_the_table_lists_exactly_the_catalog_and_the_auth_codes(self):
        expected = {kind.code for kind in API_ERRORS} | set(get_args(AuthErrorCode))
        assert _documented_codes() == expected
