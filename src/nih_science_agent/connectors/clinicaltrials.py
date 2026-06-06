"""ClinicalTrials.gov connector (API v2).

Search and fetch clinical study records from the public ClinicalTrials.gov v2
API (https://clinicaltrials.gov/api/v2/). No key required.

Public surface:

- ``search_trials(condition, intervention, sponsor, query, has_results, limit=100)``
- ``get_trial(nct_id)``
- ``normalize_trial(raw)``

The raw v2 schema is deeply nested under ``protocolSection`` modules; normalization
flattens the fields the design doc calls for onto a stable :class:`Trial` record.
Study references carry PMIDs (``DERIVED``/``RESULT``/``BACKGROUND``), which feed
the trial→publication edges of the linkage layer later.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, Field

from nih_science_agent.connectors._http import CachedClient

API_BASE = "https://clinicaltrials.gov/api/v2"
STUDIES_URL = f"{API_BASE}/studies"
STUDY_URL = f"{API_BASE}/studies/{{nct_id}}"
TRIAL_DETAIL_URL = "https://clinicaltrials.gov/study/{nct_id}"

# v2 caps pageSize at 1000.
MAX_PAGE_SIZE = 1000

# NIH core grant number: activity code + IC (2 letters) + 6-digit serial,
# e.g. R01DK075877, U01AI155325, P01HL131478. Application-type prefix and
# support-year suffix (5R01...-03) are stripped to recover the core number.
_GRANT_RE = re.compile(r"([A-Z][A-Z0-9]\d)([A-Z]{2})(\d{6})")


class TrialReference(BaseModel):
    pmid: str | None = None
    type: str | None = None  # DERIVED | RESULT | BACKGROUND
    citation: str | None = None


class Trial(BaseModel):
    """Normalized ClinicalTrials.gov study record."""

    nct_id: str | None = None
    brief_title: str | None = None
    official_title: str | None = None
    conditions: list[str] = Field(default_factory=list)
    interventions: list[str] = Field(default_factory=list)
    sponsors: list[str] = Field(default_factory=list)
    collaborators: list[str] = Field(default_factory=list)
    phase: str | None = None
    enrollment: int | None = None
    start_date: str | None = None
    completion_date: str | None = None
    overall_status: str | None = None
    has_results: bool | None = None
    references: list[TrialReference] = Field(default_factory=list)
    # Authoritative NIH core grant numbers the study itself reports (from CT.gov
    # secondaryIdInfos type=NIH / OTHER_GRANT). These tie a trial to its award.
    nih_grant_numbers: list[str] = Field(default_factory=list)
    source_url: str | None = None
    retrieved_at: str | None = None


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _date(struct: Any) -> str | None:
    if isinstance(struct, dict):
        return struct.get("date")
    return None


def _extract_nih_grants(ident: dict[str, Any]) -> list[str]:
    """Pull NIH core grant numbers from a study's secondaryIdInfos.

    Handles both structured ``type=NIH`` ids (e.g. ``5R01DK075877-03``) and
    free-text ``OTHER_GRANT`` ids (e.g. ``U.S. NIH Grant 5R18DK096394``). Returns
    de-duplicated core project numbers, prefix/suffix stripped.
    """
    grants: list[str] = []
    seen: set[str] = set()
    for sid in ident.get("secondaryIdInfos") or []:
        if not isinstance(sid, dict):
            continue
        sid_type = (sid.get("type") or "").upper()
        domain = (sid.get("domain") or "").upper()
        is_nih = sid_type == "NIH" or "NIH" in domain or "nih.gov" in (sid.get("link") or "")
        if sid_type == "OTHER_GRANT" and not is_nih:
            # OTHER_GRANT covers many funders; only treat as NIH if it parses
            # to a grant number AND mentions NIH.
            is_nih = "NIH" in (sid.get("id") or "").upper()
        if not is_nih:
            continue
        m = _GRANT_RE.search((sid.get("id") or "").replace(" ", "").upper())
        if m:
            core = "".join(m.groups())
            if core not in seen:
                seen.add(core)
                grants.append(core)
    return grants


def normalize_trial(raw: dict[str, Any], retrieved_at: str | None = None) -> Trial:
    """Map a raw v2 study object onto a :class:`Trial`.

    ``raw`` is a single study dict (top-level ``protocolSection`` + ``hasResults``),
    as returned by both the single-study endpoint and each item of a search.
    Defensive: missing modules normalize to ``None``/empty.
    """
    if not isinstance(raw, dict):
        raise TypeError(f"expected a dict, got {type(raw).__name__}")

    ps = raw.get("protocolSection") or {}
    ident = ps.get("identificationModule") or {}
    status = ps.get("statusModule") or {}
    sponsor_mod = ps.get("sponsorCollaboratorsModule") or {}
    design = ps.get("designModule") or {}
    conditions_mod = ps.get("conditionsModule") or {}
    arms = ps.get("armsInterventionsModule") or {}
    refs_mod = ps.get("referencesModule") or {}

    nct_id = ident.get("nctId")

    lead = sponsor_mod.get("leadSponsor") or {}
    sponsors = [lead["name"]] if lead.get("name") else []
    collaborators = [
        c["name"]
        for c in (sponsor_mod.get("collaborators") or [])
        if isinstance(c, dict) and c.get("name")
    ]

    interventions = [
        f"{i.get('type')}: {i.get('name')}" if i.get("type") else i.get("name")
        for i in (arms.get("interventions") or [])
        if isinstance(i, dict) and i.get("name")
    ]

    phases = design.get("phases") or []
    phase = "/".join(phases) if phases else None
    enrollment = (design.get("enrollmentInfo") or {}).get("count")

    references = [
        TrialReference(
            pmid=str(r["pmid"]) if r.get("pmid") else None,
            type=r.get("type"),
            citation=r.get("citation"),
        )
        for r in (refs_mod.get("references") or [])
        if isinstance(r, dict)
    ]

    return Trial(
        nct_id=nct_id,
        brief_title=ident.get("briefTitle"),
        official_title=ident.get("officialTitle"),
        conditions=list(conditions_mod.get("conditions") or []),
        interventions=interventions,
        sponsors=sponsors,
        collaborators=collaborators,
        phase=phase,
        enrollment=enrollment,
        start_date=_date(status.get("startDateStruct")),
        completion_date=_date(status.get("completionDateStruct"))
        or _date(status.get("primaryCompletionDateStruct")),
        overall_status=status.get("overallStatus"),
        has_results=raw.get("hasResults"),
        references=references,
        nih_grant_numbers=_extract_nih_grants(ident),
        source_url=TRIAL_DETAIL_URL.format(nct_id=nct_id) if nct_id else None,
        retrieved_at=retrieved_at or _utcnow_iso(),
    )


def _search_params(
    condition: str | None,
    intervention: str | None,
    sponsor: str | None,
    query: str | None,
    has_results: bool | None,
) -> dict[str, Any]:
    params: dict[str, Any] = {"countTotal": "true"}
    if condition:
        params["query.cond"] = condition
    if intervention:
        params["query.intr"] = intervention
    if sponsor:
        params["query.spons"] = sponsor
    if query:
        params["query.term"] = query
    if has_results is True:
        params["aggFilters"] = "results:with"
    elif has_results is False:
        params["aggFilters"] = "results:without"
    return params


class ClinicalTrialsClient(CachedClient):
    """Cached client over the ClinicalTrials.gov v2 API."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(cache_subdir="clinicaltrials", **kwargs)

    def count_trials(
        self,
        condition: str | None = None,
        intervention: str | None = None,
        sponsor: str | None = None,
        query: str | None = None,
        has_results: bool | None = None,
    ) -> int:
        """Return the total number of studies matching the filters (no fetch)."""
        params = _search_params(condition, intervention, sponsor, query, has_results)
        params["pageSize"] = 1
        data = self.get_json(STUDIES_URL, params=params)
        return int(data.get("totalCount") or 0)

    def search_trials(
        self,
        condition: str | None = None,
        intervention: str | None = None,
        sponsor: str | None = None,
        query: str | None = None,
        has_results: bool | None = None,
        limit: int = 100,
    ) -> list[Trial]:
        """Search studies. Filters map to the v2 ``query.*`` and ``aggFilters`` params."""
        base_params = _search_params(condition, intervention, sponsor, query, has_results)
        retrieved_at = _utcnow_iso()
        trials: list[Trial] = []
        page_token: str | None = None
        while len(trials) < limit:
            params = dict(base_params)
            params["pageSize"] = min(MAX_PAGE_SIZE, limit - len(trials))
            if page_token:
                params["pageToken"] = page_token
            data = self.get_json(STUDIES_URL, params=params)
            for raw in data.get("studies") or []:
                trials.append(normalize_trial(raw, retrieved_at=retrieved_at))
            page_token = data.get("nextPageToken")
            if not page_token or not (data.get("studies")):
                break

        return trials[:limit]

    def get_trial(self, nct_id: str) -> Trial | None:
        """Fetch a single study by NCT id."""
        try:
            data = self.get_json(STUDY_URL.format(nct_id=nct_id))
        except httpx.HTTPStatusError:
            return None
        if not isinstance(data, dict) or not data.get("protocolSection"):
            return None
        return normalize_trial(data)


# --------------------------------------------------------------------------- #
# Module-level convenience functions
# --------------------------------------------------------------------------- #


def search_trials(
    condition: str | None = None,
    intervention: str | None = None,
    sponsor: str | None = None,
    query: str | None = None,
    has_results: bool | None = None,
    limit: int = 100,
    use_cache: bool = True,
) -> list[Trial]:
    with ClinicalTrialsClient(use_cache=use_cache) as client:
        return client.search_trials(
            condition=condition,
            intervention=intervention,
            sponsor=sponsor,
            query=query,
            has_results=has_results,
            limit=limit,
        )


def get_trial(nct_id: str, use_cache: bool = True) -> Trial | None:
    with ClinicalTrialsClient(use_cache=use_cache) as client:
        return client.get_trial(nct_id)


def count_trials(
    condition: str | None = None,
    intervention: str | None = None,
    sponsor: str | None = None,
    query: str | None = None,
    has_results: bool | None = None,
    use_cache: bool = True,
) -> int:
    with ClinicalTrialsClient(use_cache=use_cache) as client:
        return client.count_trials(
            condition=condition,
            intervention=intervention,
            sponsor=sponsor,
            query=query,
            has_results=has_results,
        )


def find_trials_for_grant(
    core_project_num: str, limit: int = 100, use_cache: bool = True
) -> list[Trial]:
    """Find trials that authoritatively report ``core_project_num`` as a funder.

    Searches ClinicalTrials.gov for the grant number, then keeps only studies
    whose parsed ``nih_grant_numbers`` actually include it — so a stray text
    match never produces a false award→trial link.
    """
    core = core_project_num.upper()
    candidates = search_trials(query=core_project_num, limit=limit, use_cache=use_cache)
    return [t for t in candidates if core in [g.upper() for g in t.nih_grant_numbers]]
