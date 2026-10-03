import pytest
import responses
from pydantic import SecretStr

from biblio_demon.metadata import (
    CrossrefClient,
    DataCiteClient,
    MetadataResolver,
    ResolutionError,
    SemanticScholarClient,
    clean_markup,
    split_full_name,
)

DOI = "10.1109/cvpr.2016.90"
CROSSREF_URL = f"https://api.crossref.org/works/{DOI}"
DATACITE_URL = f"https://api.datacite.org/dois/{DOI}"
S2_URL = f"https://api.semanticscholar.org/graph/v1/paper/DOI:{DOI}"

CROSSREF_MSG = {
    "message": {
        "title": ["Deep Residual Learning for <i>Image</i> Recognition"],
        "author": [
            {"given": "Kaiming", "family": "He"},
            {"given": "Xiangyu", "family": "Zhang"},
            {"name": "Some Consortium"},
        ],
        "issued": {"date-parts": [[2016, 6]]},
        "container-title": ["2016 IEEE CVPR"],
        "abstract": "<jats:p>Deeper nets are &amp; harder.</jats:p>",
        "type": "proceedings-article",
    }
}


@pytest.fixture
def fast_wait(monkeypatch):
    monkeypatch.setattr("biblio_demon.metadata._wait", lambda _state: 0)


def test_crossref_parse(settings) -> None:
    meta = CrossrefClient.parse(DOI, CROSSREF_MSG["message"])
    assert meta.title == "Deep Residual Learning for Image Recognition"
    assert meta.authors == ["Kaiming He", "Xiangyu Zhang", "Some Consortium"]
    assert meta.first_author_family == "He"
    assert meta.year == 2016
    assert meta.abstract == "Deeper nets are & harder."
    assert meta.item_type == "conferencePaper"
    assert meta.venue == "2016 IEEE CVPR"


@responses.activate
def test_crossref_sends_polite_user_agent(settings) -> None:
    responses.get(CROSSREF_URL, json=CROSSREF_MSG)
    CrossrefClient(settings).fetch(DOI)
    ua = responses.calls[0].request.headers["User-Agent"]
    assert "mailto:test@example.org" in ua


@responses.activate
def test_fallback_on_404_to_datacite(settings) -> None:
    responses.get(CROSSREF_URL, status=404)
    responses.get(
        DATACITE_URL,
        json={
            "data": {
                "attributes": {
                    "titles": [{"title": "A dataset paper"}],
                    "creators": [
                        {"familyName": "Doe", "givenName": "Jane"},
                        {"name": "Roe, Richard"},
                    ],
                    "publicationYear": 2021,
                    "publisher": "Zenodo",
                    "types": {"resourceTypeGeneral": "Preprint"},
                    "descriptions": [
                        {"descriptionType": "Abstract", "description": "Abs."}
                    ],
                }
            }
        },
    )
    meta = MetadataResolver(settings).resolve(DOI)
    assert meta.source == "datacite"
    assert meta.authors == ["Jane Doe", "Richard Roe"]
    assert meta.item_type == "preprint"
    assert meta.venue == "Zenodo"
    assert len(responses.calls) == 2  # el 404 no se reintenta


@responses.activate
def test_retries_transient_then_semantic_scholar(settings, fast_wait) -> None:
    responses.get(CROSSREF_URL, status=503)
    responses.get(DATACITE_URL, status=404)
    responses.get(
        S2_URL,
        json={
            "title": "Deep Residual Learning",
            "authors": [{"name": "Kaiming He"}],
            "year": 2016,
            "venue": "CVPR",
            "publicationTypes": ["Conference"],
        },
    )
    meta = MetadataResolver(settings).resolve(DOI)
    assert meta.source == "semantic_scholar"
    assert meta.item_type == "conferencePaper"
    crossref_calls = [c for c in responses.calls if "crossref" in c.request.url]
    assert len(crossref_calls) == settings.http_max_attempts


@responses.activate
def test_all_sources_fail(settings, fast_wait) -> None:
    for url in (CROSSREF_URL, DATACITE_URL, S2_URL):
        responses.get(url, status=404)
    with pytest.raises(ResolutionError) as info:
        MetadataResolver(settings).resolve(DOI)
    assert set(info.value.reasons) == {"crossref", "datacite", "semantic_scholar"}


@responses.activate
def test_semantic_scholar_api_key_header(settings) -> None:
    responses.get(S2_URL, json={"title": "T", "authors": []})
    settings.semantic_scholar_api_key = SecretStr("secret")
    SemanticScholarClient(settings).fetch(DOI)
    assert responses.calls[0].request.headers["x-api-key"] == "secret"


def test_helpers() -> None:
    assert clean_markup("<jats:title>Abstract</jats:title> Hello  world") == (
        "Hello world"
    )
    assert clean_markup(None) is None
    a = split_full_name("Juan Carlos de la Cruz")
    assert (a.given, a.family) == ("Juan Carlos de la", "Cruz")
    assert MetadataResolver.arxiv_doi("1706.03762") == "10.48550/arxiv.1706.03762"
    assert DataCiteClient.name == "datacite"
