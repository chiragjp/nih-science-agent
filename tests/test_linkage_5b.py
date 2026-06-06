"""Offline tests for the Task 5b linkage layer.

Covers the five behaviors the design doc requires:
- authoritative vs inferred edges are tagged differently, distinct confidences;
- coverage numerator/denominator correct on a small fixture;
- accession extraction rejects gene-like false positives;
- disambiguation never silently merges distinct PIs with similar names;
- bias-audit stratifies coverage by IC, mechanism, and award age.
"""

from __future__ import annotations

from nih_science_agent.graph.knowledge_graph import KnowledgeGraph
from nih_science_agent.linkage import edges as E
from nih_science_agent.linkage.accession_extraction import (
    accession_edges,
    extract_accessions,
)
from nih_science_agent.linkage.coverage import (
    coverage_report,
    stratify_coverage,
)
from nih_science_agent.linkage.disambiguation import (
    InstitutionMention as InstMention,
)
from nih_science_agent.linkage.disambiguation import (
    PIMention,
    disambiguate_institutions,
    disambiguate_pis,
    normalize_institution,
)

# --------------------------------------------------------------------------- #
# Accession extraction
# --------------------------------------------------------------------------- #


def test_extracts_known_accessions() -> None:
    text = (
        "Sequencing data were deposited in GEO under accession GSE123456 and "
        "the dbGaP study phs001234.v1.p1. Raw reads are in SRA (SRR9988776), "
        "BioProject PRJNA556677. Immune data: SDY80."
    )
    matches = extract_accessions(text)
    repos = {m.repository for m in matches}
    assert {"GEO", "dbGaP", "SRA", "BioProject", "ImmPort"} <= repos
    accs = {m.accession for m in matches}
    assert "GSE123456" in accs
    assert "phs001234.v1.p1" in accs


def test_context_cue_raises_confidence() -> None:
    with_cue = extract_accessions("data deposited under accession GSE123456")[0]
    without_cue = extract_accessions("the value GSE123456 appears here")[0]
    assert with_cue.confidence > without_cue.confidence


def test_no_false_positive_on_gene_names() -> None:
    # Gene symbols and ordinary tokens that superficially resemble accessions.
    text = "The genes TP53, BRCA1, GPL, SRP9, and SDY were upregulated; see Fig GSE."
    assert extract_accessions(text) == []


def test_accession_edges_are_inferred() -> None:
    matches = extract_accessions("deposited under accession GSE123456")
    edges = accession_edges("37100513", matches)
    assert len(edges) == 1
    e = edges[0]
    assert e.authoritative is False
    assert e.method == "accession_regex"
    assert e.object == "DATASET:GEO:GSE123456"
    assert 0.0 < e.confidence < 1.0


def test_authoritative_and_inferred_have_distinct_confidence() -> None:
    auth = E.award_publication_edge("R01ES032470", "37100513")
    inferred = accession_edges("37100513", extract_accessions("value GSE123456 here"))[0]
    assert auth.authoritative and auth.confidence == 1.0
    assert (not inferred.authoritative) and inferred.confidence < 1.0


# --------------------------------------------------------------------------- #
# Disambiguation
# --------------------------------------------------------------------------- #


def test_distinct_profile_ids_never_merge_despite_same_name() -> None:
    mentions = [
        PIMention(full_name="John Smith", profile_id=111, institution="Univ A"),
        PIMention(full_name="John Smith", profile_id=222, institution="Univ B"),
    ]
    resolved = disambiguate_pis(mentions)
    assert len(resolved) == 2  # same name, different profile_id -> two identities


def test_similar_names_with_different_initials_stay_distinct() -> None:
    mentions = [
        PIMention(full_name="John A Smith", institution="Univ A"),
        PIMention(full_name="John B Smith", institution="Univ A"),
    ]
    assert len(disambiguate_pis(mentions)) == 2


def test_same_profile_id_merges_name_variants() -> None:
    mentions = [
        PIMention(full_name="Doe, Jane", profile_id=9608896),
        PIMention(full_name="Jane Doe", profile_id=9608896),
    ]
    resolved = disambiguate_pis(mentions)
    assert len(resolved) == 1
    assert resolved[0].mention_count == 2
    assert set(resolved[0].name_variants) == {"Doe, Jane", "Jane Doe"}
    assert resolved[0].resolved_by == "profile_id"


def test_orcid_takes_priority() -> None:
    mentions = [
        PIMention(full_name="J Smith", orcid="0000-0002-1825-0097", profile_id=1),
        PIMention(full_name="John Smith", orcid="0000-0002-1825-0097", profile_id=2),
    ]
    resolved = disambiguate_pis(mentions)
    assert len(resolved) == 1
    assert resolved[0].resolved_by == "orcid"


def test_institution_normalization_and_ror() -> None:
    assert normalize_institution("Harvard University") == "harvard"
    assert normalize_institution("HARVARD MEDICAL SCHOOL") == "harvard"
    # Same ROR merges even if names differ; no ROR falls back to name.
    resolved = disambiguate_institutions(
        [
            InstMention(name="Harvard Medical School", ror_id="03vek6s52"),
            InstMention(name="Harvard Univ.", ror_id="03vek6s52"),
        ]
    )
    assert len(resolved) == 1
    assert resolved[0].resolved_by == "ror"


# --------------------------------------------------------------------------- #
# Coverage + bias audit
# --------------------------------------------------------------------------- #


def _portfolio_graph() -> KnowledgeGraph:
    """Three awards; only some carry publication / trial edges."""
    g = KnowledgeGraph()
    ts = "2026-01-01T00:00:00+00:00"
    # award A: NIEHS R01, FY2015 (old), has a pub + a trial
    g.add_node("AWARD:A", ic="NIEHS", activity_code="R01", fiscal_year=2015)
    g.add_edge(E.award_publication_edge("A", "1", retrieved_at=ts))
    g.add_edge(E.award_trial_edge("A", "NCT1", retrieved_at=ts))
    # award B: NIEHS R01, FY2023 (new), pub only
    g.add_node("AWARD:B", ic="NIEHS", activity_code="R01", fiscal_year=2023)
    g.add_edge(E.award_publication_edge("B", "2", retrieved_at=ts))
    # award C: NCI P30, FY2024 (new), no outputs
    g.add_node("AWARD:C", ic="NCI", activity_code="P30", fiscal_year=2024)
    return g


def test_coverage_numerator_denominator() -> None:
    report = coverage_report(_portfolio_graph())
    assert report.denominator == 3
    by_pred = {r.predicate: r for r in report.rows}
    assert by_pred["produced"].numerator == 2  # A and B
    assert by_pred["produced"].coverage == 2 / 3
    assert by_pred["funded_trial"].numerator == 1  # only A
    # produced edges are authoritative
    assert by_pred["produced"].authoritative_edges == 2
    assert by_pred["produced"].inferred_edges == 0


def test_stratify_by_ic_mechanism_age() -> None:
    g = _portfolio_graph()
    by_ic = {r.stratum: r for r in stratify_coverage(g, "produced", by="ic")}
    assert by_ic["NIEHS"].coverage == 1.0  # A and B both have a pub
    assert by_ic["NCI"].coverage == 0.0  # C has none

    by_age = {
        r.stratum: r for r in stratify_coverage(g, "funded_trial", by="age", current_year=2026)
    }
    assert by_age["10y+"].coverage == 1.0  # A (FY2015) has the trial
    assert by_age["0-4y"].coverage == 0.0  # B, C (recent) have none

    by_mech = {r.stratum: r for r in stratify_coverage(g, "produced", by="mechanism")}
    assert by_mech["R01"].coverage == 1.0
    assert by_mech["P30"].coverage == 0.0
