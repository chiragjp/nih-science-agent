"""NAMs connector — NICEATM Integrated Chemical Environment (ICE).

ICE (https://ice.ntp.niehs.nih.gov/) aggregates curated chemical bioactivity and
**New Approach Methodology (NAM)** assay results — the alternative-test-method
data relevant to NICEATM / DNICEATM. The public REST API returns assay/endpoint
records for a chemical (by CASRN or DTXSID).

Public surface:
- ``chemical_assays(chemid)`` -> list[AssayResult]
- ``chemical_assay_summary(chemid)`` -> AssaySummary (counts by assay/endpoint)
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field

from nih_science_agent.connectors._http import CachedClient

ICE_SEARCH_URL = "https://ice.ntp.niehs.nih.gov/api/v1/search"
ICE_DETAIL_URL = "https://ice.ntp.niehs.nih.gov/#/search?chem={chemid}"


class AssayResult(BaseModel):
    casrn: str | None = None
    assay: str | None = None
    endpoint: str | None = None
    value: str | None = None
    unit: str | None = None
    species: str | None = None
    substance_type: str | None = None
    route: str | None = None


class AssaySummary(BaseModel):
    chemid: str
    total_records: int = 0
    distinct_assays: int = 0
    distinct_endpoints: int = 0
    top_assays: list[dict[str, Any]] = Field(default_factory=list)
    top_endpoints: list[dict[str, Any]] = Field(default_factory=list)
    source_url: str | None = None
    retrieved_at: str | None = None


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def normalize_assay(raw: dict[str, Any]) -> AssayResult:
    return AssayResult(
        casrn=raw.get("casrn"),
        assay=raw.get("assay"),
        endpoint=raw.get("endpoint"),
        value=str(raw.get("value")) if raw.get("value") is not None else None,
        unit=raw.get("unit"),
        species=raw.get("species"),
        substance_type=raw.get("substanceType"),
        route=raw.get("route"),
    )


class IceClient(CachedClient):
    """Cached client over the NICEATM ICE search API."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(cache_subdir="nams", **kwargs)

    def _endpoints(self, chemid: str) -> list[dict[str, Any]]:
        data = self.get_json(ICE_SEARCH_URL, params={"chemid": chemid})
        return data.get("endPoints") or [] if isinstance(data, dict) else []

    def chemical_assays(self, chemid: str, limit: int = 500) -> list[AssayResult]:
        """Assay/endpoint records for a chemical (CASRN or DTXSID), capped at ``limit``."""
        return [normalize_assay(r) for r in self._endpoints(chemid)[:limit]]

    def chemical_assay_summary(self, chemid: str) -> AssaySummary:
        """Summarize a chemical's NAMs assay coverage (counts by assay/endpoint)."""
        rows = self._endpoints(chemid)
        assays = Counter(r.get("assay") for r in rows if r.get("assay"))
        endpoints = Counter(r.get("endpoint") for r in rows if r.get("endpoint"))
        return AssaySummary(
            chemid=chemid,
            total_records=len(rows),
            distinct_assays=len(assays),
            distinct_endpoints=len(endpoints),
            top_assays=[{"assay": a, "n": n} for a, n in assays.most_common(10)],
            top_endpoints=[{"endpoint": e, "n": n} for e, n in endpoints.most_common(10)],
            source_url=ICE_DETAIL_URL.format(chemid=chemid),
            retrieved_at=_utcnow_iso(),
        )


def chemical_assays(chemid: str, limit: int = 500, use_cache: bool = True) -> list[AssayResult]:
    with IceClient(use_cache=use_cache) as client:
        return client.chemical_assays(chemid, limit=limit)


def chemical_assay_summary(chemid: str, use_cache: bool = True) -> AssaySummary:
    with IceClient(use_cache=use_cache) as client:
        return client.chemical_assay_summary(chemid)
