"""openFDA connector — drug approvals as a translation outcome.

Queries the public openFDA drug label endpoint to find FDA-approved drugs whose
labeling indicates a given condition. This is the far end of the translation
axis: a therapy reaching patients. No key required (a key only raises limits).

Approvals are a *translation* signal for a disease area; like the CDC outcomes,
they are juxtaposed with NIH funding, not causally attributed to any one grant.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field

from nih_science_agent.connectors._http import CachedClient

DRUG_LABEL_URL = "https://api.fda.gov/drug/label.json"
DRUGSFDA_URL = "https://api.fda.gov/drug/drugsfda.json"


class DrugApproval(BaseModel):
    brand_names: list[str] = Field(default_factory=list)
    generic_names: list[str] = Field(default_factory=list)
    manufacturer: str | None = None
    application_numbers: list[str] = Field(default_factory=list)
    route: list[str] = Field(default_factory=list)
    indication_snippet: str | None = None
    source_url: str | None = None
    retrieved_at: str | None = None


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _first(values: Any) -> str | None:
    if isinstance(values, list) and values:
        return values[0]
    return None


def normalize_label(raw: dict[str, Any], retrieved_at: str | None = None) -> DrugApproval:
    """Map an openFDA drug-label record onto a :class:`DrugApproval`."""
    of = raw.get("openfda") or {}
    indication = _first(raw.get("indications_and_usage"))
    snippet = indication[:300] if isinstance(indication, str) else None
    app_nums = of.get("application_number") or []
    return DrugApproval(
        brand_names=list(of.get("brand_name") or []),
        generic_names=list(of.get("generic_name") or []),
        manufacturer=_first(of.get("manufacturer_name")),
        application_numbers=list(app_nums),
        route=list(of.get("route") or []),
        indication_snippet=snippet,
        source_url=(
            f"https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event="
            f"overview.process&ApplNo={app_nums[0]}"
            if app_nums
            else None
        ),
        retrieved_at=retrieved_at or _utcnow_iso(),
    )


class FDAClient(CachedClient):
    """Cached client over the openFDA drug endpoints."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(cache_subdir="fda", **kwargs)

    def drugs_for_indication(
        self, indication: str, limit: int = 20
    ) -> tuple[int, list[DrugApproval]]:
        """Return (total_count, drugs) whose labeling indicates ``indication``.

        ``total_count`` is openFDA's reported match total (often far larger than
        the returned page); ``drugs`` are the first ``limit`` normalized records.
        Returns ``(0, [])`` when openFDA has no match (it 404s on zero results).
        """
        params = {
            "search": f'indications_and_usage:"{indication}"',
            "limit": min(limit, 100),
        }
        ts = _utcnow_iso()
        try:
            data = self.get_json(DRUG_LABEL_URL, params=params)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return 0, []
            raise
        total = (data.get("meta") or {}).get("results", {}).get("total", 0)
        drugs = [normalize_label(r, retrieved_at=ts) for r in data.get("results") or []]
        return total, drugs


def drugs_for_indication(
    indication: str, limit: int = 20, use_cache: bool = True
) -> tuple[int, list[DrugApproval]]:
    with FDAClient(use_cache=use_cache) as client:
        return client.drugs_for_indication(indication, limit=limit)
