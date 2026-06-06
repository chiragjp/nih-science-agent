"""Publication productivity analysis: linked publications per funding dollar.

Computes, for a bounded population of grants, the number of authoritative
RePORTER-linked publications per $1M of funding — split by mechanism family,
because center/core grants (P-series shared facilities) accumulate
acknowledgments from everyone using them and would otherwise dominate a naive
ranking. The report is explicit about its denominator and any cap applied
(no silent truncation), per the design doc's coverage discipline.

Caveats this tool surfaces but cannot fully remove:
- Funding is summed over the records matched by the query's fiscal-year filter,
  not necessarily the grant's full lifetime.
- Publication counts are lifetime and RePORTER-authoritative-only (incomplete,
  biased — undercounts pubs that don't cite the grant).
- pubs-per-$ favors old grants (pubs accrue for years after funding) and
  shared infrastructure; compare within a mechanism family, not across.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from nih_science_agent.connectors import reporter

# Mechanism family classification (heuristic, by activity code).
_CENTER_CODES = {"U19", "U54", "UM1", "UM2", "P2C", "UG3", "UH3"}
_TRAINING_CODES = {"D43", "D71", "R25", "R90", "R38", "RL5", "TL1", "KL2"}


def mechanism_family(activity_code: str | None) -> str:
    """Bucket an NIH activity code into a coarse family.

    Returns one of: ``research``, ``center_core``, ``training_career``,
    ``intramural``, ``other``. Heuristic — good enough to keep shared-facility
    cores from contaminating a research-grant productivity ranking.
    """
    a = (activity_code or "").upper().strip()
    if not a:
        return "other"
    if a.startswith("DP"):  # DP1/DP2/DP5 Pioneer/New Innovator are research
        return "research"
    if a.startswith("Z"):  # ZIA/ZIC intramural
        return "intramural"
    if a in _CENTER_CODES or a[0] in {"P", "S", "G", "C"}:
        return "center_core"
    if a in _TRAINING_CODES or a[0] in {"T", "F", "K"}:
        return "training_career"
    if a[0] in {"R", "U"}:
        return "research"
    return "other"


class GrantProductivity(BaseModel):
    core_project_num: str
    activity_code: str | None = None
    mechanism_family: str
    title: str | None = None
    total_funding: float
    publication_count: int
    pubs_per_million: float | None = None


class ProductivityReport(BaseModel):
    """Result of a pubs-per-dollar analysis over a grant population."""

    criteria: dict = Field(default_factory=dict)
    records_matched: int = 0  # raw project-year records in the population
    grants_matched: int = 0  # distinct core grants after grouping
    grants_analyzed: int = 0  # grants we actually fetched publications for
    funding_floor: float = 0.0
    capped: bool = False  # True if grants_analyzed < grants_matched (disclosure)
    notes: list[str] = Field(default_factory=list)
    results: list[GrantProductivity] = Field(default_factory=list)


def pubs_per_dollar(
    query: str | None = None,
    institutes: list[str] | None = None,
    fiscal_years: list[int] | None = None,
    mechanisms: list[str] | None = None,
    pi_names: list[str] | None = None,
    funding_floor: float = 250_000.0,
    max_records: int = 6000,
    max_grants: int = 400,
    pub_limit: int = 5000,
    use_cache: bool = True,
) -> ProductivityReport:
    """Rank a bounded grant population by linked publications per $1M.

    Pulls matching project-year records, groups them into core grants (summing
    funding), then fetches authoritative publication counts for each grant above
    ``funding_floor``. ``max_grants`` caps how many grants get a publication
    fetch — when the population is larger, the highest-funded grants are
    analyzed and ``capped`` is set so the omission is never silent.
    """
    report = ProductivityReport(
        criteria={
            "query": query,
            "institutes": institutes,
            "fiscal_years": fiscal_years,
            "mechanisms": mechanisms,
            "pi_names": pi_names,
        },
        funding_floor=funding_floor,
    )

    projects = reporter.search_projects(
        query=query,
        fiscal_years=fiscal_years,
        institutes=institutes,
        mechanisms=mechanisms,
        pi_names=pi_names,
        limit=max_records,
        use_cache=use_cache,
    )
    report.records_matched = len(projects)
    if len(projects) >= max_records:
        report.notes.append(
            f"Hit max_records={max_records}; population may be truncated. Narrow the query."
        )

    # Group project-year records into core grants, summing funding.
    grants: dict[str, GrantProductivity] = {}
    for p in projects:
        core = p.core_project_num or p.project_number
        if not core:
            continue
        g = grants.get(core)
        if g is None:
            grants[core] = GrantProductivity(
                core_project_num=core,
                activity_code=p.activity_code,
                mechanism_family=mechanism_family(p.activity_code),
                title=p.project_title,
                total_funding=p.total_cost or 0.0,
                publication_count=0,
            )
        else:
            g.total_funding += p.total_cost or 0.0
    report.grants_matched = len(grants)

    # Apply the funding floor, then cap by funding for the publication fetch.
    eligible = [g for g in grants.values() if g.total_funding >= funding_floor]
    eligible.sort(key=lambda g: g.total_funding, reverse=True)
    if len(eligible) > max_grants:
        report.capped = True
        report.notes.append(
            f"Analyzed the {max_grants} highest-funded of {len(eligible)} eligible grants "
            f"(>= ${funding_floor:,.0f}); smaller grants not fetched. This biases against "
            f"low-funding high-ratio grants — raise max_grants for full coverage."
        )
        eligible = eligible[:max_grants]

    analyzed: list[GrantProductivity] = []
    for g in eligible:
        links = reporter.get_publications(g.core_project_num, limit=pub_limit, use_cache=use_cache)
        g.publication_count = len(links)
        if g.total_funding > 0:
            g.pubs_per_million = g.publication_count / (g.total_funding / 1_000_000)
        analyzed.append(g)

    report.grants_analyzed = len(analyzed)
    analyzed.sort(key=lambda g: g.pubs_per_million or 0.0, reverse=True)
    report.results = analyzed
    return report
