"""Offline tests for the award→publication linkage layer."""

from __future__ import annotations

import httpx

from nih_science_agent.connectors import icite, pubmed, reporter
from nih_science_agent.connectors.reporter import (
    ReporterClient,
    normalize_publication_link,
)
from nih_science_agent.linkage import edges as linkage

PUBLICATIONS_RESPONSE = {
    "meta": {"total": 2, "offset": 0, "limit": 500},
    "results": [
        {"coreproject": "R01ES032470", "pmid": 37100513, "applid": 11115672},
        {"coreproject": "R01ES032470", "pmid": 37066248, "applid": 11115672},
    ],
}


def _mock_client(payload: dict, captured: list[dict] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            import json

            captured.append(json.loads(request.content))
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# RePORTER publications endpoint
# --------------------------------------------------------------------------- #


def test_normalize_publication_link() -> None:
    link = normalize_publication_link(
        {"coreproject": "R01ES032470", "pmid": 37100513, "applid": 11115672}
    )
    assert link.core_project_num == "R01ES032470"
    assert link.pmid == "37100513"  # coerced to str
    assert link.application_id == 11115672
    assert link.retrieved_at is not None


def test_get_publications_hits_publications_endpoint(tmp_path) -> None:
    captured: list[dict] = []
    http = _mock_client(PUBLICATIONS_RESPONSE, captured)
    client = ReporterClient(client=http, cache_dir=tmp_path, use_cache=False)
    links = client.get_publications("R01ES032470")
    assert [link.pmid for link in links] == ["37100513", "37066248"]
    assert captured[0]["criteria"]["core_project_nums"] == ["R01ES032470"]


def test_publications_cache_does_not_collide_with_projects(tmp_path) -> None:
    # Same payload shape to both endpoints must produce distinct cache files.
    http = _mock_client(PUBLICATIONS_RESPONSE)
    client = ReporterClient(client=http, cache_dir=tmp_path, use_cache=True)
    client.get_publications("R01ES032470")
    files = [p.name for p in tmp_path.glob("*.json")]
    assert any(f.startswith("publications_") for f in files)


# --------------------------------------------------------------------------- #
# Edge construction
# --------------------------------------------------------------------------- #


def test_award_publication_edge_is_authoritative() -> None:
    edge = linkage.award_publication_edge("R01ES032470", "37100513")
    assert edge.subject == "AWARD:R01ES032470"
    assert edge.object == "PUBLICATION:37100513"
    assert edge.predicate == "produced"
    assert edge.authoritative is True
    assert edge.confidence == 1.0
    assert edge.source == "nih_reporter"
    assert edge.evidence_pointer == "https://pubmed.ncbi.nlm.nih.gov/37100513/"


# --------------------------------------------------------------------------- #
# End-to-end linkage (all three connectors mocked)
# --------------------------------------------------------------------------- #


def test_link_award_publications_enriched(monkeypatch) -> None:
    # RePORTER publication links
    monkeypatch.setattr(
        reporter,
        "get_publications",
        lambda core, limit=500, use_cache=True: [
            normalize_publication_link({"coreproject": core, "pmid": 37100513, "applid": 1}),
            normalize_publication_link({"coreproject": core, "pmid": 37066248, "applid": 1}),
        ],
    )
    # PubMed enrichment (only one of the two has metadata available)
    monkeypatch.setattr(
        pubmed,
        "fetch_summaries",
        lambda pmids, use_cache=True: [
            pubmed.PubmedArticle(pmid="37100513", title="Linked paper", pub_year=2023, journal="J")
        ],
    )
    # iCite metrics
    monkeypatch.setattr(
        icite,
        "fetch_metrics",
        lambda pmids, use_cache=True: [
            icite.ICiteMetrics(pmid="37100513", relative_citation_ratio=3.2, citation_count=40)
        ],
    )

    links = linkage.link_award_publications("R01ES032470", enrich=True, metrics=True)
    assert len(links) == 2
    assert all(link.edge.authoritative for link in links)

    first = next(link for link in links if link.pmid == "37100513")
    assert first.article is not None and first.article.title == "Linked paper"
    assert first.metrics is not None and first.metrics.relative_citation_ratio == 3.2

    # The second PMID had no metadata/metrics — gracefully None, edge still present.
    second = next(link for link in links if link.pmid == "37066248")
    assert second.article is None
    assert second.metrics is None
    assert second.edge.object == "PUBLICATION:37066248"

    edges = linkage.edges_from_links(links)
    assert len(edges) == 2
