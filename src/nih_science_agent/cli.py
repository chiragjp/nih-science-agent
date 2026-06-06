"""Command-line interface for nih-science-agent.

Entry point: ``nih-agent`` (see ``[project.scripts]`` in pyproject.toml).
Every command is a thin wrapper over a deterministic connector/tool so that the
same operations are callable from an MCP server or notebook later.
"""

from __future__ import annotations

import json

import typer

from nih_science_agent.connectors import (
    clinicaltrials,
    databook,
    foa,
    icite,
    pubmed,
    reporter,
)
from nih_science_agent.connectors import nams as nams_conn
from nih_science_agent.graph import builder as graph_builder
from nih_science_agent.linkage import coverage as coverage_mod
from nih_science_agent.linkage import edges as linkage
from nih_science_agent.logging import setup_logging
from nih_science_agent.storage import duckdb_store
from nih_science_agent.tools import benchmarks as benchmarks_tool
from nih_science_agent.tools import briefs as briefs_tool
from nih_science_agent.tools import conditions as conditions_tool
from nih_science_agent.tools import meta_research as meta_tool
from nih_science_agent.tools import preclinical as preclinical_tool
from nih_science_agent.tools import productivity as productivity_tool
from nih_science_agent.tools import pulse as pulse_tool

app = typer.Typer(
    add_completion=False,
    help="Tool-first CLI for mapping NIH-funded research from awards to discoveries.",
    no_args_is_help=True,
)

awards_app = typer.Typer(help="Search and fetch NIH RePORTER awards.", no_args_is_help=True)
app.add_typer(awards_app, name="awards")

pubs_app = typer.Typer(help="Search PubMed and fetch iCite citation metrics.", no_args_is_help=True)
app.add_typer(pubs_app, name="pubs")

trials_app = typer.Typer(help="Search and fetch ClinicalTrials.gov studies.", no_args_is_help=True)
app.add_typer(trials_app, name="trials")

graph_app = typer.Typer(help="Build and inspect the knowledge graph.", no_args_is_help=True)
app.add_typer(graph_app, name="graph")

pulse_app = typer.Typer(
    help="Juxtapose NIH funding with national health outcomes.", no_args_is_help=True
)
app.add_typer(pulse_app, name="pulse")


def _parse_years(years: str | None) -> list[int] | None:
    """Parse a ``--years`` value: ``2020``, ``2018:2022``, or ``2018,2020,2022``."""
    if not years:
        return None
    text = years.strip()
    if ":" in text:
        start_s, end_s = text.split(":", 1)
        start, end = int(start_s), int(end_s)
        if start > end:
            start, end = end, start
        return list(range(start, end + 1))
    return [int(y) for y in text.split(",") if y.strip()]


def _project_summary(p: reporter.ReporterProject) -> dict[str, object]:
    pis = ", ".join(pi.full_name for pi in p.principal_investigators if pi.full_name)
    return {
        "project_number": p.project_number,
        "fiscal_year": p.fiscal_year,
        "ic": p.nih_institute_or_center,
        "activity_code": p.activity_code,
        "total_cost": p.total_cost,
        "organization": p.organization.name,
        "pis": pis,
        "title": p.project_title,
    }


@awards_app.command("search")
def awards_search(
    query: str | None = typer.Argument(
        None, help="Free-text query over title, abstract, and terms (optional)."
    ),
    years: str | None = typer.Option(
        None, "--years", help="Fiscal years: '2022', a range '2018:2022', or list '2018,2020'."
    ),
    ic: list[str] | None = typer.Option(
        None, "--ic", help="NIH Institute/Center code, e.g. NIEHS. Repeatable."
    ),
    mechanism: list[str] | None = typer.Option(
        None, "--mechanism", "-m", help="Activity code, e.g. R01. Repeatable."
    ),
    pi: list[str] | None = typer.Option(
        None, "--pi", help="PI name, matched against any name part, e.g. 'Khatri'. Repeatable."
    ),
    pi_id: list[int] | None = typer.Option(
        None, "--pi-id", help="RePORTER PI profile id, for unambiguous lookup. Repeatable."
    ),
    limit: int = typer.Option(25, "--limit", help="Maximum awards to return."),
    as_json: bool = typer.Option(False, "--json", help="Emit full normalized records as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Search NIH RePORTER for awards.

    Provide at least one of: a text query, --pi/--pi-id, --ic, --mechanism, or --years.
    """
    setup_logging()
    if not any([query, ic, mechanism, pi, pi_id, years]):
        typer.echo(
            "Provide at least one filter: a query, --pi/--pi-id, --ic, --mechanism, or --years."
        )
        raise typer.Exit(code=2)

    projects = reporter.search_projects(
        query=query,
        fiscal_years=_parse_years(years),
        institutes=list(ic) if ic else None,
        mechanisms=list(mechanism) if mechanism else None,
        pi_names=list(pi) if pi else None,
        pi_profile_ids=list(pi_id) if pi_id else None,
        limit=limit,
        use_cache=not no_cache,
    )

    if as_json:
        typer.echo(json.dumps([p.model_dump() for p in projects], indent=2))
        return

    if not projects:
        typer.echo("No awards found.")
        return

    typer.echo(f"{len(projects)} award(s):\n")
    for p in projects:
        s = _project_summary(p)
        cost = f"${s['total_cost']:,.0f}" if isinstance(s["total_cost"], (int, float)) else "n/a"
        typer.echo(f"• {s['project_number']}  FY{s['fiscal_year']}  {s['ic']}  {cost}")
        typer.echo(f"    {s['title']}")
        if s["pis"]:
            typer.echo(f"    PI(s): {s['pis']}  —  {s['organization']}")
        typer.echo("")


@awards_app.command("get")
def awards_get(
    project_number: str = typer.Argument(..., help="Project number, e.g. R01ES032470."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full normalized record as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Fetch a single award by project number."""
    setup_logging()
    project = reporter.get_project(project_number, use_cache=not no_cache)
    if project is None:
        typer.echo(f"No award found for {project_number}.")
        raise typer.Exit(code=1)

    if as_json:
        typer.echo(json.dumps(project.model_dump(), indent=2))
        return

    typer.echo(json.dumps(_project_summary(project), indent=2))
    if project.abstract_text:
        typer.echo("\nAbstract:\n" + project.abstract_text)


@awards_app.command("pubs")
def awards_pubs(
    project_number: str = typer.Argument(
        ..., help="Core project number, e.g. R01ES032470 (strip the support-year prefix/suffix)."
    ),
    metrics: bool = typer.Option(
        False, "--metrics", help="Also fetch iCite RCR/citation metrics for each publication."
    ),
    limit: int = typer.Option(500, "--limit", help="Maximum linked publications to return."),
    as_json: bool = typer.Option(
        False, "--json", help="Emit typed edges + enriched records as JSON."
    ),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """List the publications NIH RePORTER links to an award (authoritative edges)."""
    setup_logging()
    links = linkage.link_award_publications(
        project_number, enrich=True, metrics=metrics, limit=limit, use_cache=not no_cache
    )
    if not links:
        typer.echo(f"No linked publications found for {project_number}.")
        return

    if as_json:
        typer.echo(json.dumps([link.model_dump() for link in links], indent=2))
        return

    typer.echo(f"{len(links)} publication(s) linked to {project_number} (authoritative):\n")
    for link in links:
        a = link.article
        title = a.title if a and a.title else "(metadata unavailable)"
        year = a.pub_year if a else None
        journal = a.journal if a else None
        typer.echo(f"• PMID {link.pmid}  ({year})  {journal}")
        typer.echo(f"    {title}")
        if metrics and link.metrics is not None:
            m = link.metrics
            rcr = (
                f"{m.relative_citation_ratio:.2f}"
                if m.relative_citation_ratio is not None
                else "n/a"
            )
            typer.echo(
                f"    RCR {rcr}  •  {m.citation_count} citations  •  NIH pct {m.nih_percentile}"
            )
        typer.echo("")


@awards_app.command("productivity")
def awards_productivity(
    query: str | None = typer.Argument(None, help="Optional free-text topic filter."),
    years: str | None = typer.Option(
        None, "--years", help="Fiscal years: '2022', a range '2018:2024', or list '2018,2020'."
    ),
    ic: list[str] | None = typer.Option(None, "--ic", help="NIH IC code, e.g. NIAID. Repeatable."),
    mechanism: list[str] | None = typer.Option(
        None, "--mechanism", "-m", help="Activity code, e.g. DP2. Repeatable."
    ),
    pi: list[str] | None = typer.Option(None, "--pi", help="PI name. Repeatable."),
    floor: float = typer.Option(
        250_000, "--floor", help="Minimum cumulative funding for a grant to be ranked."
    ),
    max_grants: int = typer.Option(
        400, "--max-grants", help="Cap on grants to fetch publications for (disclosed if hit)."
    ),
    top: int = typer.Option(15, "--top", help="How many ranked grants to display."),
    family: str | None = typer.Option(
        None, "--family", help="Filter display to one mechanism family (e.g. research)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit the full report as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Rank a bounded grant population by linked publications per $1M of funding."""
    setup_logging()
    if not any([query, ic, mechanism, pi, years]):
        typer.echo("Provide at least one filter: a query, --ic, --mechanism, --pi, or --years.")
        raise typer.Exit(code=2)

    report = productivity_tool.pubs_per_dollar(
        query=query,
        institutes=list(ic) if ic else None,
        fiscal_years=_parse_years(years),
        mechanisms=list(mechanism) if mechanism else None,
        pi_names=list(pi) if pi else None,
        funding_floor=floor,
        max_grants=max_grants,
        use_cache=not no_cache,
    )

    if as_json:
        typer.echo(report.model_dump_json(indent=2))
        return

    rows = report.results
    if family:
        rows = [r for r in rows if r.mechanism_family == family]

    typer.echo(
        f"Population: {report.records_matched} records → {report.grants_matched} grants; "
        f"analyzed {report.grants_analyzed} (>= ${report.funding_floor:,.0f})."
    )
    for note in report.notes:
        typer.echo(f"  ⚠ {note}")
    typer.echo("")
    typer.echo(f"{'pubs/$1M':>9}  {'pubs':>5}  {'cum $':>13}  {'core':<14} {'fam':<11} title")
    for r in rows[:top]:
        ppm = f"{r.pubs_per_million:.1f}" if r.pubs_per_million is not None else "n/a"
        title = (r.title or "")[:48]
        typer.echo(
            f"{ppm:>9}  {r.publication_count:5d}  ${r.total_funding:12,.0f}  "
            f"{r.core_project_num:<14} {r.mechanism_family:<11} {title}"
        )


@pubs_app.command("search")
def pubs_search(
    query: str = typer.Argument(..., help="PubMed query (E-utilities syntax supported)."),
    limit: int = typer.Option(10, "--limit", help="Maximum articles to return."),
    metrics: bool = typer.Option(
        False, "--metrics", help="Also fetch iCite RCR/citation metrics for each result."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit full normalized records as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Search PubMed and (optionally) attach iCite metrics."""
    setup_logging()
    articles = pubmed.search_articles(query, retmax=limit, use_cache=not no_cache)
    if not articles:
        typer.echo("No articles found.")
        return

    metrics_by_pmid: dict[str, icite.ICiteMetrics] = {}
    if metrics:
        pmids = [a.pmid for a in articles if a.pmid]
        metrics_by_pmid = {
            m.pmid: m for m in icite.fetch_metrics(pmids, use_cache=not no_cache) if m.pmid
        }

    if as_json:
        payload = []
        for a in articles:
            row = a.model_dump()
            if metrics and a.pmid in metrics_by_pmid:
                row["icite"] = metrics_by_pmid[a.pmid].model_dump()
            payload.append(row)
        typer.echo(json.dumps(payload, indent=2))
        return

    typer.echo(f"{len(articles)} article(s):\n")
    for a in articles:
        authors = ", ".join(a.authors[:3]) + (" et al." if len(a.authors) > 3 else "")
        typer.echo(f"• PMID {a.pmid}  ({a.pub_year})  {a.journal}")
        typer.echo(f"    {a.title}")
        if authors:
            typer.echo(f"    {authors}")
        m = metrics_by_pmid.get(a.pmid or "")
        if m is not None:
            rcr = (
                f"{m.relative_citation_ratio:.2f}"
                if m.relative_citation_ratio is not None
                else "n/a"
            )
            typer.echo(
                f"    RCR {rcr}  •  {m.citation_count} citations  •  NIH pct {m.nih_percentile}"
            )
        typer.echo("")


@pubs_app.command("metrics")
def pubs_metrics(
    pmids: list[str] = typer.Argument(..., help="One or more PMIDs."),
    as_json: bool = typer.Option(False, "--json", help="Emit full normalized records as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Fetch iCite citation metrics (RCR, percentile, counts) for PMIDs."""
    setup_logging()
    results = icite.fetch_metrics(list(pmids), use_cache=not no_cache)
    if not results:
        typer.echo("No metrics found.")
        raise typer.Exit(code=1)

    if as_json:
        typer.echo(json.dumps([m.model_dump() for m in results], indent=2))
        return

    for m in results:
        rcr = f"{m.relative_citation_ratio:.2f}" if m.relative_citation_ratio is not None else "n/a"
        typer.echo(f"• PMID {m.pmid}  ({m.year})  {m.journal}")
        typer.echo(f"    {m.title}")
        typer.echo(
            f"    RCR {rcr}  •  {m.citation_count} citations  •  "
            f"{m.citations_per_year} cites/yr  •  NIH percentile {m.nih_percentile}"
        )
        typer.echo("")


@trials_app.command("search")
def trials_search(
    query: str | None = typer.Argument(None, help="Free-text query over the study record."),
    condition: str | None = typer.Option(None, "--condition", "-c", help="Condition/disease."),
    intervention: str | None = typer.Option(
        None, "--intervention", "-i", help="Intervention, e.g. a drug name."
    ),
    sponsor: str | None = typer.Option(None, "--sponsor", "-s", help="Lead sponsor."),
    has_results: bool = typer.Option(
        False, "--has-results", help="Restrict to studies with posted results."
    ),
    limit: int = typer.Option(15, "--limit", help="Maximum studies to return."),
    as_json: bool = typer.Option(False, "--json", help="Emit full normalized records as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Search ClinicalTrials.gov for studies."""
    setup_logging()
    if not any([query, condition, intervention, sponsor]):
        typer.echo("Provide at least one of: a query, --condition, --intervention, or --sponsor.")
        raise typer.Exit(code=2)

    trials = clinicaltrials.search_trials(
        condition=condition,
        intervention=intervention,
        sponsor=sponsor,
        query=query,
        has_results=True if has_results else None,
        limit=limit,
        use_cache=not no_cache,
    )
    if not trials:
        typer.echo("No studies found.")
        return

    if as_json:
        typer.echo(json.dumps([t.model_dump() for t in trials], indent=2))
        return

    typer.echo(f"{len(trials)} study(ies):\n")
    for t in trials:
        results_flag = "✓results" if t.has_results else "no results"
        typer.echo(f"• {t.nct_id}  {t.overall_status}  {t.phase or 'N/A'}  ({results_flag})")
        typer.echo(f"    {t.brief_title}")
        if t.conditions:
            typer.echo(f"    Conditions: {', '.join(t.conditions[:4])}")
        if t.sponsors:
            typer.echo(f"    Sponsor: {t.sponsors[0]}")
        typer.echo("")


@trials_app.command("get")
def trials_get(
    nct_id: str = typer.Argument(..., help="NCT id, e.g. NCT04280705."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full normalized record as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Fetch a single ClinicalTrials.gov study by NCT id."""
    setup_logging()
    trial = clinicaltrials.get_trial(nct_id, use_cache=not no_cache)
    if trial is None:
        typer.echo(f"No study found for {nct_id}.")
        raise typer.Exit(code=1)

    if as_json:
        typer.echo(json.dumps(trial.model_dump(), indent=2))
        return

    results_state = "results posted" if trial.has_results else "no results"
    interventions = "; ".join(trial.interventions) or "—"
    typer.echo(f"{trial.nct_id}  —  {trial.brief_title}")
    typer.echo(f"  Status:      {trial.overall_status}  ({results_state})")
    typer.echo(f"  Phase:       {trial.phase or 'N/A'}    Enrollment: {trial.enrollment}")
    typer.echo(f"  Dates:       {trial.start_date} → {trial.completion_date}")
    typer.echo(f"  Conditions:  {', '.join(trial.conditions) or '—'}")
    typer.echo(f"  Intervention: {interventions}")
    typer.echo(f"  Sponsor:     {', '.join(trial.sponsors) or '—'}")
    if trial.collaborators:
        typer.echo(f"  Collaborators: {', '.join(trial.collaborators)}")
    linked_pmids = [r.pmid for r in trial.references if r.pmid]
    if linked_pmids:
        shown = ", ".join(linked_pmids[:6]) + ("…" if len(linked_pmids) > 6 else "")
        typer.echo(f"  Ref PMIDs:   {len(linked_pmids)} ({shown})")


@graph_app.command("build")
def graph_build(
    project_number: str = typer.Argument(..., help="Core project number, e.g. R01DK075877."),
    no_trials: bool = typer.Option(False, "--no-trials", help="Skip CT.gov trial discovery."),
    no_pubs: bool = typer.Option(False, "--no-pubs", help="Skip publication links."),
    as_json: bool = typer.Option(
        False, "--json", help="Emit the full graph (nodes + edges) as JSON."
    ),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Assemble a knowledge graph centered on one award and summarize it."""
    setup_logging()
    g = graph_builder.build_award_graph(
        project_number,
        with_publications=not no_pubs,
        with_trials=not no_trials,
        use_cache=not no_cache,
    )
    if as_json:
        typer.echo(json.dumps(g.to_dict(), indent=2))
        return

    stats = g.stats()
    typer.echo(f"Graph for {project_number}: {stats['nodes']} nodes, {stats['edges']} edges")
    typer.echo(
        f"  ({stats['authoritative_edges']} authoritative, {stats['inferred_edges']} inferred)\n"
    )
    typer.echo("Nodes by type:")
    for t, n in sorted(stats["node_types"].items(), key=lambda kv: -kv[1]):
        typer.echo(f"  {t:<16} {n}")
    typer.echo("\nEdges by relation:")
    for p, n in sorted(stats["predicates"].items(), key=lambda kv: -kv[1]):
        typer.echo(f"  {p:<16} {n}")

    trials = g.nodes_of_type(linkage.CLINICAL_TRIAL)
    if trials:
        typer.echo("\nLinked clinical trials:")
        for nct in trials:
            title = g.g.nodes[nct].get("title") or ""
            typer.echo(f"  {nct.split(':', 1)[1]}  {title[:60]}")


@graph_app.command("coverage")
def graph_coverage(
    query: str | None = typer.Argument(None, help="Optional free-text topic filter."),
    years: str | None = typer.Option(None, "--years", help="Fiscal years, e.g. 2018:2024."),
    ic: list[str] | None = typer.Option(None, "--ic", help="NIH IC code. Repeatable."),
    mechanism: list[str] | None = typer.Option(None, "--mechanism", "-m", help="Activity code."),
    pi: list[str] | None = typer.Option(None, "--pi", help="PI name. Repeatable."),
    max_awards: int = typer.Option(40, "--max-awards", help="Cap on awards in the portfolio."),
    stratify: str | None = typer.Option(
        None, "--stratify", help="Bias audit: stratify a predicate by ic|mechanism|age."
    ),
    predicate: str = typer.Option(
        "produced", "--predicate", help="Predicate to stratify (with --stratify)."
    ),
    no_trials: bool = typer.Option(False, "--no-trials", help="Skip CT.gov trial discovery."),
    as_json: bool = typer.Option(False, "--json", help="Emit the coverage report as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Report per-edge-type linkage coverage over a portfolio (and bias audits)."""
    setup_logging()
    if not any([query, ic, mechanism, pi, years]):
        typer.echo("Provide at least one filter: a query, --ic, --mechanism, --pi, or --years.")
        raise typer.Exit(code=2)

    g = graph_builder.build_portfolio_graph(
        query=query,
        institutes=list(ic) if ic else None,
        fiscal_years=_parse_years(years),
        mechanisms=list(mechanism) if mechanism else None,
        pi_names=list(pi) if pi else None,
        max_awards=max_awards,
        with_trials=not no_trials,
        use_cache=not no_cache,
    )

    if stratify:
        rows = coverage_mod.stratify_coverage(g, predicate=predicate, by=stratify)
        if as_json:
            typer.echo(json.dumps([r.model_dump() for r in rows], indent=2))
            return
        n_awards = len(g.nodes_of_type(linkage.AWARD))
        typer.echo(f"Coverage of '{predicate}' by {stratify} (n={n_awards} awards):\n")
        typer.echo(f"{'stratum':<10} {'coverage':>9}   num/den")
        for r in rows:
            typer.echo(f"{r.stratum:<10} {r.coverage:>8.0%}   {r.numerator}/{r.denominator}")
        return

    report = coverage_mod.coverage_report(g)
    if as_json:
        typer.echo(report.model_dump_json(indent=2))
        return

    typer.echo(f"Linkage coverage over {report.denominator} awards:\n")
    typer.echo(f"{'edge type':<16} {'coverage':>9}   {'num/den':>9}   edges (auth/inferred)")
    for r in report.rows:
        total = r.authoritative_edges + r.inferred_edges
        typer.echo(
            f"{r.predicate:<16} {r.coverage:>8.0%}   {r.numerator:>3}/{r.denominator:<3}   "
            f"{total} ({r.authoritative_edges}/{r.inferred_edges})"
        )


@pulse_app.command("conditions")
def pulse_conditions() -> None:
    """List the conditions in the crosswalk and their outcome handles."""
    typer.echo(f"{'key':<20} {'label':<34} {'CDC cause':<22} IC")
    for c in conditions_tool.list_conditions():
        typer.echo(
            f"{c.key:<20} {c.label[:33]:<34} {(c.cdc_cause_name or '—'):<22} {c.primary_ic or '—'}"
        )


@pulse_app.command("show")
def pulse_show(
    condition: str = typer.Argument(..., help="Condition key or name, e.g. diabetes."),
    years: str | None = typer.Option(
        None, "--years", help="Funding fiscal years, e.g. 2008:2017 (default 2008:2017)."
    ),
    state: str = typer.Option("United States", "--state", help="Geography for mortality."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full pulse as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Show the funding→outcome pulse for one condition (juxtaposition, not causation)."""
    setup_logging()
    pulse = pulse_tool.condition_pulse(
        condition,
        years=_parse_years(years),
        state=state,
        use_cache=not no_cache,
    )
    if pulse is None:
        known = ", ".join(c.key for c in conditions_tool.list_conditions())
        typer.echo(f"Unknown condition '{condition}'. Known: {known}")
        raise typer.Exit(code=1)

    if as_json:
        typer.echo(pulse.model_dump_json(indent=2))
        return

    span = f"FY{min(pulse.years)}-{max(pulse.years)}"
    typer.echo(f"== Pulse: {pulse.condition_label} ==\n")
    typer.echo("INPUT - NIH funding")
    typer.echo(
        f"  {pulse.distinct_awards} distinct awards, ${pulse.total_funding:,.0f} total ({span})"
    )
    if pulse.funding_by_year:
        first, last = pulse.funding_by_year[0], pulse.funding_by_year[-1]
        typer.echo(
            f"  FY{first.fiscal_year}: ${first.total_funding / 1e6:,.0f}M "
            f"-> FY{last.fiscal_year}: ${last.total_funding / 1e6:,.0f}M"
        )
        if pulse.funding_is_floor:
            typer.echo("  (funding is a floor — some years hit the per-year record cap)")
    typer.echo("\nOUTPUT - knowledge produced")
    typer.echo(f"  {pulse.publication_count:,} PubMed publications")
    typer.echo(
        f"  {pulse.trial_count:,} clinical trials "
        f"({pulse.trials_with_results:,} with posted results)"
    )
    typer.echo("\nTRANSLATION — therapies reaching patients")
    drugs = ", ".join(pulse.example_drugs[:6]) if pulse.example_drugs else "—"
    typer.echo(f"  {pulse.approved_drug_count:,} FDA-approved drugs labeled for this condition")
    typer.echo(f"  e.g. {drugs}")
    typer.echo("\nOUTCOME — the nation's health")
    if pulse.mortality:
        rated = [p for p in pulse.mortality if p.aadr is not None]
        if rated:
            a, b = rated[0], rated[-1]
            arrow = "↓" if (pulse.mortality_aadr_change_pct or 0) < 0 else "↑"
            typer.echo(
                f"  Age-adjusted death rate: {a.aadr} ({a.year}) → {b.aadr} ({b.year}) "
                f"{arrow} {abs(pulse.mortality_aadr_change_pct or 0):.0f}% per 100k"
            )
    if pulse.prevalence_value is not None:
        typer.echo(
            f"  Prevalence (avg local est.): {pulse.prevalence_value}% — {pulse.prevalence_measure}"
        )
    typer.echo(f"\n⚠ {pulse.caveat}")


meta_app = typer.Typer(help="Meta-research (research-on-research) analyses.", no_args_is_help=True)
app.add_typer(meta_app, name="meta")

bulk_app = typer.Typer(
    help="ExPORTER bulk ingestion + DuckDB population-scale queries.", no_args_is_help=True
)
app.add_typer(bulk_app, name="bulk")


@bulk_app.command("build")
def bulk_build(
    years: str = typer.Argument(..., help="Fiscal years to load, e.g. 2020:2023 or 2021."),
) -> None:
    """Download ExPORTER projects for the years and load them into DuckDB."""
    setup_logging()
    yrs = _parse_years(years)
    if not yrs:
        typer.echo("Provide fiscal years, e.g. 2020:2023.")
        raise typer.Exit(code=2)
    typer.echo(f"Building DuckDB awards table for FY{min(yrs)}–{max(yrs)} ({len(yrs)} year(s))…")
    db = duckdb_store.build_awards_db(yrs)
    con = duckdb_store.connect(db)
    n = con.execute("SELECT count(*) FROM awards").fetchone()[0]
    con.close()
    typer.echo(f"Loaded {n:,} award rows into {db}")


@bulk_app.command("build-pubs")
def bulk_build_pubs(
    years: str = typer.Argument(..., help="Calendar years of link tables, e.g. 2000:2025."),
) -> None:
    """Download ExPORTER publication link tables and load them into DuckDB."""
    setup_logging()
    yrs = _parse_years(years)
    if not yrs:
        typer.echo("Provide years, e.g. 2000:2025.")
        raise typer.Exit(code=2)
    typer.echo(f"Loading publication link tables for {min(yrs)}–{max(yrs)}…")
    db = duckdb_store.build_publinks_db(yrs)
    con = duckdb_store.connect(db)
    n = con.execute("SELECT count(*) FROM publinks").fetchone()[0]
    con.close()
    typer.echo(f"Loaded {n:,} award→publication links into {db}")


@bulk_app.command("productivity")
def bulk_productivity(
    ic: str | None = typer.Option(None, "--ic", help="Restrict to an NIH IC, e.g. NCI."),
    year: int | None = typer.Option(None, "--year", help="Award fiscal year."),
    floor: float = typer.Option(250_000, "--floor", help="Minimum PI funding to include."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full result as JSON."),
) -> None:
    """Population-scale diminishing returns: linked-pubs-per-$ vs PI grant support."""
    setup_logging()
    con = duckdb_store.connect()
    res = duckdb_store.funding_vs_output(con, ic=ic, year=year, funding_floor=floor)
    con.close()
    if as_json:
        typer.echo(res.model_dump_json(indent=2))
        return
    if res.n_pis == 0:
        typer.echo("No data — run `bulk build` and `bulk build-pubs` first.")
        for n in res.notes:
            typer.echo(f"  {n}")
        raise typer.Exit(code=1)

    scope = f"IC={ic or 'all'} · FY={year or 'all loaded'}"
    typer.echo(f"Diminishing returns at scale ({scope}) — {res.n_pis:,} PIs\n")
    typer.echo(f"{'bin':<4} {'PIs':>6} {'mean $':>13} {'mean pubs':>10} {'pubs/$1M':>9}")
    for b in res.bins:
        typer.echo(
            f"{b.label:<4} {b.n_pis:>6} ${b.mean_funding:>12,.0f} "
            f"{b.mean_pubs:>10.1f} {b.mean_pubs_per_million:>9.2f}"
        )
    sp = res.spearman_funding_vs_pubs_per_dollar
    if sp is not None:
        typer.echo(
            f"\nSpearman(support, pubs-per-$) = {sp:+.2f}  → "
            f"{'diminishing returns' if sp < 0 else 'increasing returns'}"
        )
    for n in res.notes:
        typer.echo(f"⚠ {n}")


@bulk_app.command("latency")
def bulk_latency(
    ic: str | None = typer.Option(None, "--ic", help="Restrict to an NIH IC, e.g. NCI."),
    years: str | None = typer.Option(None, "--years", help="Grant start FYs, e.g. 2005:2015."),
    as_json: bool = typer.Option(False, "--json", help="Full result as JSON."),
) -> None:
    """Grant-to-first-publication latency at population scale (with censoring)."""
    setup_logging()
    con = duckdb_store.connect()
    res = duckdb_store.grant_to_output_latency(con, ic=ic, years=_parse_years(years))
    con.close()
    if as_json:
        typer.echo(res.model_dump_json(indent=2))
        return
    if res.grants_total == 0:
        typer.echo("No data — run `bulk build` and `bulk build-pubs` first.")
        raise typer.Exit(code=1)
    scope = f"IC={ic or 'all'} · start FY={years or 'all loaded'}"
    typer.echo(f"Grant → first publication latency ({scope})\n")
    typer.echo(f"  {res.grants_total:,} grants · {res.grants_with_output:,} with a linked pub")
    if res.median_years_to_first_pub is not None:
        typer.echo(
            f"  time to first publication: median {res.median_years_to_first_pub:.0f}y "
            f"(IQR {res.p25_years:.0f}–{res.p75_years:.0f}y)"
        )
    typer.echo("")
    for n in res.notes:
        typer.echo(f"⚠ {n}")


@bulk_app.command("foa-types")
def bulk_foa_types(
    ic: str | None = typer.Option(None, "--ic", help="Restrict to an NIH IC, e.g. NCI."),
    year: int | None = typer.Option(None, "--year", help="Award fiscal year."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full result as JSON."),
) -> None:
    """Output-per-$ by FOA type (RFA vs PA …) at population scale, from bulk data."""
    setup_logging()
    con = duckdb_store.connect()
    rows = duckdb_store.productivity_by_foa_type(con, ic=ic, year=year)
    con.close()
    if as_json:
        typer.echo(json.dumps([r.model_dump() for r in rows], indent=2))
        return
    if not rows:
        typer.echo("No data — run `bulk build` and `bulk build-pubs` first.")
        raise typer.Exit(code=1)
    scope = f"IC={ic or 'all'} · FY={year or 'all loaded'}"
    typer.echo(f"Output by FOA type ({scope}):\n")
    typer.echo(f"{'type':<8} {'awards':>8} {'funding':>15} {'pubs':>9} {'pubs/$1M':>9}")
    for r in rows:
        ppm = f"{r.pubs_per_million:.2f}" if r.pubs_per_million is not None else "—"
        typer.echo(
            f"{r.foa_type:<8} {r.awards:>8,} ${r.total_funding:>14,.0f} "
            f"{r.publications:>9,} {ppm:>9}"
        )
    typer.echo(
        "\n⚠ FOA type from number prefix; output is lifetime linked pubs "
        "(authoritative floor). Not age-adjusted across types."
    )


@bulk_app.command("concentration")
def bulk_concentration(
    ic: str | None = typer.Option(None, "--ic", help="Restrict to an NIH IC, e.g. NCI."),
    year: int | None = typer.Option(None, "--year", help="Restrict to a fiscal year."),
    top: int = typer.Option(15, "--top", help="Top PIs to display."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full result as JSON."),
) -> None:
    """Funding concentration across contact PIs (top-share + Gini) from bulk data."""
    setup_logging()
    con = duckdb_store.connect()
    res = duckdb_store.funding_concentration(con, ic=ic, year=year, top_n=top)
    con.close()
    if as_json:
        typer.echo(res.model_dump_json(indent=2))
        return
    if res.n_pis == 0:
        typer.echo("No data — run `nih-agent bulk build <years>` first.")
        raise typer.Exit(code=1)

    scope = f"IC={ic or 'all'} · FY={year or 'all loaded'}"
    typer.echo(f"Funding concentration ({scope})")
    typer.echo(f"  {res.n_pis:,} contact PIs · ${res.total_funding:,.0f} total\n")
    typer.echo(f"  Top 1% of PIs hold {res.top1_pct_share:.0%} of $")
    typer.echo(f"  Top 5% of PIs hold {res.top5_pct_share:.0%} of $")
    typer.echo(f"  Top 10% of PIs hold {res.top10_pct_share:.0%} of $")
    typer.echo(f"  Gini coefficient: {res.gini:.3f}\n")
    typer.echo("Most-funded PIs:")
    for r in res.top_pis:
        typer.echo(f"  ${r['funding']:>14,.0f}  {r['awards']:>3} awards  {r['pi_name']}")
    typer.echo(
        "\n⚠ Contact-PI attribution inflates the extreme top with large "
        "contracts/center grants (one administrative contact). Aggregate "
        "shares and Gini are robust; individual top-PI rows are not productivity."
    )


@meta_app.command("translation")
def meta_translation(
    project_number: str = typer.Argument(..., help="Core project number, e.g. R01GM118129."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full lineage as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Trace a grant's basic→clinical reach through the citation graph (inferred)."""
    setup_logging()
    lin = meta_tool.translation_lineage(project_number, use_cache=not no_cache)
    if as_json:
        typer.echo(lin.model_dump_json(indent=2))
        return
    typer.echo(f"Translation lineage — {lin.core_project_num} (FY{lin.grant_fiscal_year})\n")
    typer.echo(f"  {lin.publications} linked publications")
    typer.echo(
        f"  mean APT (potential to translate): {lin.mean_apt if lin.mean_apt is not None else '—'}"
    )
    typer.echo(
        f"  {lin.pubs_with_clinical_citation} of them cited by clinical work → "
        f"{lin.clinical_citation_reach} distinct clinical papers reached"
    )
    if lin.years_to_first_clinical_citation is not None:
        typer.echo(
            f"  time to first clinical citation: {lin.years_to_first_clinical_citation} years"
        )
    if lin.example_clinical_citers:
        typer.echo(
            f"  example clinical citers (PMID): {', '.join(lin.example_clinical_citers[:6])}"
        )
    typer.echo("")
    for n in lin.notes:
        typer.echo(f"⚠ {n}")


@meta_app.command("open-science")
def meta_open_science(
    project_number: str = typer.Argument(..., help="Core project number."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full result as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Open-science signals for a grant: PMC availability, datasets, trial registration."""
    setup_logging()
    s = meta_tool.assess_open_science_signals(project_number, use_cache=not no_cache)
    if as_json:
        typer.echo(s.model_dump_json(indent=2))
        return
    typer.echo(f"Open-science signals — {s.core_project_num}\n")
    typer.echo(f"  {s.publications} publications")
    pmc = f"{s.pmc_fraction:.0%}" if s.pmc_fraction is not None else "—"
    typer.echo(f"  PubMed Central availability: {pmc}")
    typer.echo(f"  registered clinical trial: {'yes' if s.has_registered_trial else 'no'}")
    typer.echo(f"  dataset accessions (abstract): {', '.join(s.dataset_accessions) or 'none'}")
    typer.echo("")
    for n in s.notes:
        typer.echo(f"⚠ {n}")


@meta_app.command("brief")
def meta_brief(
    topic: str = typer.Argument(..., help="Topic, e.g. 'exposome'."),
    years: str | None = typer.Option(None, "--years", help="Fiscal years, e.g. 2014:2023."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full brief as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Synthesize a meta-research brief (composition, redundancy, open-science, translation)."""
    setup_logging()
    b = meta_tool.build_meta_research_brief(
        topic, years=_parse_years(years), use_cache=not no_cache
    )
    if as_json:
        typer.echo(b.model_dump_json(indent=2))
        return
    yr = f"FY{min(b.years)}–{max(b.years)}" if b.years else ""
    typer.echo(f"━━ Meta-research brief: {b.topic} ({yr}) ━━\n")
    typer.echo(f"  Portfolio: {b.distinct_grants:,} grants · ${b.total_funding:,.0f}")
    typer.echo(f"  Top PIs: {', '.join(b.top_pis)}")
    typer.echo(f"  Topic-overlap pairs (cross-PI): {b.redundant_pairs_cross_pi}")
    pmc = f"{b.open_science_pmc_fraction:.0%}" if b.open_science_pmc_fraction is not None else "—"
    typer.echo(
        f"  Open science (top {b.open_science_sampled}): PMC avail {pmc}, "
        f"{b.grants_with_trial} with a registered trial"
    )
    if b.top_translation:
        typer.echo("  Top clinical reach:")
        for t in b.top_translation:
            typer.echo(f"     {t['reach']:>4}  {t['core']}  {t['title']}")
    typer.echo("")
    for c in b.caveats:
        typer.echo(f"⚠ {c}")


@meta_app.command("redundancy")
def meta_redundancy(
    query: str | None = typer.Argument(None, help="Topic, e.g. 'PFAS exposure'."),
    ic: list[str] | None = typer.Option(None, "--ic", help="NIH IC code. Repeatable."),
    years: str | None = typer.Option(None, "--years", help="Fiscal years, e.g. 2018:2022."),
    min_jaccard: float = typer.Option(0.4, "--min-jaccard", help="Min term overlap to flag."),
    cross_pi_only: bool = typer.Option(False, "--cross-pi", help="Only show different-PI pairs."),
    top: int = typer.Option(15, "--top", help="Pairs to show."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full result as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Find topically near-duplicate grant pairs in a portfolio (term overlap)."""
    setup_logging()
    if not any([query, ic, years]):
        typer.echo("Provide at least one filter: a query, --ic, or --years.")
        raise typer.Exit(code=2)
    res = meta_tool.detect_portfolio_redundancy(
        query=query,
        institutes=list(ic) if ic else None,
        fiscal_years=_parse_years(years),
        min_jaccard=min_jaccard,
        use_cache=not no_cache,
    )
    if as_json:
        typer.echo(res.model_dump_json(indent=2))
        return
    pairs = [p for p in res.pairs if not p.same_pi] if cross_pi_only else res.pairs
    typer.echo(f"Topic overlap across {res.grants_analyzed} grants (Jaccard ≥ {min_jaccard}):\n")
    for p in pairs[:top]:
        flag = "same-PI" if p.same_pi else "CROSS-PI"
        typer.echo(f"  {p.jaccard:.2f}  [{flag}]  {p.core_a} ↔ {p.core_b}")
        typer.echo(f"        {(p.title_a or '')[:64]}")
        typer.echo(f"        {(p.title_b or '')[:64]}")
        typer.echo(f"        shared: {', '.join(p.shared_terms[:8])}")
    typer.echo("")
    for n in res.notes:
        typer.echo(f"⚠ {n}")


@meta_app.command("translation-scan")
def meta_translation_scan(
    query: str | None = typer.Argument(None, help="Topic, e.g. 'CRISPR gene editing'."),
    ic: list[str] | None = typer.Option(None, "--ic", help="NIH IC code. Repeatable."),
    years: str | None = typer.Option(None, "--years", help="Fiscal years, e.g. 2013:2017."),
    max_grants: int = typer.Option(30, "--max-grants", help="Grants to scan."),
    top: int = typer.Option(12, "--top", help="Top results to show."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Rank a portfolio's grants by basic→clinical citation reach (discovery→translation)."""
    setup_logging()
    if not any([query, ic, years]):
        typer.echo("Provide at least one filter: a query, --ic, or --years.")
        raise typer.Exit(code=2)
    rows = meta_tool.scan_translation(
        query=query,
        institutes=list(ic) if ic else None,
        fiscal_years=_parse_years(years),
        max_grants=max_grants,
        use_cache=not no_cache,
    )
    if as_json:
        typer.echo(json.dumps([r.model_dump() for r in rows], indent=2))
        return
    typer.echo(
        f"Discovery → translation (clinical citation reach), top {top} of {len(rows)} grants:\n"
    )
    typer.echo(f"{'reach':>5} {'pubs':>5} {'APT':>5} {'latency':>7}  core            title")
    for r in rows[:top]:
        apt = f"{r.mean_apt:.2f}" if r.mean_apt is not None else "—"
        lat = (
            f"{r.years_to_first_clinical_citation}y"
            if r.years_to_first_clinical_citation is not None
            else "—"
        )
        title = (r.title or "")[:40]
        typer.echo(
            f"{r.clinical_citation_reach:>5} {r.publications:>5} {apt:>5} {lat:>7}  "
            f"{r.core_project_num:<14} {title}"
        )


@meta_app.command("diminishing-returns")
def meta_diminishing_returns(
    query: str | None = typer.Argument(None, help="Optional topic filter."),
    ic: list[str] | None = typer.Option(None, "--ic", help="NIH IC code. Repeatable."),
    years: str | None = typer.Option(None, "--years", help="Fiscal years, e.g. 2014:2016."),
    max_pis: int = typer.Option(60, "--max-pis", help="PIs to analyze (stratified by support)."),
    floor: float = typer.Option(150_000, "--floor", help="Minimum PI funding to include."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full result as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Reproduce Open Mike's 'diminishing returns': output-per-$ vs PI grant support."""
    setup_logging()
    if not any([query, ic, years]):
        typer.echo("Provide at least one filter: a query, --ic, or --years.")
        raise typer.Exit(code=2)

    res = meta_tool.grant_support_vs_productivity(
        institutes=list(ic) if ic else None,
        fiscal_years=_parse_years(years),
        query=query,
        funding_floor=floor,
        max_pis=max_pis,
        use_cache=not no_cache,
    )
    if as_json:
        typer.echo(res.model_dump_json(indent=2))
        return

    typer.echo(f"Diminishing returns — {res.pis_analyzed} of {res.pis_total} PIs analyzed\n")
    typer.echo("Funding quartile → research output per $1M (summed RCR):")
    typer.echo(f"{'bin':<4} {'PIs':>4} {'mean $':>13} {'mean RCR':>9} {'RCR/$1M':>9}")
    for b in res.bins:
        typer.echo(
            f"{b.label:<4} {b.n_pis:>4} ${b.mean_funding:>12,.0f} "
            f"{b.mean_weighted_rcr:>9.1f} {b.mean_rcr_per_million:>9.2f}"
        )
    sp = res.spearman_funding_vs_output_per_dollar
    if sp is not None:
        direction = "diminishing returns" if sp < 0 else "increasing returns"
        typer.echo(f"\nSpearman(support, output-per-$) = {sp:+.2f}  → {direction}")
    typer.echo("")
    for n in res.notes:
        typer.echo(f"⚠ {n}")


@app.command("brief")
def brief(
    topic: str = typer.Argument(
        ..., help="Portfolio topic, e.g. 'diabetes' or 'PFAS cardiometabolic'."
    ),
    years: str | None = typer.Option(None, "--years", help="Fiscal years, e.g. 2014:2023."),
    ic: list[str] | None = typer.Option(None, "--ic", help="Restrict to NIH IC(s). Repeatable."),
    audience: str = typer.Option("NIH OD", "--audience", help="Brief audience label."),
    sample: int = typer.Option(
        12, "--sample", help="Awards to deep-enrich (pubs/trials/datasets)."
    ),
    out: str | None = typer.Option(
        None, "--out", help="Write Markdown to this path instead of stdout."
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit the structured brief as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Generate a synthesis portfolio brief (composition → outputs → translation → outcome)."""
    setup_logging()
    b = briefs_tool.build_portfolio_brief(
        topic,
        years=_parse_years(years),
        audience=audience,
        institutes=list(ic) if ic else None,
        sample_awards=sample,
        use_cache=not no_cache,
    )
    if as_json:
        typer.echo(b.model_dump_json(indent=2))
        return
    md = briefs_tool.render_brief_markdown(b)
    if out:
        from pathlib import Path

        Path(out).write_text(md)
        typer.echo(
            f"Wrote brief to {out} "
            f"({b.distinct_awards} awards, {b.publication_count} pubs sampled)."
        )
    else:
        typer.echo(md)


foa_app = typer.Typer(
    help="Resolve funding opportunity announcements (FOA/RFA).", no_args_is_help=True
)
app.add_typer(foa_app, name="foa")


@foa_app.command("get")
def foa_get(
    foa_number: str = typer.Argument(..., help="FOA number, e.g. PA-20-185 or RFA-CA-19-039."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full record as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Resolve an FOA number to its announcement metadata (grants.gov + NIH Guide)."""
    setup_logging()
    a = foa.get_foa(foa_number, use_cache=not no_cache)
    if as_json:
        typer.echo(a.model_dump_json(indent=2))
        return
    typer.echo(f"{a.foa_number}  [{a.foa_type}]  ({a.status or 'unknown status'})")
    typer.echo(f"  {a.title or '(title unavailable)'}")
    typer.echo(
        f"  Agency: {a.agency or '—'}   Open: {a.open_date or '—'}   Close: {a.close_date or '—'}"
    )
    typer.echo(f"  Guide:  {a.guide_url}")
    typer.echo(f"  Source: {a.source}")


databook_app = typer.Typer(
    help="NIH Data Book aggregate reference statistics (success rates, …).",
    no_args_is_help=True,
)
app.add_typer(databook_app, name="databook")


@databook_app.command("success-rates")
def databook_success_rates(
    since: int = typer.Option(2000, "--since", help="First fiscal year to show."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full series as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Re-download the report."),
) -> None:
    """NIH success rates by fiscal year (the application funnel RePORTER lacks)."""
    setup_logging()
    rows = [r for r in databook.r01_success_rates(use_cache=not no_cache) if r.fiscal_year >= since]
    if as_json:
        typer.echo(json.dumps([r.model_dump() for r in rows], indent=2))
        return
    typer.echo("NIH success rates (aggregate reference — not joinable to individual grants)\n")
    typer.echo(
        f"{'FY':<6} {'RPG apps':>9} {'RPG awd':>8} {'RPG%':>6}   "
        f"{'R01 apps':>9} {'R01 awd':>8} {'R01%':>6}"
    )
    for r in rows:
        rpg = f"{r.rpg_success_rate:.0%}" if r.rpg_success_rate is not None else "—"
        r01 = f"{r.r01_success_rate:.0%}" if r.r01_success_rate is not None else "—"
        typer.echo(
            f"{r.fiscal_year:<6} {r.rpg_applications or 0:>9,} {r.rpg_awards or 0:>8,} {rpg:>7}   "
            f"{r.r01_applications or 0:>9,} {r.r01_awards or 0:>8,} {r01:>7}"
        )


benchmark_app = typer.Typer(help="Curated benchmark topics for validation.", no_args_is_help=True)
app.add_typer(benchmark_app, name="benchmark")


@benchmark_app.command("list")
def benchmark_list() -> None:
    """List the curated benchmark topics and what each expects."""
    for b in benchmarks_tool.list_benchmarks():
        typer.echo(f"{b.key:<20} {b.label}")
        typer.echo(f"    query: {b.query!r}  ·  {b.rationale}")


@benchmark_app.command("run")
def benchmark_run(
    key: str | None = typer.Argument(None, help="Benchmark key (omit to run all)."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
    as_json: bool = typer.Option(False, "--json", help="Emit results as JSON."),
) -> None:
    """Run benchmark(s): build each brief and check its expected kinds of results."""
    setup_logging()
    if key:
        bench = benchmarks_tool.get_benchmark(key)
        if bench is None:
            typer.echo(f"Unknown benchmark '{key}'.")
            raise typer.Exit(code=2)
        benches = [bench]
    else:
        benches = benchmarks_tool.list_benchmarks()

    results = [benchmarks_tool.run_benchmark(b, use_cache=not no_cache) for b in benches]
    if as_json:
        typer.echo(json.dumps([r.model_dump() for r in results], indent=2))
        return

    n_pass = sum(r.passed for r in results)
    for r in results:
        typer.echo(f"{'PASS' if r.passed else 'FAIL'}  {r.key:<20} {r.distinct_awards:>5} awards")
        for c in r.checks:
            mark = "✓" if c.passed else "✗"
            typer.echo(f"      {mark} {c.name}: {c.detail}")
    typer.echo(f"\n{n_pass}/{len(results)} benchmarks passed")
    if n_pass < len(results):
        raise typer.Exit(code=1)


nams_app = typer.Typer(
    help="Preclinical / NAMs (alternative methods) — ICE chemicals + NIH portfolio.",
    no_args_is_help=True,
)
app.add_typer(nams_app, name="nams")


@nams_app.command("chemical")
def nams_chemical(
    chemid: str = typer.Argument(..., help="CASRN or DTXSID, e.g. 80-05-7 (bisphenol A)."),
    as_json: bool = typer.Option(False, "--json", help="Emit the summary as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Summarize a chemical's NAMs assay coverage from NICEATM ICE."""
    setup_logging()
    s = nams_conn.chemical_assay_summary(chemid, use_cache=not no_cache)
    if as_json:
        typer.echo(s.model_dump_json(indent=2))
        return
    typer.echo(f"ICE assay coverage — {s.chemid}\n")
    typer.echo(
        f"  {s.total_records:,} assay records · {s.distinct_assays} assays · "
        f"{s.distinct_endpoints} endpoints"
    )
    typer.echo("  top assays:")
    for a in s.top_assays[:6]:
        typer.echo(f"     {a['n']:>5}  {a['assay']}")
    typer.echo(f"  source: {s.source_url}")


@nams_app.command("portfolio")
def nams_portfolio(
    area: str | None = typer.Option(None, "--area", help="immunology | ad_adrd | cardiometabolic."),
    years: str | None = typer.Option(None, "--years", help="Fiscal years, e.g. 2015:2023."),
    as_json: bool = typer.Option(False, "--json", help="Emit the full map as JSON."),
    no_cache: bool = typer.Option(False, "--no-cache", help="Bypass the on-disk response cache."),
) -> None:
    """Map NIH NAM usage (organ-on-chip, iPSC, organoids …) across preclinical areas."""
    setup_logging()
    yrs = _parse_years(years)
    areas = [area] if area else None
    pmap = preclinical_tool.nams_portfolio_map(areas=areas, years=yrs, use_cache=not no_cache)
    if as_json:
        typer.echo(pmap.model_dump_json(indent=2))
        return
    for a in pmap.areas:
        typer.echo(f"━━ {a.area} ━━  ({a.grants_with_nam}/{a.grants_scanned} grants mention a NAM)")
        for method, n in a.by_method.items():
            typer.echo(f"   {n:>4}  {method}")
        typer.echo(f"   ⚠ {a.coverage_note}\n")


if __name__ == "__main__":
    app()
