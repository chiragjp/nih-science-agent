"""Offline tests for the knowledge graph builder and trial→grant extraction."""

from __future__ import annotations

from nih_science_agent.connectors import clinicaltrials, reporter
from nih_science_agent.connectors.clinicaltrials import Trial, normalize_trial
from nih_science_agent.connectors.reporter import (
    Organization,
    PrincipalInvestigator,
    ReporterProject,
    ReporterPublicationLink,
)
from nih_science_agent.graph import builder
from nih_science_agent.graph.knowledge_graph import KnowledgeGraph, split_node
from nih_science_agent.linkage import edges as E

# --------------------------------------------------------------------------- #
# Trial → NIH grant extraction
# --------------------------------------------------------------------------- #


def test_extract_nih_grant_structured() -> None:
    raw = {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT01127659",
                "secondaryIdInfos": [
                    {"id": "R01DK075877", "type": "NIH", "link": "https://reporter.nih.gov/x"},
                    {"id": "20-0006", "type": "OTHER"},
                ],
            }
        }
    }
    t = normalize_trial(raw)
    assert t.nih_grant_numbers == ["R01DK075877"]


def test_extract_nih_grant_freetext_and_prefix_suffix() -> None:
    raw = {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT1",
                "secondaryIdInfos": [
                    {
                        "id": "U.S. NIH Grant 5R18DK096394-03",
                        "type": "OTHER_GRANT",
                        "domain": "U.S. NIH Grant/Contract Award Number",
                    },
                ],
            }
        }
    }
    t = normalize_trial(raw)
    assert t.nih_grant_numbers == ["R18DK096394"]  # prefix 5 + suffix -03 stripped


def test_non_nih_secondary_ids_ignored() -> None:
    raw = {
        "protocolSection": {
            "identificationModule": {
                "nctId": "NCT2",
                "secondaryIdInfos": [
                    {"id": "EUDRACT-2019-000123-11", "type": "REGISTRY"},
                    {"id": "SomeFoundationGrant123456", "type": "OTHER_GRANT"},
                ],
            }
        }
    }
    assert normalize_trial(raw).nih_grant_numbers == []


def test_find_trials_for_grant_filters_false_matches(monkeypatch) -> None:
    real = Trial(nct_id="NCT_REAL", nih_grant_numbers=["R01DK075877"])
    bogus = Trial(nct_id="NCT_BOGUS", nih_grant_numbers=["R01XX000000"])
    monkeypatch.setattr(clinicaltrials, "search_trials", lambda **kw: [real, bogus])
    out = clinicaltrials.find_trials_for_grant("R01DK075877")
    assert [t.nct_id for t in out] == ["NCT_REAL"]  # text-only match dropped


# --------------------------------------------------------------------------- #
# KnowledgeGraph primitives
# --------------------------------------------------------------------------- #


def test_split_node() -> None:
    assert split_node("AWARD:R01ES032470") == ("AWARD", "R01ES032470")


def test_add_edge_creates_typed_nodes() -> None:
    g = KnowledgeGraph()
    g.add_edge(E.award_publication_edge("R01ES032470", "37100513"))
    assert g.num_nodes == 2
    assert g.node_type("AWARD:R01ES032470") == "AWARD"
    assert g.node_type("PUBLICATION:37100513") == "PUBLICATION"
    assert g.neighbors("AWARD:R01ES032470", predicate="produced") == ["PUBLICATION:37100513"]
    edge_data = next(iter(g.g.edges(data=True)))[2]
    assert edge_data["authoritative"] is True


# --------------------------------------------------------------------------- #
# Builder (connectors mocked)
# --------------------------------------------------------------------------- #


def _fake_project() -> ReporterProject:
    return ReporterProject(
        project_number="5R01ES032470-05",
        core_project_num="R01ES032470",
        project_title="Exposome methods",
        fiscal_year=2024,
        nih_institute_or_center="NIEHS",
        activity_code="R01",
        total_cost=620000,
        foa_number="PA-19-056",
        organization=Organization(name="HARVARD", state="MA"),
        principal_investigators=[
            PrincipalInvestigator(profile_id=9608896, full_name="Patel, Chirag")
        ],
        terms=["exposome", "PFAS"],
    )


def test_build_award_graph_wires_all_edge_types(monkeypatch) -> None:
    monkeypatch.setattr(reporter, "get_project", lambda num, use_cache=True: _fake_project())
    monkeypatch.setattr(
        reporter,
        "get_publications",
        lambda core, limit=500, use_cache=True: [
            ReporterPublicationLink(pmid="37100513"),
            ReporterPublicationLink(pmid="37066248"),
        ],
    )
    monkeypatch.setattr(
        clinicaltrials,
        "find_trials_for_grant",
        lambda core, limit=100, use_cache=True: [
            Trial(
                nct_id="NCT01234567",
                brief_title="A trial",
                overall_status="COMPLETED",
                nih_grant_numbers=["R01ES032470"],
            )
        ],
    )

    g = builder.build_award_graph("R01ES032470")
    stats = g.stats()

    # Node types present
    assert "AWARD" in stats["node_types"]
    assert stats["node_types"]["PI"] == 1
    assert stats["node_types"]["INSTITUTION"] == 1
    assert stats["node_types"]["NIH_IC"] == 1
    assert stats["node_types"]["FOA"] == 1
    assert stats["node_types"]["TOPIC_TERM"] == 2
    assert stats["node_types"]["PUBLICATION"] == 2
    assert stats["node_types"]["CLINICAL_TRIAL"] == 1

    # Edge relations
    assert stats["predicates"]["produced"] == 2
    assert stats["predicates"]["funded_trial"] == 1
    assert stats["predicates"]["has_pi"] == 1
    assert stats["authoritative_edges"] == stats["edges"]  # all authoritative so far

    award = "AWARD:R01ES032470"
    assert g.neighbors(award, node_type="CLINICAL_TRIAL") == ["CLINICAL_TRIAL:NCT01234567"]
    assert set(g.neighbors(award, predicate="produced")) == {
        "PUBLICATION:37100513",
        "PUBLICATION:37066248",
    }
