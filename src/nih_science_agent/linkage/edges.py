"""Typed edges for the linkage layer.

Every edge between awards, publications, trials, etc. carries provenance,
method, and confidence — and is explicitly tagged as **authoritative** (asserted
directly by a source, e.g. RePORTER's own publication list) or **inferred**
(derived by matching, e.g. PI + topic). Coverage and bias audits (Task 5b) read
these fields; downstream tools must never silently mix the two kinds.

This module currently builds the first edge type — AWARD→PUBLICATION from
RePORTER's authoritative publication links — and is the seam where inferred
linkers (grant-text mining, PI+topic) will attach later.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from nih_science_agent.connectors import icite, pubmed, reporter

# Node id prefixes keep subject/object globally unambiguous in a future graph.
AWARD = "AWARD"
PUBLICATION = "PUBLICATION"
CLINICAL_TRIAL = "CLINICAL_TRIAL"
PI = "PI"
INSTITUTION = "INSTITUTION"
NIH_IC = "NIH_IC"
FOA = "FOA"
TOPIC_TERM = "TOPIC_TERM"
DATASET = "DATASET"
CONDITION = "CONDITION"
HEALTH_INDICATOR = "HEALTH_INDICATOR"
DRUG_APPROVAL = "DRUG_APPROVAL"


def award_trial_edge(core_project_num: str, nct_id: str, retrieved_at: str | None = None) -> Edge:
    """Build an authoritative AWARD→CLINICAL_TRIAL edge.

    Authoritative because the study itself reports the grant number in its
    ClinicalTrials.gov ``secondaryIdInfos`` (type=NIH), with a RePORTER link.
    """
    return Edge(
        subject=f"{AWARD}:{core_project_num}",
        predicate="funded_trial",
        object=f"{CLINICAL_TRIAL}:{nct_id}",
        source="clinicaltrials_gov",
        method="ctgov_nih_secondary_id",
        authoritative=True,
        confidence=1.0,
        evidence_pointer=f"https://clinicaltrials.gov/study/{nct_id}",
        retrieved_at=retrieved_at or _utcnow_iso(),
    )


class Edge(BaseModel):
    """A typed, provenance-bearing edge between two nodes."""

    subject: str  # e.g. "AWARD:R01ES032470"
    predicate: str  # e.g. "produced"
    object: str  # e.g. "PUBLICATION:37100513"
    source: str  # data source that yielded the edge, e.g. "nih_reporter"
    method: str  # how it was derived, e.g. "reporter_publication_link"
    authoritative: bool  # True = asserted by a source; False = inferred
    confidence: float  # 1.0 for authoritative links
    evidence_pointer: str | None = None  # URL/locator backing the edge
    retrieved_at: str | None = None


class LinkedPublication(BaseModel):
    """A publication linked to an award, enriched with optional metadata."""

    pmid: str
    edge: Edge
    article: pubmed.PubmedArticle | None = None
    metrics: icite.ICiteMetrics | None = None


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def award_publication_edge(
    core_project_num: str, pmid: str, retrieved_at: str | None = None
) -> Edge:
    """Build a single authoritative AWARD→PUBLICATION edge."""
    return Edge(
        subject=f"{AWARD}:{core_project_num}",
        predicate="produced",
        object=f"{PUBLICATION}:{pmid}",
        source="nih_reporter",
        method="reporter_publication_link",
        authoritative=True,
        confidence=1.0,
        evidence_pointer=f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
        retrieved_at=retrieved_at or _utcnow_iso(),
    )


def link_award_publications(
    core_project_num: str,
    enrich: bool = True,
    metrics: bool = False,
    limit: int = 500,
    use_cache: bool = True,
) -> list[LinkedPublication]:
    """Link an award to its publications via RePORTER's authoritative list.

    Returns one :class:`LinkedPublication` per PMID, each carrying an
    authoritative :class:`Edge`. When ``enrich`` is set, PubMed metadata is
    attached; when ``metrics`` is set, iCite RCR/citation metrics are too.
    """
    links = reporter.get_publications(core_project_num, limit=limit, use_cache=use_cache)
    pmids = [link.pmid for link in links if link.pmid]
    retrieved_at = _utcnow_iso()

    articles_by_pmid: dict[str, pubmed.PubmedArticle] = {}
    if enrich and pmids:
        articles_by_pmid = {
            a.pmid: a for a in pubmed.fetch_summaries(pmids, use_cache=use_cache) if a.pmid
        }

    metrics_by_pmid: dict[str, icite.ICiteMetrics] = {}
    if metrics and pmids:
        metrics_by_pmid = {
            m.pmid: m for m in icite.fetch_metrics(pmids, use_cache=use_cache) if m.pmid
        }

    out: list[LinkedPublication] = []
    for pmid in pmids:
        out.append(
            LinkedPublication(
                pmid=pmid,
                edge=award_publication_edge(core_project_num, pmid, retrieved_at=retrieved_at),
                article=articles_by_pmid.get(pmid),
                metrics=metrics_by_pmid.get(pmid),
            )
        )
    return out


def edges_from_links(links: list[LinkedPublication]) -> list[Edge]:
    """Extract just the typed edges from a list of linked publications."""
    return [link.edge for link in links]
