"""Offline tests for the RePORTER connector — no network access required.

The HTTP client is replaced with an ``httpx.MockTransport`` that returns a
fixed fixture, so these tests exercise request construction, normalization,
pagination, and caching without touching api.reporter.nih.gov.
"""

from __future__ import annotations

import json

import httpx
import pytest

from nih_science_agent.connectors import reporter
from nih_science_agent.connectors.reporter import (
    ReporterClient,
    ReporterProject,
    normalize_reporter_project,
)

# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

RAW_PROJECT = {
    "appl_id": 10288999,
    "project_num": "5R01ES032470-03",
    "core_project_num": "R01ES032470",
    "project_title": "PFAS exposure and the serum proteome",
    "abstract_text": "This study examines per- and polyfluoroalkyl substances...",
    "fiscal_year": 2023,
    "agency_ic_admin": {
        "code": "ES",
        "abbreviation": "NIEHS",
        "name": "National Institute of Environmental Health Sciences",
    },
    "activity_code": "R01",
    "award_type": "5",
    "organization": {
        "org_name": "HARVARD MEDICAL SCHOOL",
        "org_city": "BOSTON",
        "org_state": "MA",
        "org_country": "UNITED STATES",
        "external_org_id": 3212902,
    },
    "principal_investigators": [
        {
            "profile_id": 123,
            "first_name": "Jane",
            "last_name": "Doe",
            "full_name": "Doe, Jane",
            "is_contact_pi": True,
        },
        {"profile_id": 456, "first_name": "John", "last_name": "Roe"},
    ],
    "award_amount": 512345,
    "terms": "<PFAS><proteomics><exposure><environmental health>",
    "opportunity_number": "PA-20-185",
    "full_study_section": {"name": "Systemic Injury by Environmental Exposure", "srg_code": "SIEE"},
}


def _make_response(results: list[dict], total: int | None = None) -> dict:
    return {
        "meta": {
            "total": total if total is not None else len(results),
            "offset": 0,
            "limit": len(results),
        },
        "results": results,
    }


def _mock_client(response_payload: dict, captured: list[dict] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(json.loads(request.content))
        return httpx.Response(200, json=response_payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def test_normalize_full_record() -> None:
    p = normalize_reporter_project(RAW_PROJECT)
    assert isinstance(p, ReporterProject)
    assert p.project_number == "5R01ES032470-03"
    assert p.application_id == 10288999
    assert p.fiscal_year == 2023
    assert p.nih_institute_or_center == "NIEHS"
    assert p.activity_code == "R01"
    assert p.total_cost == 512345
    assert p.foa_number == "PA-20-185"
    assert p.study_section == "Systemic Injury by Environmental Exposure"
    assert p.source_url == "https://reporter.nih.gov/project-details/10288999"
    assert p.retrieved_at is not None


def test_normalize_terms_angle_brackets() -> None:
    p = normalize_reporter_project(RAW_PROJECT)
    assert p.terms == ["PFAS", "proteomics", "exposure", "environmental health"]


def test_normalize_terms_fallback_to_delimited_string() -> None:
    p = normalize_reporter_project({"terms": "aging; cancer , immunology"})
    assert p.terms == ["aging", "cancer", "immunology"]


def test_normalize_pis_and_contact_flag() -> None:
    p = normalize_reporter_project(RAW_PROJECT)
    assert len(p.principal_investigators) == 2
    first = p.principal_investigators[0]
    assert first.full_name == "Doe, Jane"
    assert first.is_contact_pi is True
    # full_name synthesized from first/last when absent
    assert p.principal_investigators[1].full_name == "John Roe"


def test_normalize_organization_and_ror() -> None:
    p = normalize_reporter_project(RAW_PROJECT)
    assert p.organization.name == "HARVARD MEDICAL SCHOOL"
    assert p.organization.state == "MA"
    # Integer org id from the API is coerced to a string.
    assert p.organization.external_org_id == "3212902"


def test_normalize_empty_and_partial() -> None:
    p = normalize_reporter_project({})
    assert p.project_number is None
    assert p.terms == []
    assert p.principal_investigators == []
    assert p.organization.name is None
    assert p.publications == []
    assert p.retrieved_at is not None


def test_normalize_rejects_non_dict() -> None:
    with pytest.raises(TypeError):
        normalize_reporter_project([1, 2, 3])  # type: ignore[arg-type]


def test_retrieved_at_is_propagated() -> None:
    p = normalize_reporter_project(RAW_PROJECT, retrieved_at="2026-01-01T00:00:00+00:00")
    assert p.retrieved_at == "2026-01-01T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# Search request construction
# --------------------------------------------------------------------------- #


def test_search_builds_criteria(tmp_path) -> None:
    captured: list[dict] = []
    http = _mock_client(_make_response([RAW_PROJECT]), captured)
    client = ReporterClient(client=http, cache_dir=tmp_path, use_cache=False)

    results = client.search_projects(
        query="PFAS proteomics",
        fiscal_years=[2022, 2023],
        institutes=["NIEHS"],
        mechanisms=["R01"],
        limit=10,
    )

    assert len(results) == 1
    assert results[0].project_number == "5R01ES032470-03"

    body = captured[0]
    crit = body["criteria"]
    assert crit["advanced_text_search"]["search_text"] == "PFAS proteomics"
    assert crit["fiscal_years"] == [2022, 2023]
    assert crit["agencies"] == ["NIEHS"]
    assert crit["activity_codes"] == ["R01"]
    assert body["limit"] == 10


def test_search_by_pi_name_builds_criteria(tmp_path) -> None:
    captured: list[dict] = []
    http = _mock_client(_make_response([RAW_PROJECT]), captured)
    client = ReporterClient(client=http, cache_dir=tmp_path, use_cache=False)
    client.search_projects(pi_names=["Khatri", "Purvesh Khatri"], limit=5)
    crit = captured[0]["criteria"]
    assert crit["pi_names"] == [{"any_name": "Khatri"}, {"any_name": "Purvesh Khatri"}]
    assert "advanced_text_search" not in crit


def test_search_by_pi_profile_id_builds_criteria(tmp_path) -> None:
    captured: list[dict] = []
    http = _mock_client(_make_response([RAW_PROJECT]), captured)
    client = ReporterClient(client=http, cache_dir=tmp_path, use_cache=False)
    client.search_projects(pi_profile_ids=[9608896], limit=5)
    assert captured[0]["criteria"]["pi_profile_ids"] == [9608896]


def test_search_empty_query_omits_text_search(tmp_path) -> None:
    captured: list[dict] = []
    http = _mock_client(_make_response([RAW_PROJECT]), captured)
    client = ReporterClient(client=http, cache_dir=tmp_path, use_cache=False)
    client.search_projects(fiscal_years=[2023], limit=5)
    assert "advanced_text_search" not in captured[0]["criteria"]


def test_search_respects_limit_across_pages(tmp_path) -> None:
    # Server reports a large total; each page returns the same single record.
    captured: list[dict] = []
    http = _mock_client(_make_response([RAW_PROJECT], total=1000), captured)
    client = ReporterClient(client=http, cache_dir=tmp_path, use_cache=False)
    results = client.search_projects(query="x", limit=3)
    assert len(results) == 3  # capped at limit, not total


def test_get_project_by_number(tmp_path) -> None:
    captured: list[dict] = []
    http = _mock_client(_make_response([RAW_PROJECT]), captured)
    client = ReporterClient(client=http, cache_dir=tmp_path, use_cache=False)
    p = client.get_project("R01ES032470")
    assert p is not None
    assert p.application_id == 10288999
    assert captured[0]["criteria"]["project_nums"] == ["R01ES032470"]


def test_get_project_not_found(tmp_path) -> None:
    http = _mock_client(_make_response([]))
    client = ReporterClient(client=http, cache_dir=tmp_path, use_cache=False)
    assert client.get_project("R01XX000000") is None


# --------------------------------------------------------------------------- #
# Caching
# --------------------------------------------------------------------------- #


def test_cache_round_trip(tmp_path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_make_response([RAW_PROJECT]))

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ReporterClient(client=http, cache_dir=tmp_path, use_cache=True)

    client.search_projects(query="PFAS", limit=1)
    assert calls["n"] == 1
    # Second identical search should be served from disk, no new HTTP call.
    client.search_projects(query="PFAS", limit=1)
    assert calls["n"] == 1
    assert list(tmp_path.glob("projects_*.json"))


def test_no_cache_always_hits_transport(tmp_path) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_make_response([RAW_PROJECT]))

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ReporterClient(client=http, cache_dir=tmp_path, use_cache=False)
    client.search_projects(query="PFAS", limit=1)
    client.search_projects(query="PFAS", limit=1)
    assert calls["n"] == 2


# --------------------------------------------------------------------------- #
# CLI helpers
# --------------------------------------------------------------------------- #


def test_cli_year_parsing() -> None:
    from nih_science_agent.cli import _parse_years

    assert _parse_years(None) is None
    assert _parse_years("2023") == [2023]
    assert _parse_years("2018:2021") == [2018, 2019, 2020, 2021]
    assert _parse_years("2021:2018") == [2018, 2019, 2020, 2021]  # reversed range
    assert _parse_years("2018,2020,2022") == [2018, 2020, 2022]


def test_module_level_search_wraps_client(monkeypatch, tmp_path) -> None:
    http = _mock_client(_make_response([RAW_PROJECT]))

    # Patch the client class the convenience function constructs so it uses our
    # mock transport instead of a real network client.
    monkeypatch.setattr(
        reporter,
        "ReporterClient",
        lambda **kw: ReporterClient(client=http, cache_dir=tmp_path, use_cache=False),
    )
    results = reporter.search_projects(query="PFAS", limit=1)
    assert len(results) == 1
    assert results[0].project_number == "5R01ES032470-03"
