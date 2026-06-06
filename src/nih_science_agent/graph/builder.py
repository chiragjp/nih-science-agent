"""Build a knowledge graph around an NIH award.

Assembles the Task 5 edge types from the connectors and linkage layer:

    AWARD → PI / INSTITUTION / NIH_IC / FOA / TOPIC_TERM   (RePORTER project)
    AWARD → PUBLICATION                                     (RePORTER pub links)
    AWARD → CLINICAL_TRIAL                                  (CT.gov NIH grant ids)

Every edge is authoritative here (asserted by a source). Inferred edges
(PI+topic, grant-text mining) attach through the same :class:`Edge` model later.
"""

from __future__ import annotations

from datetime import UTC, datetime

from nih_science_agent.connectors import clinicaltrials, reporter
from nih_science_agent.graph.knowledge_graph import KnowledgeGraph
from nih_science_agent.linkage import edges as E


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _reporter_edge(subject: str, predicate: str, obj: str, retrieved_at: str) -> E.Edge:
    """An authoritative edge sourced from a RePORTER project field."""
    return E.Edge(
        subject=subject,
        predicate=predicate,
        object=obj,
        source="nih_reporter",
        method="reporter_project_field",
        authoritative=True,
        confidence=1.0,
        retrieved_at=retrieved_at,
    )


def add_award_entities(g: KnowledgeGraph, project: reporter.ReporterProject, ts: str) -> str:
    """Add an award node and its authoritative entity edges; return the award id."""
    core = project.core_project_num or project.project_number
    award_id = f"{E.AWARD}:{core}"
    g.add_node(
        award_id,
        title=project.project_title,
        fiscal_year=project.fiscal_year,
        ic=project.nih_institute_or_center,
        activity_code=project.activity_code,
        total_cost=project.total_cost,
    )

    for pi in project.principal_investigators:
        if not pi.full_name:
            continue
        pi_key = str(pi.profile_id) if pi.profile_id else pi.full_name
        pi_id = f"{E.PI}:{pi_key}"
        g.add_node(pi_id, name=pi.full_name, profile_id=pi.profile_id)
        g.add_edge(_reporter_edge(award_id, "has_pi", pi_id, ts))

    if project.organization.name:
        inst_id = f"{E.INSTITUTION}:{project.organization.name}"
        g.add_node(inst_id, name=project.organization.name, state=project.organization.state)
        g.add_edge(_reporter_edge(award_id, "awarded_to", inst_id, ts))

    if project.nih_institute_or_center:
        ic_id = f"{E.NIH_IC}:{project.nih_institute_or_center}"
        g.add_node(ic_id, name=project.nih_institute_or_center)
        g.add_edge(_reporter_edge(award_id, "funded_by", ic_id, ts))

    if project.foa_number:
        foa_id = f"{E.FOA}:{project.foa_number}"
        g.add_node(foa_id, name=project.foa_number)
        g.add_edge(_reporter_edge(award_id, "under_foa", foa_id, ts))

    for term in project.terms:
        term_id = f"{E.TOPIC_TERM}:{term.lower()}"
        g.add_node(term_id, name=term)
        g.add_edge(_reporter_edge(award_id, "about", term_id, ts))

    return award_id


def add_award_outputs(
    g: KnowledgeGraph,
    core: str,
    ts: str,
    with_publications: bool = True,
    with_trials: bool = True,
    pub_limit: int = 500,
    trial_limit: int = 100,
    use_cache: bool = True,
) -> None:
    """Add an award's publication and clinical-trial output edges."""
    if with_publications:
        for link in reporter.get_publications(core, limit=pub_limit, use_cache=use_cache):
            if link.pmid:
                g.add_node(f"{E.PUBLICATION}:{link.pmid}")
                g.add_edge(E.award_publication_edge(core, link.pmid, retrieved_at=ts))

    if with_trials:
        for trial in clinicaltrials.find_trials_for_grant(
            core, limit=trial_limit, use_cache=use_cache
        ):
            if not trial.nct_id:
                continue
            g.add_node(
                f"{E.CLINICAL_TRIAL}:{trial.nct_id}",
                title=trial.brief_title,
                status=trial.overall_status,
                phase=trial.phase,
            )
            g.add_edge(E.award_trial_edge(core, trial.nct_id, retrieved_at=ts))


def build_award_graph(
    project_number: str,
    with_publications: bool = True,
    with_trials: bool = True,
    pub_limit: int = 500,
    trial_limit: int = 100,
    use_cache: bool = True,
) -> KnowledgeGraph:
    """Build a :class:`KnowledgeGraph` centered on a single award."""
    g = KnowledgeGraph()
    ts = _utcnow_iso()

    project = reporter.get_project(project_number, use_cache=use_cache)
    core = (project.core_project_num if project else None) or project_number
    if project is not None:
        add_award_entities(g, project, ts)
    else:
        g.add_node(f"{E.AWARD}:{core}")

    add_award_outputs(
        g,
        core,
        ts,
        with_publications=with_publications,
        with_trials=with_trials,
        pub_limit=pub_limit,
        trial_limit=trial_limit,
        use_cache=use_cache,
    )
    return g


def build_portfolio_graph(
    query: str | None = None,
    institutes: list[str] | None = None,
    fiscal_years: list[int] | None = None,
    mechanisms: list[str] | None = None,
    pi_names: list[str] | None = None,
    max_awards: int = 50,
    with_publications: bool = True,
    with_trials: bool = True,
    use_cache: bool = True,
) -> KnowledgeGraph:
    """Build a multi-award graph for a portfolio query (for coverage reporting).

    Deduplicates project-year records to distinct core grants, then wires each
    award's entity and output edges. Bounded by ``max_awards`` (awards taken in
    search order).
    """
    g = KnowledgeGraph()
    ts = _utcnow_iso()

    projects = reporter.search_projects(
        query=query,
        fiscal_years=fiscal_years,
        institutes=institutes,
        mechanisms=mechanisms,
        pi_names=pi_names,
        limit=max(max_awards * 4, max_awards),
        use_cache=use_cache,
    )

    seen: set[str] = set()
    cores: list[str] = []
    for p in projects:
        core = p.core_project_num or p.project_number
        if not core or core in seen:
            continue
        seen.add(core)
        add_award_entities(g, p, ts)
        cores.append(core)
        if len(cores) >= max_awards:
            break

    for core in cores:
        add_award_outputs(
            g,
            core,
            ts,
            with_publications=with_publications,
            with_trials=with_trials,
            use_cache=use_cache,
        )
    return g
