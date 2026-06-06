"""Funding Opportunity Announcement (FOA / RFA / NOFO) connector.

ExPORTER already gives each award its FOA *identifier* (the ``foa_number``
column). This connector resolves that id to the announcement's structured
metadata, so funding *policy* can be linked to outputs (e.g. do targeted RFAs
produce different science than parent program announcements?).

Two sources, both keyed on the FOA number:
- **grants.gov Search2 API** — structured JSON, covers active *and* archived NIH
  NOFOs (no key required). Primary.
- **NIH Guide** — deterministic URL ``grants.nih.gov/grants/guide/{type}-files/{FOA}.html``,
  durable even for long-expired FOAs. Used for the human-facing link.

The FOA **type** (RFA vs PA vs PAR …) is derivable from the number's prefix
alone — no fetch needed — which is what powers the population-scale
"by FOA type" analyses in the DuckDB store.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from nih_science_agent.connectors._http import CachedClient

GRANTS_GOV_SEARCH2 = "https://api.grants.gov/v1/api/search2"
NIH_GUIDE_URL = "https://grants.nih.gov/grants/guide/{folder}-files/{foa}.html"

# FOA-number prefix → (type label, NIH Guide folder).
_PREFIX = [
    ("RFA-", "RFA", "rfa"),  # Request for Applications (targeted, set-aside funds)
    ("PAR-", "PAR", "pa"),  # Program Announcement with special Review
    ("PAS-", "PAS", "pa"),  # Program Announcement with Set-aside funds
    ("PA-", "PA", "pa"),  # parent / program announcement
    ("NOT-", "NOTICE", "notice"),
]


def foa_type(foa_number: str | None) -> str:
    """Classify an FOA number into RFA / PAR / PAS / PA / NOTICE / OTHER / NONE."""
    if not foa_number:
        return "NONE"
    u = foa_number.strip().upper()
    for prefix, label, _ in _PREFIX:
        if u.startswith(prefix):
            return label
    return "OTHER"


def guide_url(foa_number: str) -> str:
    """Deterministic NIH Guide URL for an FOA number (durable for expired FOAs)."""
    u = foa_number.strip().upper()
    folder = "pa"
    for prefix, _, fold in _PREFIX:
        if u.startswith(prefix):
            folder = fold
            break
    return NIH_GUIDE_URL.format(folder=folder, foa=u)


class FoaAnnouncement(BaseModel):
    foa_number: str
    foa_type: str
    title: str | None = None
    agency: str | None = None
    status: str | None = None  # posted | closed | archived | forecasted
    open_date: str | None = None
    close_date: str | None = None
    guide_url: str | None = None
    source: str | None = None
    retrieved_at: str | None = None


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def normalize_grants_gov(hit: dict[str, Any], foa_number: str) -> FoaAnnouncement:
    return FoaAnnouncement(
        foa_number=hit.get("number") or foa_number,
        foa_type=foa_type(hit.get("number") or foa_number),
        title=hit.get("title"),
        agency=hit.get("agencyCode") or hit.get("agency"),
        status=hit.get("oppStatus"),
        open_date=hit.get("openDate"),
        close_date=hit.get("closeDate"),
        guide_url=guide_url(foa_number),
        source="grants_gov",
        retrieved_at=_utcnow_iso(),
    )


class FoaClient(CachedClient):
    """Cached client over the grants.gov Search2 API."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(cache_subdir="foa", **kwargs)

    def get_foa(self, foa_number: str) -> FoaAnnouncement:
        """Resolve an FOA number to its announcement metadata.

        Tries grants.gov; on no match, returns a minimal record (type + Guide URL
        derived from the number) so the link is always available.
        """
        body = {
            "rows": 3,
            "keyword": "",
            "oppNum": foa_number,
            "oppStatuses": "forecasted|posted|closed|archived",
        }
        data = self.post_json(GRANTS_GOV_SEARCH2, body)
        hits = (data.get("data") or {}).get("oppHits") or []
        exact = [h for h in hits if (h.get("number") or "").upper() == foa_number.upper()]
        chosen = exact[0] if exact else (hits[0] if hits else None)
        if chosen:
            return normalize_grants_gov(chosen, foa_number)
        return FoaAnnouncement(
            foa_number=foa_number,
            foa_type=foa_type(foa_number),
            guide_url=guide_url(foa_number),
            source="derived",
            retrieved_at=_utcnow_iso(),
        )


def get_foa(foa_number: str, use_cache: bool = True) -> FoaAnnouncement:
    with FoaClient(use_cache=use_cache) as client:
        return client.get_foa(foa_number)
