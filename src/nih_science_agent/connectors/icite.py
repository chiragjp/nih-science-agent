"""iCite connector — NIH citation metrics for PMIDs.

iCite (https://icite.od.nih.gov/) exposes citation-based influence metrics,
most notably the Relative Citation Ratio (RCR) — a field- and time-normalized
measure where 1.0 is the median NIH-funded paper. The public API needs no key.

Public surface:

- ``fetch_metrics(pmids)`` -> list[ICiteMetrics]
- ``get_metrics(pmid)`` -> ICiteMetrics | None
- ``normalize_metrics(raw)`` -> ICiteMetrics
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from nih_science_agent.connectors._http import CachedClient

ICITE_BASE = "https://icite.od.nih.gov/api"
ICITE_PUBS_URL = f"{ICITE_BASE}/pubs"
ICITE_PUB_URL = "https://icite.od.nih.gov/analysis?search_id={pmid}"

# The /pubs endpoint accepts a comma-separated pmids list; batch to stay polite.
MAX_BATCH = 200


class ICiteMetrics(BaseModel):
    """Normalized iCite metrics for a single article."""

    pmid: str | None = None
    year: int | None = None
    title: str | None = None
    journal: str | None = None
    relative_citation_ratio: float | None = None
    nih_percentile: float | None = None
    citation_count: int | None = None
    citations_per_year: float | None = None
    expected_citations_per_year: float | None = None
    field_citation_rate: float | None = None
    is_research_article: bool | None = None
    is_clinical: bool | None = None
    doi: str | None = None
    source_url: str | None = None
    retrieved_at: str | None = None


class TranslationRecord(BaseModel):
    """iCite translation signals for one article."""

    pmid: str | None = None
    year: int | None = None
    apt: float | None = None  # Approximate Potential to Translate (0–1)
    is_clinical: bool | None = None
    clinical_citers: list[str] = []  # cited_by_clin: clinical articles citing this
    retrieved_at: str | None = None


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def normalize_translation(
    raw: dict[str, Any], retrieved_at: str | None = None
) -> TranslationRecord:
    pmid = raw.get("pmid")
    citers = raw.get("cited_by_clin") or []
    return TranslationRecord(
        pmid=str(pmid) if pmid is not None else None,
        year=raw.get("year"),
        apt=_to_float(raw.get("apt")),
        is_clinical=_as_bool(raw.get("is_clinical")),
        clinical_citers=[str(c) for c in citers if c is not None],
        retrieved_at=retrieved_at or _utcnow_iso(),
    )


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_bool(value: Any) -> bool | None:
    """iCite returns "Yes"/"No" or booleans for flag fields."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"yes", "true", "1"}:
            return True
        if v in {"no", "false", "0"}:
            return False
    return None


def normalize_metrics(raw: dict[str, Any], retrieved_at: str | None = None) -> ICiteMetrics:
    """Map one iCite ``data`` entry onto an :class:`ICiteMetrics` record."""
    if not isinstance(raw, dict):
        raise TypeError(f"expected a dict, got {type(raw).__name__}")

    pmid = raw.get("pmid")
    return ICiteMetrics(
        pmid=str(pmid) if pmid is not None else None,
        year=raw.get("year"),
        title=raw.get("title"),
        journal=raw.get("journal"),
        relative_citation_ratio=raw.get("relative_citation_ratio"),
        nih_percentile=raw.get("nih_percentile"),
        citation_count=raw.get("citation_count"),
        citations_per_year=raw.get("citations_per_year"),
        expected_citations_per_year=raw.get("expected_citations_per_year"),
        field_citation_rate=raw.get("field_citation_rate"),
        is_research_article=_as_bool(raw.get("is_research_article")),
        is_clinical=_as_bool(raw.get("is_clinical")),
        doi=raw.get("doi"),
        source_url=ICITE_PUB_URL.format(pmid=pmid) if pmid is not None else None,
        retrieved_at=retrieved_at or _utcnow_iso(),
    )


class ICiteClient(CachedClient):
    """Cached client over the iCite /pubs endpoint."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(cache_subdir="icite", **kwargs)

    def fetch_metrics(self, pmids: list[str]) -> list[ICiteMetrics]:
        """Fetch metrics for ``pmids`` (batched, order preserved where returned)."""
        if not pmids:
            return []
        retrieved_at = _utcnow_iso()
        by_pmid: dict[str, ICiteMetrics] = {}
        for start in range(0, len(pmids), MAX_BATCH):
            batch = pmids[start : start + MAX_BATCH]
            data = self.get_json(ICITE_PUBS_URL, params={"pmids": ",".join(batch)})
            for rec in data.get("data") or []:
                m = normalize_metrics(rec, retrieved_at=retrieved_at)
                if m.pmid:
                    by_pmid[m.pmid] = m
        # Preserve the caller's order; drop pmids iCite had no record for.
        return [by_pmid[p] for p in pmids if p in by_pmid]

    def get_metrics(self, pmid: str) -> ICiteMetrics | None:
        results = self.fetch_metrics([pmid])
        return results[0] if results else None

    def fetch_translation(self, pmids: list[str]) -> list[TranslationRecord]:
        """Fetch translation signals for ``pmids``: APT + the clinical-citer list.

        ``cited_by_clin`` is iCite's precomputed list of *clinical* articles citing
        a paper — the basic→clinical citation edge, no NLP required.
        """
        if not pmids:
            return []
        ts = _utcnow_iso()
        by_pmid: dict[str, TranslationRecord] = {}
        for start in range(0, len(pmids), MAX_BATCH):
            batch = pmids[start : start + MAX_BATCH]
            data = self.get_json(ICITE_PUBS_URL, params={"pmids": ",".join(batch)})
            for rec in data.get("data") or []:
                t = normalize_translation(rec, retrieved_at=ts)
                if t.pmid:
                    by_pmid[t.pmid] = t
        return [by_pmid[p] for p in pmids if p in by_pmid]


# --------------------------------------------------------------------------- #
# Module-level convenience functions
# --------------------------------------------------------------------------- #


def fetch_metrics(pmids: list[str], use_cache: bool = True) -> list[ICiteMetrics]:
    with ICiteClient(use_cache=use_cache) as client:
        return client.fetch_metrics(pmids)


def get_metrics(pmid: str, use_cache: bool = True) -> ICiteMetrics | None:
    with ICiteClient(use_cache=use_cache) as client:
        return client.get_metrics(pmid)


def fetch_translation(pmids: list[str], use_cache: bool = True) -> list[TranslationRecord]:
    with ICiteClient(use_cache=use_cache) as client:
        return client.fetch_translation(pmids)
