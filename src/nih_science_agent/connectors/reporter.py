"""NIH RePORTER v2 connector.

Typed client for the public NIH RePORTER API (https://api.reporter.nih.gov/).
Provides project search and single-project retrieval with on-disk response
caching and a deterministic normalization step.

The public surface is intentionally small:

- ``search_projects(query, fiscal_years, institutes=None, mechanisms=None, limit=100)``
- ``get_project(project_number)``
- ``normalize_reporter_project(raw)``

Normalization maps the RePORTER response schema (which is large, snake_case,
and occasionally inconsistent) onto the stable :class:`ReporterProject` record
defined here. Every record carries ``source_url`` and ``retrieved_at`` so that
downstream tools can audit provenance.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from nih_science_agent.config import get_settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.reporter.nih.gov/v2"
PROJECTS_SEARCH_URL = f"{API_BASE}/projects/search"
PUBLICATIONS_SEARCH_URL = f"{API_BASE}/publications/search"
# Human-facing detail page; RePORTER routes by application id.
PROJECT_DETAIL_URL = "https://reporter.nih.gov/project-details/{appl_id}"

# RePORTER caps a single page at 500 results and the offset window at 14,999.
MAX_PAGE_LIMIT = 500
MAX_OFFSET = 14_999

DEFAULT_TIMEOUT = 30.0

# RePORTER's ``terms`` field is a single string of angle-bracket-wrapped tokens,
# e.g. ``<aging><protein><PFAS>``. This splits it into a clean list.
_TERMS_RE = re.compile(r"<([^<>]+)>")


# --------------------------------------------------------------------------- #
# Typed records
# --------------------------------------------------------------------------- #


class PrincipalInvestigator(BaseModel):
    profile_id: int | None = None
    first_name: str | None = None
    last_name: str | None = None
    full_name: str | None = None
    is_contact_pi: bool | None = None


class Organization(BaseModel):
    name: str | None = None
    city: str | None = None
    state: str | None = None
    country: str | None = None
    # RePORTER's internal organization identifier (an integer, not a ROR).
    external_org_id: str | None = None


class ReporterProject(BaseModel):
    """Normalized NIH RePORTER project record.

    Field names follow the Task 2 contract in the design doc, not the raw
    RePORTER schema. Unknown/absent raw fields normalize to ``None`` or empty
    lists rather than raising, so a partial API response still yields a record.
    """

    project_number: str | None = None
    core_project_num: str | None = None
    application_id: int | None = None
    project_title: str | None = None
    abstract_text: str | None = None
    fiscal_year: int | None = None
    nih_institute_or_center: str | None = None
    activity_code: str | None = None
    funding_mechanism: str | None = None
    organization: Organization = Field(default_factory=Organization)
    principal_investigators: list[PrincipalInvestigator] = Field(default_factory=list)
    total_cost: float | None = None
    terms: list[str] = Field(default_factory=list)
    foa_number: str | None = None
    study_section: str | None = None
    publications: list[dict[str, Any]] = Field(default_factory=list)
    patents: list[dict[str, Any]] = Field(default_factory=list)
    clinical_studies: list[dict[str, Any]] = Field(default_factory=list)
    source_url: str | None = None
    retrieved_at: str | None = None


class ReporterPublicationLink(BaseModel):
    """An authoritative award→publication link from RePORTER's publications API.

    These are the links NIH itself asserts between a core project and a PMID —
    the authoritative edge, distinct from any inferred (PI + topic) match.
    """

    core_project_num: str | None = None
    pmid: str | None = None
    application_id: int | None = None
    retrieved_at: str | None = None


def normalize_publication_link(
    raw: dict[str, Any], retrieved_at: str | None = None
) -> ReporterPublicationLink:
    if not isinstance(raw, dict):
        raise TypeError(f"expected a dict, got {type(raw).__name__}")
    pmid = raw.get("pmid")
    return ReporterPublicationLink(
        core_project_num=raw.get("coreproject") or raw.get("core_project_num"),
        pmid=str(pmid) if pmid is not None else None,
        application_id=raw.get("applid") or raw.get("application_id"),
        retrieved_at=retrieved_at or _utcnow_iso(),
    )


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def _parse_terms(raw_terms: Any) -> list[str]:
    if not raw_terms:
        return []
    if isinstance(raw_terms, list):
        return [str(t).strip() for t in raw_terms if str(t).strip()]
    matches = _TERMS_RE.findall(str(raw_terms))
    if matches:
        return [m.strip() for m in matches if m.strip()]
    # Fall back to a plain semicolon/comma separated string.
    return [t.strip() for t in re.split(r"[;,]", str(raw_terms)) if t.strip()]


def _normalize_pis(raw_pis: Any) -> list[PrincipalInvestigator]:
    if not isinstance(raw_pis, list):
        return []
    out: list[PrincipalInvestigator] = []
    for pi in raw_pis:
        if not isinstance(pi, dict):
            continue
        full = pi.get("full_name")
        if not full:
            parts = [pi.get("first_name"), pi.get("last_name")]
            full = " ".join(p for p in parts if p) or None
        out.append(
            PrincipalInvestigator(
                profile_id=pi.get("profile_id"),
                first_name=pi.get("first_name"),
                last_name=pi.get("last_name"),
                full_name=full,
                is_contact_pi=pi.get("is_contact_pi"),
            )
        )
    return out


def _normalize_org(raw_org: Any) -> Organization:
    if not isinstance(raw_org, dict):
        return Organization()
    ext_id = raw_org.get("external_org_id")
    return Organization(
        name=raw_org.get("org_name"),
        city=raw_org.get("org_city"),
        state=raw_org.get("org_state"),
        country=raw_org.get("org_country"),
        external_org_id=str(ext_id) if ext_id is not None else None,
    )


def _first(d: dict[str, Any], *keys: str) -> Any:
    """Return the first present, non-None value among ``keys``."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def normalize_reporter_project(
    raw: dict[str, Any], retrieved_at: str | None = None
) -> ReporterProject:
    """Map a raw RePORTER project dict onto a :class:`ReporterProject`.

    Defensive by design: any missing field normalizes to ``None``/empty rather
    than raising, so a thin or schema-drifted response still produces a record.
    """
    if not isinstance(raw, dict):
        raise TypeError(f"expected a dict, got {type(raw).__name__}")

    ic = _first(raw, "agency_ic_admin")
    ic_name = None
    if isinstance(ic, dict):
        ic_name = _first(ic, "abbreviation", "name", "code")
    elif isinstance(ic, str):
        ic_name = ic

    study_section = _first(raw, "full_study_section", "study_section")
    if isinstance(study_section, dict):
        study_section = _first(study_section, "name", "srg_code")

    appl_id = _first(raw, "appl_id", "application_id")
    source_url = None
    if appl_id is not None:
        source_url = PROJECT_DETAIL_URL.format(appl_id=appl_id)

    return ReporterProject(
        project_number=_first(raw, "project_num", "core_project_num", "project_number"),
        core_project_num=_first(raw, "core_project_num", "project_num"),
        application_id=appl_id,
        project_title=_first(raw, "project_title"),
        abstract_text=_first(raw, "abstract_text"),
        fiscal_year=_first(raw, "fiscal_year"),
        nih_institute_or_center=ic_name,
        activity_code=_first(raw, "activity_code"),
        funding_mechanism=_first(raw, "funding_mechanism", "award_type"),
        organization=_normalize_org(_first(raw, "organization")),
        principal_investigators=_normalize_pis(_first(raw, "principal_investigators")),
        total_cost=_first(raw, "award_amount", "total_cost"),
        terms=_parse_terms(_first(raw, "terms", "pref_terms")),
        foa_number=_first(raw, "opportunity_number", "full_foa", "foa_number"),
        study_section=study_section,
        publications=_first(raw, "publications") or [],
        patents=_first(raw, "patents") or [],
        clinical_studies=_first(raw, "clinical_studies", "clinicaltrials") or [],
        source_url=source_url,
        retrieved_at=retrieved_at or _utcnow_iso(),
    )


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------- #
# Client
# --------------------------------------------------------------------------- #


class ReporterClient:
    """Thin, cached client over the RePORTER v2 projects endpoint."""

    def __init__(
        self,
        client: httpx.Client | None = None,
        cache_dir: Path | None = None,
        use_cache: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        settings = get_settings()
        self.cache_dir = cache_dir or (settings.cache_dir / "reporter")
        self.use_cache = use_cache
        self._timeout = timeout
        self._client = client
        self._owns_client = client is None
        if use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- lifecycle -------------------------------------------------------- #

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                timeout=self._timeout,
                headers={"User-Agent": "nih-science-agent/0.1 (+https://reporter.nih.gov)"},
            )
        return self._client

    def close(self) -> None:
        if self._client is not None and self._owns_client:
            self._client.close()
            self._client = None

    def __enter__(self) -> ReporterClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- caching ---------------------------------------------------------- #

    def _cache_path(self, url: str, payload: dict[str, Any]) -> Path:
        key = hashlib.sha256(
            json.dumps(
                {"url": url, "payload": payload}, sort_keys=True, separators=(",", ":")
            ).encode()
        ).hexdigest()[:24]
        prefix = "publications" if url == PUBLICATIONS_SEARCH_URL else "projects"
        return self.cache_dir / f"{prefix}_{key}.json"

    def _post_search(
        self, payload: dict[str, Any], url: str = PROJECTS_SEARCH_URL
    ) -> dict[str, Any]:
        cache_path = self._cache_path(url, payload)
        if self.use_cache and cache_path.exists():
            logger.debug("RePORTER cache hit: %s", cache_path.name)
            return json.loads(cache_path.read_text())

        logger.info(
            "RePORTER POST %s offset=%s limit=%s",
            url,
            payload.get("offset"),
            payload.get("limit"),
        )
        resp = self._http().post(url, json=payload)
        resp.raise_for_status()
        data = resp.json()

        if self.use_cache:
            cache_path.write_text(json.dumps(data))
        return data

    # -- public API ------------------------------------------------------- #

    def search_projects(
        self,
        query: str | None = None,
        fiscal_years: list[int] | None = None,
        institutes: list[str] | None = None,
        mechanisms: list[str] | None = None,
        pi_names: list[str] | None = None,
        pi_profile_ids: list[int] | None = None,
        limit: int = 100,
    ) -> list[ReporterProject]:
        """Search NIH RePORTER projects.

        ``query`` is matched against project title, abstract, and terms.
        ``institutes`` are IC codes/abbreviations (e.g. ``["NIEHS"]``),
        ``mechanisms`` are activity codes (e.g. ``["R01", "U01"]``).
        ``pi_names`` are free-form investigator names matched against any name
        part (e.g. ``["Khatri"]`` or ``["Purvesh Khatri"]``); ``pi_profile_ids``
        are RePORTER PI profile ids for unambiguous lookup. Pages through the API
        as needed up to ``limit`` results.
        """
        criteria: dict[str, Any] = {}
        if query:
            criteria["advanced_text_search"] = {
                "operator": "and",
                "search_field": "projecttitle,terms,abstracttext",
                "search_text": query,
            }
        if fiscal_years:
            criteria["fiscal_years"] = list(fiscal_years)
        if institutes:
            criteria["agencies"] = list(institutes)
        if mechanisms:
            criteria["activity_codes"] = list(mechanisms)
        if pi_names:
            criteria["pi_names"] = [{"any_name": name} for name in pi_names]
        if pi_profile_ids:
            criteria["pi_profile_ids"] = list(pi_profile_ids)

        retrieved_at = _utcnow_iso()
        results: list[ReporterProject] = []
        offset = 0
        while len(results) < limit and offset <= MAX_OFFSET:
            page_size = min(MAX_PAGE_LIMIT, limit - len(results))
            payload = {
                "criteria": criteria,
                "offset": offset,
                "limit": page_size,
                "sort_field": "fiscal_year",
                "sort_order": "desc",
            }
            data = self._post_search(payload)
            page = data.get("results") or []
            for raw in page:
                results.append(normalize_reporter_project(raw, retrieved_at=retrieved_at))

            total = (data.get("meta") or {}).get("total", 0)
            offset += page_size
            if not page or offset >= total:
                break

        return results[:limit]

    def get_project(self, project_number: str) -> ReporterProject | None:
        """Fetch a single project by its project number (e.g. ``R01ES032470``)."""
        payload = {
            "criteria": {"project_nums": [project_number]},
            "offset": 0,
            "limit": 1,
        }
        data = self._post_search(payload)
        page = data.get("results") or []
        if not page:
            return None
        return normalize_reporter_project(page[0])

    def get_publications(
        self, core_project_num: str, limit: int = 500
    ) -> list[ReporterPublicationLink]:
        """Return authoritative award→publication links for a core project number.

        These are the PMIDs NIH RePORTER associates with the grant (e.g.
        ``R01ES032470``). Pages through the publications endpoint up to ``limit``.
        """
        retrieved_at = _utcnow_iso()
        links: list[ReporterPublicationLink] = []
        offset = 0
        while len(links) < limit and offset <= MAX_OFFSET:
            page_size = min(MAX_PAGE_LIMIT, limit - len(links))
            payload = {
                "criteria": {"core_project_nums": [core_project_num]},
                "offset": offset,
                "limit": page_size,
            }
            data = self._post_search(payload, url=PUBLICATIONS_SEARCH_URL)
            page = data.get("results") or []
            for raw in page:
                links.append(normalize_publication_link(raw, retrieved_at=retrieved_at))

            total = (data.get("meta") or {}).get("total", 0)
            offset += page_size
            if not page or offset >= total:
                break

        return links[:limit]


# --------------------------------------------------------------------------- #
# Module-level convenience functions
# --------------------------------------------------------------------------- #


def search_projects(
    query: str | None = None,
    fiscal_years: list[int] | None = None,
    institutes: list[str] | None = None,
    mechanisms: list[str] | None = None,
    pi_names: list[str] | None = None,
    pi_profile_ids: list[int] | None = None,
    limit: int = 100,
    use_cache: bool = True,
) -> list[ReporterProject]:
    with ReporterClient(use_cache=use_cache) as client:
        return client.search_projects(
            query=query,
            fiscal_years=fiscal_years,
            institutes=institutes,
            mechanisms=mechanisms,
            pi_names=pi_names,
            pi_profile_ids=pi_profile_ids,
            limit=limit,
        )


def get_project(project_number: str, use_cache: bool = True) -> ReporterProject | None:
    with ReporterClient(use_cache=use_cache) as client:
        return client.get_project(project_number)


def get_publications(
    core_project_num: str, limit: int = 500, use_cache: bool = True
) -> list[ReporterPublicationLink]:
    with ReporterClient(use_cache=use_cache) as client:
        return client.get_publications(core_project_num, limit=limit)
