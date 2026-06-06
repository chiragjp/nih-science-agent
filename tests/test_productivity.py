"""Offline tests for the pubs-per-dollar productivity tool."""

from __future__ import annotations

from nih_science_agent.connectors import reporter
from nih_science_agent.connectors.reporter import ReporterProject, ReporterPublicationLink
from nih_science_agent.tools.productivity import mechanism_family, pubs_per_dollar


def test_mechanism_family_classification() -> None:
    assert mechanism_family("R01") == "research"
    assert mechanism_family("U01") == "research"
    assert mechanism_family("DP2") == "research"  # New Innovator
    assert mechanism_family("P30") == "center_core"
    assert mechanism_family("P42") == "center_core"
    assert mechanism_family("U19") == "center_core"  # cooperative center, not plain research
    assert mechanism_family("T32") == "training_career"
    assert mechanism_family("F31") == "training_career"
    assert mechanism_family("K23") == "training_career"
    assert mechanism_family("D43") == "training_career"
    assert mechanism_family("ZIAES103321") == "intramural"
    assert mechanism_family(None) == "other"
    assert mechanism_family("") == "other"


def _project(
    core: str, fy: int, cost: float, act: str = "R01", title: str = "T"
) -> ReporterProject:
    return ReporterProject(
        project_number=f"{core}-{fy % 100:02d}",
        core_project_num=core,
        fiscal_year=fy,
        total_cost=cost,
        activity_code=act,
        project_title=title,
    )


def test_pubs_per_dollar_groups_years_and_ranks(monkeypatch) -> None:
    # Grant A: two years of funding ($1M total), 50 pubs -> 50 pubs/$1M
    # Grant B: one year ($500k), 40 pubs -> 80 pubs/$1M  (higher ratio, wins)
    projects = [
        _project("R01AA000001", 2022, 500_000, title="Grant A"),
        _project("R01AA000001", 2023, 500_000, title="Grant A"),
        _project("R01BB000002", 2023, 500_000, title="Grant B"),
    ]
    pubs = {
        "R01AA000001": [ReporterPublicationLink(pmid=str(i)) for i in range(50)],
        "R01BB000002": [ReporterPublicationLink(pmid=str(i)) for i in range(40)],
    }
    monkeypatch.setattr(reporter, "search_projects", lambda **kw: projects)
    monkeypatch.setattr(
        reporter, "get_publications", lambda core, limit=5000, use_cache=True: pubs[core]
    )

    report = pubs_per_dollar(institutes=["XX"], fiscal_years=[2022, 2023], funding_floor=100_000)
    assert report.records_matched == 3
    assert report.grants_matched == 2
    assert report.grants_analyzed == 2

    top = report.results[0]
    assert top.core_project_num == "R01BB000002"
    assert top.pubs_per_million == 80.0
    a = next(r for r in report.results if r.core_project_num == "R01AA000001")
    assert a.total_funding == 1_000_000  # summed across both years
    assert a.pubs_per_million == 50.0


def test_funding_floor_excludes_small_grants(monkeypatch) -> None:
    projects = [
        _project("R01AA000001", 2023, 1_000_000, title="Big"),
        _project("R03BB000002", 2023, 50_000, act="R03", title="Tiny"),
    ]
    monkeypatch.setattr(reporter, "search_projects", lambda **kw: projects)
    monkeypatch.setattr(
        reporter,
        "get_publications",
        lambda core, limit=5000, use_cache=True: [ReporterPublicationLink(pmid="1")],
    )
    report = pubs_per_dollar(institutes=["XX"], funding_floor=250_000)
    assert report.grants_matched == 2
    assert report.grants_analyzed == 1  # tiny grant excluded by the floor
    assert all(r.core_project_num != "R03BB000002" for r in report.results)


def test_cap_is_disclosed(monkeypatch) -> None:
    projects = [_project(f"R01X{i:06d}", 2023, 1_000_000) for i in range(5)]
    monkeypatch.setattr(reporter, "search_projects", lambda **kw: projects)
    monkeypatch.setattr(
        reporter,
        "get_publications",
        lambda core, limit=5000, use_cache=True: [ReporterPublicationLink(pmid="1")],
    )
    report = pubs_per_dollar(institutes=["XX"], funding_floor=0, max_grants=3)
    assert report.capped is True
    assert report.grants_analyzed == 3
    assert any("highest-funded" in n for n in report.notes)
