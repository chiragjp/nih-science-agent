"""PubMed connector via NCBI E-utilities.

Search PubMed and fetch article metadata for a set of PMIDs. Uses the public
E-utilities endpoints (``esearch`` + ``esummary``); an optional
``NCBI_API_KEY`` (env var) raises the rate limit from 3 to 10 requests/sec.

Public surface:

- ``search(query, retmax=100)`` -> list of PMIDs
- ``fetch_summaries(pmids)`` -> list[PubmedArticle]
- ``search_articles(query, retmax=100)`` -> list[PubmedArticle] (search + fetch)
- ``normalize_summary(raw)`` -> PubmedArticle

Abstracts are not returned by ``esummary``; fetching full abstract text
(``efetch``) is deferred until a tool needs it, to keep this connector light.
"""

from __future__ import annotations

import os
import re
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from nih_science_agent.connectors._http import CachedClient

EUTILS_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
ESEARCH_URL = f"{EUTILS_BASE}/esearch.fcgi"
ESUMMARY_URL = f"{EUTILS_BASE}/esummary.fcgi"
ARTICLE_URL = "https://pubmed.ncbi.nlm.nih.gov/{pmid}/"

# esummary tops out around 500 ids per request.
MAX_SUMMARY_BATCH = 200

_YEAR_RE = re.compile(r"(\d{4})")


class PubmedArticle(BaseModel):
    """Normalized PubMed article record."""

    pmid: str | None = None
    title: str | None = None
    journal: str | None = None
    pub_year: int | None = None
    pub_date: str | None = None
    authors: list[str] = Field(default_factory=list)
    doi: str | None = None
    pmcid: str | None = None
    pub_types: list[str] = Field(default_factory=list)
    source_url: str | None = None
    retrieved_at: str | None = None


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _extract_article_ids(raw: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return ``(doi, pmcid)`` from an esummary ``articleids`` list."""
    doi = pmcid = None
    for item in raw.get("articleids") or []:
        if not isinstance(item, dict):
            continue
        idtype = item.get("idtype")
        value = item.get("value")
        if idtype == "doi" and value:
            doi = value
        elif idtype == "pmc" and value:
            pmcid = value
    return doi, pmcid


def normalize_summary(raw: dict[str, Any], retrieved_at: str | None = None) -> PubmedArticle:
    """Map one esummary record (the per-uid dict) onto a :class:`PubmedArticle`."""
    if not isinstance(raw, dict):
        raise TypeError(f"expected a dict, got {type(raw).__name__}")

    pmid = raw.get("uid")
    pub_date = raw.get("pubdate") or raw.get("epubdate") or None
    pub_year = None
    if pub_date:
        m = _YEAR_RE.search(str(pub_date))
        if m:
            pub_year = int(m.group(1))

    authors = [
        a["name"] for a in (raw.get("authors") or []) if isinstance(a, dict) and a.get("name")
    ]
    doi, pmcid = _extract_article_ids(raw)

    return PubmedArticle(
        pmid=str(pmid) if pmid is not None else None,
        title=(raw.get("title") or "").rstrip(".") or None,
        journal=raw.get("fulljournalname") or raw.get("source"),
        pub_year=pub_year,
        pub_date=pub_date,
        authors=authors,
        doi=doi,
        pmcid=pmcid,
        pub_types=list(raw.get("pubtype") or []),
        source_url=ARTICLE_URL.format(pmid=pmid) if pmid is not None else None,
        retrieved_at=retrieved_at or _utcnow_iso(),
    )


class PubmedClient(CachedClient):
    """Cached client over NCBI E-utilities (esearch + esummary)."""

    def __init__(self, api_key: str | None = None, **kwargs: Any) -> None:
        super().__init__(cache_subdir="pubmed", **kwargs)
        self.api_key = api_key or os.environ.get("NCBI_API_KEY")

    def _common_params(self) -> dict[str, Any]:
        params: dict[str, Any] = {"db": "pubmed", "retmode": "json", "tool": "nih-science-agent"}
        if self.api_key:
            params["api_key"] = self.api_key
        return params

    def search(self, query: str, retmax: int = 100, sort: str = "relevance") -> list[str]:
        """Return PMIDs matching ``query``.

        ``sort`` defaults to ``"relevance"`` (PubMed best-match, which surfaces
        established papers); pass ``"most+recent"`` for newest-first.
        """
        params = self._common_params()
        params.update({"term": query, "retmax": retmax, "sort": sort})
        data = self.get_json(ESEARCH_URL, params=params)
        return list((data.get("esearchresult") or {}).get("idlist") or [])

    def count(self, query: str) -> int:
        """Return the number of PubMed records matching ``query`` (no fetch)."""
        params = self._common_params()
        params.update({"term": query, "retmax": 0})
        data = self.get_json(ESEARCH_URL, params=params)
        try:
            return int((data.get("esearchresult") or {}).get("count") or 0)
        except (TypeError, ValueError):
            return 0

    def fetch_summaries(self, pmids: list[str]) -> list[PubmedArticle]:
        """Fetch article metadata for ``pmids`` (batched, order preserved)."""
        if not pmids:
            return []
        retrieved_at = _utcnow_iso()
        out: list[PubmedArticle] = []
        for start in range(0, len(pmids), MAX_SUMMARY_BATCH):
            batch = pmids[start : start + MAX_SUMMARY_BATCH]
            params = self._common_params()
            params["id"] = ",".join(batch)
            data = self.get_json(ESUMMARY_URL, params=params)
            result = data.get("result") or {}
            for pmid in batch:
                rec = result.get(pmid)
                if isinstance(rec, dict):
                    out.append(normalize_summary(rec, retrieved_at=retrieved_at))
        return out

    def search_articles(self, query: str, retmax: int = 100) -> list[PubmedArticle]:
        """Convenience: search then fetch summaries for the matching PMIDs."""
        return self.fetch_summaries(self.search(query, retmax=retmax))


# --------------------------------------------------------------------------- #
# Module-level convenience functions
# --------------------------------------------------------------------------- #


def search(query: str, retmax: int = 100, use_cache: bool = True) -> list[str]:
    with PubmedClient(use_cache=use_cache) as client:
        return client.search(query, retmax=retmax)


def count(query: str, use_cache: bool = True) -> int:
    with PubmedClient(use_cache=use_cache) as client:
        return client.count(query)


def fetch_summaries(pmids: list[str], use_cache: bool = True) -> list[PubmedArticle]:
    with PubmedClient(use_cache=use_cache) as client:
        return client.fetch_summaries(pmids)


def search_articles(query: str, retmax: int = 100, use_cache: bool = True) -> list[PubmedArticle]:
    with PubmedClient(use_cache=use_cache) as client:
        return client.search_articles(query, retmax=retmax)
