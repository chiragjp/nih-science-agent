"""Coverage and bias reporting for the linkage graph.

For a portfolio of award nodes, reports per-edge-type coverage — how many awards
carry at least one edge of each type (numerator) out of all awards in the query
(denominator) — broken down by source and method, and by authoritative vs
inferred. Also stratifies coverage by IC, funding mechanism, and award age so a
bias audit can show *where* linkage is thin (the design doc's §5A discipline:
every analysis ships with its coverage).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from nih_science_agent.linkage.edges import AWARD

if TYPE_CHECKING:
    from nih_science_agent.graph.knowledge_graph import KnowledgeGraph


class CoverageRow(BaseModel):
    predicate: str
    numerator: int  # subjects with >= 1 edge of this type
    denominator: int  # subjects in the portfolio
    coverage: float
    authoritative_edges: int
    inferred_edges: int
    by_source: dict[str, int] = Field(default_factory=dict)
    by_method: dict[str, int] = Field(default_factory=dict)


class CoverageReport(BaseModel):
    denominator: int
    rows: list[CoverageRow] = Field(default_factory=list)


class StratumRow(BaseModel):
    stratum: str
    numerator: int
    denominator: int
    coverage: float


def _out_edges(graph: KnowledgeGraph, node: str):
    return graph.g.out_edges(node, data=True)


def coverage_report(
    graph: KnowledgeGraph,
    predicates: list[str] | None = None,
    subject_type: str = AWARD,
) -> CoverageReport:
    """Per-edge-type coverage over all ``subject_type`` nodes in ``graph``."""
    subjects = graph.nodes_of_type(subject_type)
    denom = len(subjects)

    if predicates is None:
        found: set[str] = set()
        for s in subjects:
            for *_, d in _out_edges(graph, s):
                found.add(d.get("predicate"))
        predicates = sorted(found)

    rows: list[CoverageRow] = []
    for p in predicates:
        numerator = 0
        by_source: Counter = Counter()
        by_method: Counter = Counter()
        auth = inferred = 0
        for s in subjects:
            has_edge = False
            for *_, d in _out_edges(graph, s):
                if d.get("predicate") != p:
                    continue
                has_edge = True
                by_source[d.get("source")] += 1
                by_method[d.get("method")] += 1
                if d.get("authoritative"):
                    auth += 1
                else:
                    inferred += 1
            if has_edge:
                numerator += 1
        rows.append(
            CoverageRow(
                predicate=p,
                numerator=numerator,
                denominator=denom,
                coverage=(numerator / denom) if denom else 0.0,
                authoritative_edges=auth,
                inferred_edges=inferred,
                by_source=dict(by_source),
                by_method=dict(by_method),
            )
        )
    return CoverageReport(denominator=denom, rows=rows)


def _age_bucket(fy: int | None, current_year: int) -> str:
    if not fy:
        return "UNKNOWN"
    age = current_year - fy
    if age < 5:
        return "0-4y"
    if age < 10:
        return "5-9y"
    return "10y+"


def _stratum_value(attrs: dict, by: str, current_year: int) -> str:
    if by == "ic":
        return attrs.get("ic") or "UNKNOWN"
    if by == "mechanism":
        return attrs.get("activity_code") or "UNKNOWN"
    if by == "age":
        return _age_bucket(attrs.get("fiscal_year"), current_year)
    raise ValueError(f"unknown stratifier: {by!r} (use ic|mechanism|age)")


def stratify_coverage(
    graph: KnowledgeGraph,
    predicate: str,
    by: str,
    current_year: int | None = None,
    subject_type: str = AWARD,
) -> list[StratumRow]:
    """Coverage of one ``predicate`` stratified by IC, mechanism, or award age.

    Surfaces bias: e.g. publication-linkage coverage that is high for old R01s
    but near-zero for recent awards, or uneven across ICs.
    """
    current_year = current_year or datetime.now(UTC).year
    subjects = graph.nodes_of_type(subject_type)

    groups: dict[str, list[str]] = defaultdict(list)
    for s in subjects:
        groups[_stratum_value(graph.g.nodes[s], by, current_year)].append(s)

    rows: list[StratumRow] = []
    for stratum, subs in groups.items():
        numerator = sum(
            1
            for s in subs
            if any(d.get("predicate") == predicate for *_, d in _out_edges(graph, s))
        )
        rows.append(
            StratumRow(
                stratum=stratum,
                numerator=numerator,
                denominator=len(subs),
                coverage=(numerator / len(subs)) if subs else 0.0,
            )
        )
    return sorted(rows, key=lambda r: r.stratum)
