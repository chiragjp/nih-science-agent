"""Offline tests for the PubMed and iCite connectors (no network access).

The shared ``CachedClient`` is driven by an ``httpx.MockTransport`` so these
tests exercise request construction, normalization, batching, and caching
without touching NCBI or icite.od.nih.gov.
"""

from __future__ import annotations

import httpx
import pytest

from nih_science_agent.connectors import icite, pubmed
from nih_science_agent.connectors.icite import ICiteClient, normalize_metrics
from nih_science_agent.connectors.pubmed import PubmedClient, normalize_summary

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

ESEARCH_RESPONSE = {"esearchresult": {"count": "2", "idlist": ["32887691", "29643470"]}}

ESUMMARY_RESPONSE = {
    "result": {
        "uids": ["32887691"],
        "32887691": {
            "uid": "32887691",
            "pubdate": "2020 Sep 4",
            "source": "Science",
            "fulljournalname": "Science (New York, N.Y.)",
            "authors": [
                {"name": "Doe J", "authtype": "Author"},
                {"name": "Roe K", "authtype": "Author"},
            ],
            "title": "A landmark study of something important.",
            "articleids": [
                {"idtype": "pubmed", "value": "32887691"},
                {"idtype": "doi", "value": "10.1126/science.abc1234"},
                {"idtype": "pmc", "value": "PMC7654321"},
            ],
            "pubtype": ["Journal Article"],
        },
    }
}

ICITE_RESPONSE = {
    "data": [
        {
            "pmid": 32887691,
            "year": 2020,
            "title": "A landmark study of something important",
            "journal": "Science",
            "relative_citation_ratio": 4.31,
            "nih_percentile": 92.5,
            "citation_count": 137,
            "citations_per_year": 27.4,
            "expected_citations_per_year": 6.2,
            "field_citation_rate": 5.9,
            "is_research_article": "Yes",
            "is_clinical": "No",
            "doi": "10.1126/science.abc1234",
        }
    ],
    "links": {},
}


def _mock_client(
    routes: dict[str, dict], captured: list[httpx.Request] | None = None
) -> httpx.Client:
    """Route by URL path suffix to a JSON payload."""

    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        for suffix, payload in routes.items():
            if request.url.path.endswith(suffix):
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={})

    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# PubMed normalization
# --------------------------------------------------------------------------- #


def test_normalize_summary_full() -> None:
    rec = ESUMMARY_RESPONSE["result"]["32887691"]
    a = normalize_summary(rec)
    assert a.pmid == "32887691"
    assert a.title == "A landmark study of something important"  # trailing period stripped
    assert a.journal == "Science (New York, N.Y.)"
    assert a.pub_year == 2020
    assert a.authors == ["Doe J", "Roe K"]
    assert a.doi == "10.1126/science.abc1234"
    assert a.pmcid == "PMC7654321"
    assert a.source_url == "https://pubmed.ncbi.nlm.nih.gov/32887691/"
    assert a.retrieved_at is not None


def test_normalize_summary_partial() -> None:
    a = normalize_summary({"uid": "1"})
    assert a.pmid == "1"
    assert a.authors == []
    assert a.doi is None
    assert a.pub_year is None


def test_normalize_summary_rejects_non_dict() -> None:
    with pytest.raises(TypeError):
        normalize_summary(["not", "a", "dict"])  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# PubMed client
# --------------------------------------------------------------------------- #


def test_pubmed_search_returns_pmids(tmp_path) -> None:
    captured: list[httpx.Request] = []
    http = _mock_client({"esearch.fcgi": ESEARCH_RESPONSE}, captured)
    client = PubmedClient(client=http, cache_dir=tmp_path, use_cache=False)
    pmids = client.search("crispr base editing", retmax=50)
    assert pmids == ["32887691", "29643470"]
    q = dict(captured[0].url.params)
    assert q["term"] == "crispr base editing"
    assert q["retmax"] == "50"
    assert q["db"] == "pubmed"


def test_pubmed_api_key_included(tmp_path) -> None:
    captured: list[httpx.Request] = []
    http = _mock_client({"esearch.fcgi": ESEARCH_RESPONSE}, captured)
    client = PubmedClient(client=http, cache_dir=tmp_path, use_cache=False, api_key="SECRETKEY")
    client.search("x")
    assert dict(captured[0].url.params)["api_key"] == "SECRETKEY"


def test_pubmed_fetch_summaries(tmp_path) -> None:
    http = _mock_client({"esummary.fcgi": ESUMMARY_RESPONSE})
    client = PubmedClient(client=http, cache_dir=tmp_path, use_cache=False)
    articles = client.fetch_summaries(["32887691"])
    assert len(articles) == 1
    assert articles[0].journal == "Science (New York, N.Y.)"


def test_pubmed_fetch_summaries_empty_returns_empty(tmp_path) -> None:
    http = _mock_client({})
    client = PubmedClient(client=http, cache_dir=tmp_path, use_cache=False)
    assert client.fetch_summaries([]) == []


# --------------------------------------------------------------------------- #
# iCite normalization + client
# --------------------------------------------------------------------------- #


def test_normalize_metrics_full() -> None:
    m = normalize_metrics(ICITE_RESPONSE["data"][0])
    assert m.pmid == "32887691"
    assert m.relative_citation_ratio == 4.31
    assert m.nih_percentile == 92.5
    assert m.citation_count == 137
    assert m.is_research_article is True  # "Yes" -> True
    assert m.is_clinical is False  # "No" -> False
    assert m.source_url is not None


def test_normalize_metrics_handles_bool_flags() -> None:
    m = normalize_metrics({"pmid": 1, "is_clinical": True, "is_research_article": False})
    assert m.is_clinical is True
    assert m.is_research_article is False


def test_icite_fetch_metrics_preserves_order_and_drops_missing(tmp_path) -> None:
    http = _mock_client({"/pubs": ICITE_RESPONSE})
    client = ICiteClient(client=http, cache_dir=tmp_path, use_cache=False)
    # Ask for two pmids; iCite only returns one -> the missing one is dropped.
    results = client.fetch_metrics(["32887691", "99999999"])
    assert [m.pmid for m in results] == ["32887691"]


def test_icite_get_single(tmp_path) -> None:
    http = _mock_client({"/pubs": ICITE_RESPONSE})
    client = ICiteClient(client=http, cache_dir=tmp_path, use_cache=False)
    m = client.get_metrics("32887691")
    assert m is not None and m.relative_citation_ratio == 4.31


# --------------------------------------------------------------------------- #
# Shared caching behavior
# --------------------------------------------------------------------------- #


def test_cache_round_trip(tmp_path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=ICITE_RESPONSE)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ICiteClient(client=http, cache_dir=tmp_path, use_cache=True)
    client.fetch_metrics(["32887691"])
    client.fetch_metrics(["32887691"])
    assert calls["n"] == 1  # second call served from disk
    assert list(tmp_path.glob("*.json"))


def test_module_level_wrappers(monkeypatch, tmp_path) -> None:
    http_pub = _mock_client({"esearch.fcgi": ESEARCH_RESPONSE, "esummary.fcgi": ESUMMARY_RESPONSE})
    http_icite = _mock_client({"/pubs": ICITE_RESPONSE})
    monkeypatch.setattr(
        pubmed,
        "PubmedClient",
        lambda **kw: PubmedClient(client=http_pub, cache_dir=tmp_path, use_cache=False),
    )
    monkeypatch.setattr(
        icite,
        "ICiteClient",
        lambda **kw: ICiteClient(client=http_icite, cache_dir=tmp_path, use_cache=False),
    )
    assert pubmed.search("x") == ["32887691", "29643470"]
    assert icite.fetch_metrics(["32887691"])[0].pmid == "32887691"
