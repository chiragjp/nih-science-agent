"""Offline tests for the meta-research diminishing-returns analysis."""

from __future__ import annotations

import pytest

from nih_science_agent.connectors import icite, reporter
from nih_science_agent.connectors.icite import ICiteMetrics, TranslationRecord
from nih_science_agent.connectors.reporter import (
    PrincipalInvestigator,
    ReporterProject,
    ReporterPublicationLink,
)
from nih_science_agent.tools import meta_research as meta
from nih_science_agent.tools.meta_research import _spearman


def test_translation_lineage(monkeypatch) -> None:
    # Grant FY2012 → 2 pubs. Pub p1 is cited by clinical papers c1 (2018), c2 (2020);
    # p2 by c2 (dup) → reach = 2 distinct clinical papers, first clinical citation 2018.
    monkeypatch.setattr(
        reporter,
        "get_publications",
        lambda core, limit=300, use_cache=True: [
            ReporterPublicationLink(pmid="p1"),
            ReporterPublicationLink(pmid="p2"),
        ],
    )

    def fake_translation(pmids, use_cache=True):
        catalog = {
            "p1": TranslationRecord(pmid="p1", year=2013, apt=0.3, clinical_citers=["c1", "c2"]),
            "p2": TranslationRecord(pmid="p2", year=2014, apt=0.1, clinical_citers=["c2"]),
            "c1": TranslationRecord(pmid="c1", year=2018, is_clinical=True),
            "c2": TranslationRecord(pmid="c2", year=2020, is_clinical=True),
        }
        return [catalog[p] for p in pmids if p in catalog]

    monkeypatch.setattr(icite, "fetch_translation", fake_translation)

    lin = meta.translation_lineage("R01GM000001", grant_fiscal_year=2012)
    assert lin.publications == 2
    assert lin.pubs_with_clinical_citation == 2
    assert lin.clinical_citation_reach == 2  # c1, c2 (deduped)
    assert lin.mean_apt == 0.2  # mean(0.3, 0.1)
    assert lin.years_to_first_clinical_citation == 6  # 2018 - 2012
    assert set(lin.example_clinical_citers) == {"c1", "c2"}


def test_scan_translation_ranks_by_reach(monkeypatch) -> None:
    monkeypatch.setattr(
        reporter,
        "search_projects",
        lambda **kw: [
            ReporterProject(core_project_num="G_HI", project_title="High reach", fiscal_year=2012),
            ReporterProject(core_project_num="G_LO", project_title="Low reach", fiscal_year=2013),
        ],
    )

    def fake_lineage(core, grant_fiscal_year=None, use_cache=True):
        reach = 100 if core == "G_HI" else 3
        return meta.TranslationLineage(
            core_project_num=core,
            grant_fiscal_year=grant_fiscal_year,
            publications=10,
            clinical_citation_reach=reach,
            mean_apt=0.5,
        )

    monkeypatch.setattr(meta, "translation_lineage", fake_lineage)
    rows = meta.scan_translation(query="x", max_grants=5)
    assert [r.core_project_num for r in rows] == ["G_HI", "G_LO"]  # sorted by reach desc
    assert rows[0].clinical_citation_reach == 100
    assert rows[0].start_fiscal_year == 2012


def test_detect_portfolio_redundancy(monkeypatch) -> None:
    # A and B share most terms (different PIs) → flagged CROSS-PI; C is unrelated.
    def proj(core, terms, pid, title):
        return ReporterProject(
            core_project_num=core,
            project_title=title,
            terms=terms,
            principal_investigators=[PrincipalInvestigator(full_name="x", profile_id=pid)],
        )

    shared = [f"term{i}" for i in range(10)]
    monkeypatch.setattr(
        reporter,
        "search_projects",
        lambda **kw: [
            proj("R01A", shared, 1, "A"),
            proj("R01B", shared[:9] + ["extra"], 2, "B"),  # 9/11 overlap, different PI
            proj("R01C", [f"z{i}" for i in range(10)], 3, "C"),  # unrelated
        ],
    )
    res = meta.detect_portfolio_redundancy(query="x", min_jaccard=0.4, min_terms=5)
    assert res.grants_analyzed == 3
    assert len(res.pairs) == 1  # only A↔B clears the threshold
    pair = res.pairs[0]
    assert {pair.core_a, pair.core_b} == {"R01A", "R01B"}
    assert pair.same_pi is False
    assert pair.jaccard > 0.4


def test_assess_open_science_signals(monkeypatch) -> None:
    from nih_science_agent.connectors import clinicaltrials, pubmed
    from nih_science_agent.connectors.clinicaltrials import Trial
    from nih_science_agent.connectors.pubmed import PubmedArticle

    monkeypatch.setattr(
        reporter,
        "get_publications",
        lambda core, limit=200, use_cache=True: [
            ReporterPublicationLink(pmid="1"),
            ReporterPublicationLink(pmid="2"),
        ],
    )
    monkeypatch.setattr(
        pubmed,
        "fetch_summaries",
        lambda pmids, use_cache=True: [
            PubmedArticle(pmid="1", pmcid="PMC1"),
            PubmedArticle(pmid="2", pmcid=None),
        ],
    )
    monkeypatch.setattr(
        reporter,
        "get_project",
        lambda core, use_cache=True: ReporterProject(
            core_project_num=core, abstract_text="Data deposited under accession GSE123456."
        ),
    )
    monkeypatch.setattr(
        clinicaltrials,
        "find_trials_for_grant",
        lambda core, use_cache=True: [Trial(nct_id="NCT1", nih_grant_numbers=[core])],
    )
    s = meta.assess_open_science_signals("R01X1")
    assert s.publications == 2
    assert s.pmc_fraction == 0.5  # 1 of 2 in PMC
    assert s.has_registered_trial is True
    assert "GEO:GSE123456" in s.dataset_accessions


def test_build_meta_research_brief(monkeypatch) -> None:
    monkeypatch.setattr(
        reporter,
        "search_projects",
        lambda **kw: [
            ReporterProject(
                core_project_num="R01A",
                project_title="A",
                total_cost=500_000,
                principal_investigators=[PrincipalInvestigator(full_name="Doe", profile_id=1)],
            ),
        ],
    )
    monkeypatch.setattr(
        meta, "detect_portfolio_redundancy", lambda **kw: meta.RedundancyResult(pairs=[])
    )
    monkeypatch.setattr(
        meta,
        "assess_open_science_signals",
        lambda core, use_cache=True: meta.OpenScienceSignals(
            core_project_num=core, pmc_fraction=0.8, has_registered_trial=True
        ),
    )
    monkeypatch.setattr(meta, "scan_translation", lambda **kw: [])
    b = meta.build_meta_research_brief("exposome", years=[2020])
    assert b.distinct_grants == 1
    assert b.total_funding == 500_000
    assert b.top_pis == ["Doe"]
    assert b.open_science_pmc_fraction == 0.8
    assert b.grants_with_trial == 1


def test_spearman_detects_monotonic() -> None:
    assert _spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)
    assert _spearman([1, 2, 3, 4], [1, 2, 3, 4]) == pytest.approx(1.0)
    assert _spearman([1], [1]) is None


def _award(core, pi, pid, cost):
    return ReporterProject(
        core_project_num=core,
        project_number=core + "-01",
        total_cost=cost,
        principal_investigators=[PrincipalInvestigator(full_name=pi, profile_id=pid)],
    )


def test_diminishing_returns_pipeline(monkeypatch) -> None:
    # Four PIs with increasing funding. Output (RCR) grows sublinearly with
    # support, so output-per-$ falls -> diminishing returns (negative Spearman).
    # PI funding: A=$1M, B=$2M, C=$3M, D=$4M ; pubs scale much slower.
    awards = [
        _award("G_A", "PI A", 1, 1_000_000),
        _award("G_B", "PI B", 2, 2_000_000),
        _award("G_C", "PI C", 3, 3_000_000),
        _award("G_D", "PI D", 4, 4_000_000),
    ]
    monkeypatch.setattr(reporter, "search_projects", lambda **kw: awards)

    # Each grant has a fixed number of pubs; RCR per pub is constant (1.0).
    pubs_by_core = {"G_A": 8, "G_B": 10, "G_C": 11, "G_D": 12}

    def fake_pubs(core, limit=200, use_cache=True):
        return [ReporterPublicationLink(pmid=f"{core}_{i}") for i in range(pubs_by_core[core])]

    monkeypatch.setattr(reporter, "get_publications", fake_pubs)
    monkeypatch.setattr(
        icite,
        "fetch_metrics",
        lambda pmids, use_cache=True: [
            ICiteMetrics(pmid=p, relative_citation_ratio=1.0) for p in pmids
        ],
    )

    res = meta.grant_support_vs_productivity(institutes=["XX"], fiscal_years=[2015], max_pis=10)
    assert res.pis_total == 4
    assert res.pis_analyzed == 4

    by_pi = {r.pi: r for r in res.rows}
    assert by_pi["PI A"].weighted_rcr == 8.0
    assert by_pi["PI A"].rcr_per_million == 8.0  # 8 RCR / $1M
    assert by_pi["PI D"].rcr_per_million == 3.0  # 12 RCR / $4M  -> lower per dollar

    # output-per-$ decreases as funding increases
    assert res.spearman_funding_vs_output_per_dollar is not None
    assert res.spearman_funding_vs_output_per_dollar < 0


def test_pi_grouping_dedupes_grants(monkeypatch) -> None:
    # Same PI on two grants -> one PI with summed funding and both grants.
    awards = [
        _award("G1", "Solo PI", 99, 500_000),
        _award("G2", "Solo PI", 99, 700_000),
    ]
    monkeypatch.setattr(reporter, "search_projects", lambda **kw: awards)
    monkeypatch.setattr(reporter, "get_publications", lambda core, limit=200, use_cache=True: [])
    monkeypatch.setattr(icite, "fetch_metrics", lambda pmids, use_cache=True: [])
    res = meta.grant_support_vs_productivity(institutes=["XX"], fiscal_years=[2015])
    assert res.pis_total == 1
    assert res.rows == [] or res.rows[0].total_funding == 1_200_000
