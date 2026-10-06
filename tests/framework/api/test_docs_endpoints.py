"""
The API serves its own documentation, and every route says which document describes it.

Four of these guard failures that are SILENT, which is why they are a gate rather than a review
habit:

  A · DECLARATION — a route mounted without naming its document looks exactly like one that
      needs none. Walked from the generated schema, so a route added tomorrow is covered the
      moment it exists, and the file it names must be on disk.

  B · ROUTE ORDER — `/docs/{name}` matches `/docs/search`. Declared in the wrong order it
      answers a search with "no document called search", which is a 404 nobody reads as a
      routing fault. One request pins it.

  C · THE SERVED SET — the search must index nothing the document route would refuse, or a hit
      names a document that cannot be fetched.

  D · THE LINK HEADER — it is the half a consumer actually meets. A header naming a document
      that does not exist is worse than none, because it is followed.
"""

import pytest
from fastapi.testclient import TestClient

from python.api.api_app import create_app
from python.api.api_route_documents import route_documents, routes_by_document
from python.framework.docs_search.docs_corpus import DOCS_ROOT
from python.framework.docs_search.docs_search_index import (
    clear_docs_search_index,
    docs_search_index,
)
from python.framework.types.api.docs_types import DocListResponse, DocSearchResponse

# A query whose words are the project's own, so a mismatch means the index is wrong rather than
# the query being unlucky.
_A_REAL_QUERY = 'booking period reconciles'


@pytest.fixture(scope='module')
def app():
    """
    The mounted application, reading the documents as they are on disk right now.

    The index is built once per process and deliberately never rechecked — these documents ship
    with the server, so a change to them is a deployment. A suite is the one place that is wrong:
    an earlier test may have built it, and the files may have changed since.
    """
    clear_docs_search_index()
    return create_app()


@pytest.fixture(scope='module')
def client(app):
    """A client against it."""
    return TestClient(app)


class TestEveryRouteNamesItsDocument:

    def test_every_mounted_route_declares_one(self, app):
        walked = route_documents(app)
        assert walked, 'the walk itself found nothing — it is broken'
        undeclared = [f'{method} {path}' for path, method, name in walked if name is None]
        assert undeclared == [], 'pass openapi_extra=describes(...) on these routes'

    def test_every_declared_document_exists(self, app):
        missing = [name for name in routes_by_document(app)
                   if not (DOCS_ROOT / f'{name}.md').is_file()]
        assert missing == []

    def test_the_index_lists_every_document_a_route_names(self, app, client):
        served = {row['name'] for row in client.get('/api/v1/docs').json()['documents']}
        assert set(routes_by_document(app)) <= served


class TestTheLiteralRouteWinsOverTheParameterisedOne:

    def test_search_is_not_read_as_a_document_name(self, client):
        answer = client.get('/api/v1/docs/search', params={'q': 'x'})
        assert answer.status_code == 200, 'declare /docs/search BEFORE /docs/{name}'
        assert 'hits' in answer.json()

    def test_a_document_is_still_reachable_by_name(self, client):
        answer = client.get('/api/v1/docs/docs')
        assert answer.status_code == 200
        assert answer.headers['content-type'].startswith('text/markdown')
        assert answer.text.startswith('# ')


class TestTheSearchIndexesOnlyWhatIsServed:

    def test_every_hit_names_a_fetchable_document(self, client):
        hits = client.get('/api/v1/docs/search',
                          params={'q': _A_REAL_QUERY, 'limit': 20}).json()['hits']
        assert hits, f'nothing matched {_A_REAL_QUERY!r} — the corpus is not being read'
        for hit in hits:
            assert client.get(f"/api/v1/docs/{hit['document']}").status_code == 200

    def test_a_hit_points_at_a_line_the_document_has(self, client):
        hits = client.get('/api/v1/docs/search', params={'q': _A_REAL_QUERY}).json()['hits']
        for hit in hits:
            text = client.get(f"/api/v1/docs/{hit['document']}").text
            assert 1 <= hit['line'] <= len(text.split('\n'))

    def test_the_indexed_names_are_the_served_names(self, client):
        index = docs_search_index(DOCS_ROOT)
        served = {row['name'] for row in client.get('/api/v1/docs').json()['documents']}
        assert set(index.get_names()) == served


class TestTheLinkHeaderPointsSomewhereReal:

    def test_an_open_route_carries_it(self, client):
        link = client.get('/api/v1/timeframes').headers.get('Link')
        assert link is not None and 'rel="describedby"' in link

    def test_it_names_a_document_that_answers(self, client):
        link = client.get('/api/v1/timeframes').headers['Link']
        target = link[link.index('<') + 1:link.index('>')]
        assert client.get(target).status_code == 200

    def test_a_head_request_carries_it_too(self, client):
        """The HEAD middleware passes a COPY of the scope, so a link added outside it is lost."""
        assert 'Link' in client.head('/api/v1/timeframes').headers

    def test_an_unrouted_request_carries_none(self, client):
        assert 'Link' not in client.get('/api/v1/no-such-route').headers


class TestTheDeclaredKeysDescribeTheRows:

    def test_the_document_list_keys_on_the_name(self, client):
        body = client.get('/api/v1/docs').json()
        assert body['key'] == ['name']
        names = [row['name'] for row in body['documents']]
        assert len(names) == len(set(names))

    def test_a_search_hit_needs_its_line_as_well(self, client):
        """
        The document and the heading alone are not unique: a long section is indexed in several
        pieces that keep their parent's heading, so the pair repeats where the line does not.
        """
        hits = client.get('/api/v1/docs/search',
                          params={'q': 'equity account currency null', 'limit': 50}).json()['hits']
        assert hits
        triples = {(h['document'], h['heading'], h['line']) for h in hits}
        assert len(triples) == len(hits)


class TestTheRefusals:

    def test_an_empty_query_is_a_bad_request(self, client):
        answer = client.get('/api/v1/docs/search', params={'q': ''})
        assert answer.status_code == 400
        assert answer.json()['error'] == 'empty_query'

    def test_a_blank_query_is_the_same_refusal(self, client):
        assert client.get('/api/v1/docs/search',
                          params={'q': '   '}).json()['error'] == 'empty_query'

    def test_an_overlong_query_is_refused(self, client):
        answer = client.get('/api/v1/docs/search', params={'q': 'word ' * 60})
        assert answer.status_code == 400
        assert answer.json()['error'] == 'query_too_long'

    def test_a_limit_outside_the_range_is_refused_never_clamped(self, client):
        for limit in (0, 51):
            answer = client.get('/api/v1/docs/search', params={'q': 'equity', 'limit': limit})
            assert answer.status_code == 400, limit
            assert answer.json()['error'] == 'invalid_limit'

    def test_an_unknown_document_names_the_index_as_the_remedy(self, client):
        answer = client.get('/api/v1/docs/no-such-document')
        assert answer.status_code == 404
        assert answer.json()['error'] == 'document_not_found'
        assert '/api/v1/docs' in answer.json()['detail']

    def test_a_name_cannot_reach_outside_the_served_set(self, client):
        """The name is matched against the index, never joined onto a path."""
        for name in ('../glossary', '..%2Fglossary', 'consumer/docs'):
            assert client.get(f'/api/v1/docs/{name}').status_code in (404, 400)


class TestTheAnswersMatchTheirModels:

    def test_the_index_parses_as_its_model(self, client):
        DocListResponse(**client.get('/api/v1/docs').json())

    def test_a_search_parses_as_its_model(self, client):
        DocSearchResponse(**client.get('/api/v1/docs/search',
                                       params={'q': _A_REAL_QUERY}).json())

    def test_a_word_we_do_not_use_is_reported_back(self, client):
        body = client.get('/api/v1/docs/search',
                          params={'q': 'equity zzzqqxnotaword'}).json()
        assert 'zzzqqxnotaword' in body['terms_not_matched']

    def test_the_index_says_how_much_it_searched(self, client):
        body = client.get('/api/v1/docs/search', params={'q': _A_REAL_QUERY}).json()
        assert body['passages_searched'] > 0

    def test_a_document_row_names_the_routes_it_describes(self, app, client):
        rows = {row['name']: row['routes']
                for row in client.get('/api/v1/docs').json()['documents']}
        for name, paths in routes_by_document(app).items():
            assert rows[name] == paths
