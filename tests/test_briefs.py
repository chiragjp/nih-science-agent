"""Offline tests for the portfolio brief generator."""

from __future__ import annotations

from nih_science_agent.connectors import clinicaltrials, reporter
from nih_science_agent.connectors.clinicaltrials import Trial
from nih_science_agent.connectors.reporter import (
    Organization,
    PrincipalInvestigator,
    ReporterProject,
)
from nih_science_agent.linkage import edges as linkage
from nih_science_agent.linkage.edges import LinkedPublication
from nih_science_agent.tools import briefs


def _award(core, fy, cost, ic, act, inst, pi, abstract=""):
    return ReporterProject(
        core_project_num=core,
        project_number=core + "-01",
        fiscal_year=fy,
        total_cost=cost,
        nih_institute_or_center=ic,
        activity_code=act,
        organization=Organization(name=inst, state="MA"),
        principal_investigators=[PrincipalInvestigator(full_name=pi, profile_id=hash(pi) % 1000)],
        abstract_text=abstract,
        project_title=f"{core} study",
    )


PROJECTS = [
    _award(
        "R01DK1",
        2015,
        700_000,
        "NIDDK",
        "R01",
        "HARVARD",
        "Doe, Jane",
        abstract="Data deposited in GEO under accession GSE123456.",
    ),
    _award("R01DK1", 2016, 720_000, "NIDDK", "R01", "HARVARD", "Doe, Jane"),  # same grant, later yr
    _award("R01DK2", 2016, 500_000, "NIDDK", "R01", "STANFORD", "Roe, John"),
    _award("U01HL3", 2015, 1_200_000, "NHLBI", "U01", "HARVARD", "Smith, Ann"),
]


def _setup(monkeypatch, with_outcome=False):
    def fake_search(query=None, fiscal_years=None, institutes=None, limit=800, use_cache=True):
        yrs = fiscal_years or []
        return [p for p in PROJECTS if p.fiscal_year in yrs] if yrs else PROJECTS

    monkeypatch.setattr(reporter, "search_projects", fake_search)

    def fake_links(core, enrich=True, metrics=True, limit=50, use_cache=True):
        if core == "U01HL3":
            from nih_science_agent.connectors import icite, pubmed

            return [
                LinkedPublication(
                    pmid="111",
                    edge=linkage.award_publication_edge(core, "111"),
                    article=pubmed.PubmedArticle(pmid="111", title="High impact paper"),
                    metrics=icite.ICiteMetrics(
                        pmid="111", relative_citation_ratio=9.9, citation_count=200
                    ),
                )
            ]
        return []

    monkeypatch.setattr(linkage, "link_award_publications", fake_links)
    monkeypatch.setattr(
        clinicaltrials,
        "find_trials_for_grant",
        lambda core, use_cache=True: (
            [Trial(nct_id="NCT9", brief_title="A trial", nih_grant_numbers=[core])]
            if core == "U01HL3"
            else []
        ),
    )


def test_brief_composition(monkeypatch) -> None:
    _setup(monkeypatch)
    b = briefs.build_portfolio_brief("diabetes", years=[2015, 2016])

    assert b.distinct_awards == 3  # R01DK1 deduped across two years
    assert b.total_funding == 700_000 + 720_000 + 500_000 + 1_200_000
    # active distinct grants per fiscal year (R01DK1 is active in both)
    assert b.awards_by_year[2015] == 2  # R01DK1, U01HL3
    assert b.awards_by_year[2016] == 2  # R01DK1, R01DK2
    assert b.top_ics[0].name == "NIDDK"  # 2 of 3 cores
    assert {r.name for r in b.top_institutions} == {"HARVARD", "STANFORD"}


def test_brief_enrichment_and_provenance(monkeypatch) -> None:
    _setup(monkeypatch)
    b = briefs.build_portfolio_brief("diabetes", years=[2015, 2016], sample_awards=12)

    # publications sampled, top by RCR
    assert b.publication_count == 1
    assert b.top_publications[0].pmid == "111"
    assert b.top_publications[0].rcr == 9.9
    # trial discovered for the U01
    assert any("NCT9" in t for t in b.trials)
    # dataset mined from R01DK1 abstract (inferred)
    assert "GEO:GSE123456" in b.datasets
    # diabetes resolves to a condition -> outcome attached caveat present
    assert b.condition_label == "Diabetes"
    assert any("juxtaposition" in c.lower() or "causal" in c.lower() for c in b.caveats)


def test_brief_markdown_has_all_sections(monkeypatch) -> None:
    _setup(monkeypatch)
    b = briefs.build_portfolio_brief("diabetes", years=[2015, 2016])
    md = briefs.render_brief_markdown(b)
    for heading in [
        "## 1. Query & filters",
        "## 2. Portfolio composition",
        "### 3. Top Institutes",
        "### 6. Top PIs",
        "## 7. Publications & impact",
        "## 8. Related clinical trials",
        "## 9. Candidate reusable datasets",
        "## 10. Evidence gaps & caveats",
        "## 11. Sources & retrieval",
    ]:
        assert heading in md


def test_brief_nonmedical_topic_skips_outcome(monkeypatch) -> None:
    _setup(monkeypatch)
    b = briefs.build_portfolio_brief("quantum metrology methods", years=[2015, 2016])
    assert b.condition_label is None  # no crosswalk match -> no pulse
    assert b.mortality_summary is None
