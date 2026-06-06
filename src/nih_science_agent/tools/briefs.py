"""Portfolio brief generator — the first synthesis deliverable.

`build_portfolio_brief` ties the connectors, linkage layer, and analysis tools
into one auditable narrative for a topic: portfolio composition, knowledge
outputs (publications + RCR), translation (trials, approvals), candidate reusable
datasets, an optional population-health pulse, and — crucially — coverage and
caveats. It renders to Markdown so the same object serves an OD brief and a paper
figure.

Design discipline (see docs/knowledge-creation-at-nih.md): deep enrichment is
sampled from the highest-funded awards and the sample size is always disclosed;
dataset/publication evidence is labeled authoritative vs inferred; nothing is
silently truncated.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from nih_science_agent.connectors import clinicaltrials, reporter
from nih_science_agent.linkage import accession_extraction as accession
from nih_science_agent.linkage import edges as linkage
from nih_science_agent.tools import conditions as conditions_tool
from nih_science_agent.tools import pulse as pulse_tool


class Ranked(BaseModel):
    name: str
    count: int
    funding: float = 0.0


class TopPublication(BaseModel):
    pmid: str
    title: str | None = None
    rcr: float | None = None
    citations: int | None = None


class PortfolioBrief(BaseModel):
    # 1. query + filters
    topic: str
    audience: str
    years: list[int] = Field(default_factory=list)
    institutes: list[str] | None = None
    # composition
    distinct_awards: int = 0
    total_funding: float = 0.0
    awards_by_year: dict[int, int] = Field(default_factory=dict)
    funding_by_year: dict[int, float] = Field(default_factory=dict)
    top_ics: list[Ranked] = Field(default_factory=list)
    top_mechanisms: list[Ranked] = Field(default_factory=list)
    top_institutions: list[Ranked] = Field(default_factory=list)
    top_pis: list[Ranked] = Field(default_factory=list)
    # outputs / translation (sampled from top-funded awards)
    sample_size: int = 0
    publication_count: int = 0
    top_publications: list[TopPublication] = Field(default_factory=list)
    trials: list[str] = Field(default_factory=list)  # "NCT — title"
    datasets: list[str] = Field(default_factory=list)  # "REPO:accession"
    # outcome (if topic maps to a condition)
    condition_label: str | None = None
    mortality_summary: str | None = None
    approved_drug_count: int | None = None
    # gaps / provenance
    coverage_notes: list[str] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    generated_at: str | None = None


def _ranked(counter: Counter, funding: dict[str, float] | None = None, n: int = 5) -> list[Ranked]:
    out = []
    for name, count in counter.most_common(n):
        out.append(Ranked(name=name, count=count, funding=(funding or {}).get(name, 0.0)))
    return out


def build_portfolio_brief(
    topic: str,
    years: list[int] | None = None,
    audience: str = "NIH OD",
    institutes: list[str] | None = None,
    condition: str | None = None,
    per_year_cap: int = 800,
    sample_awards: int = 12,
    use_cache: bool = True,
) -> PortfolioBrief:
    """Build a synthesis brief for ``topic`` over ``years``.

    Composition is computed by querying each fiscal year independently (a single
    multi-year search sorts newest-first and exhausts its cap inside the latest
    year, hiding the trend). Publication/trial/dataset enrichment is sampled from
    the ``sample_awards`` highest-funded grants (the sample size is reported). If
    ``topic`` maps to a canonical condition, a population-health pulse is attached.
    """
    years = years or list(range(2014, 2024))
    ts = datetime.now(UTC).replace(microsecond=0).isoformat()
    brief = PortfolioBrief(
        topic=topic, audience=audience, years=years, institutes=institutes, generated_at=ts
    )

    # -- Composition, queried per fiscal year ---------------------------- #
    cores: dict[str, reporter.ReporterProject] = {}
    core_abstract: dict[str, str] = {}  # abstract may live on a different support-year
    ic_ct, mech_ct, inst_ct, pi_ct = Counter(), Counter(), Counter(), Counter()
    inst_fund: dict[str, float] = defaultdict(float)
    awards_by_year: Counter = Counter()
    funding_by_year: dict[int, float] = defaultdict(float)
    seen_cores: set[str] = set()  # for once-per-portfolio entity rankings
    capped = False

    for y in years:
        projects = reporter.search_projects(
            query=topic,
            fiscal_years=[y],
            institutes=institutes,
            limit=per_year_cap,
            use_cache=use_cache,
        )
        if len(projects) >= per_year_cap:
            capped = True
        year_cores: set[str] = set()
        for p in projects:
            core = p.core_project_num or p.project_number
            if not core:
                continue
            year_cores.add(core)
            if core not in cores or (p.total_cost or 0) > (cores[core].total_cost or 0):
                cores[core] = p
            if p.abstract_text and core not in core_abstract:
                core_abstract[core] = p.abstract_text
            funding_by_year[y] += p.total_cost or 0.0
            # entity rankings: count each distinct grant once across the portfolio
            if core in seen_cores:
                continue
            seen_cores.add(core)
            if p.nih_institute_or_center:
                ic_ct[p.nih_institute_or_center] += 1
            if p.activity_code:
                mech_ct[p.activity_code] += 1
            if p.organization.name:
                inst_ct[p.organization.name] += 1
                inst_fund[p.organization.name] += p.total_cost or 0.0
            for pi in p.principal_investigators:
                if pi.full_name:
                    pi_ct[pi.full_name] += 1
        awards_by_year[y] = len(year_cores)  # active distinct grants that year

    if capped:
        brief.coverage_notes.append(
            f"Some years hit the per-year cap ({per_year_cap}); composition counts/funding "
            "are a floor for those years. Narrow the topic or add --ic for full coverage."
        )

    brief.distinct_awards = len(cores)
    brief.total_funding = sum(funding_by_year.values())
    brief.awards_by_year = dict(sorted(awards_by_year.items()))
    brief.funding_by_year = dict(sorted(funding_by_year.items()))
    brief.top_ics = _ranked(ic_ct)
    brief.top_mechanisms = _ranked(mech_ct)
    brief.top_institutions = _ranked(inst_ct, inst_fund)
    brief.top_pis = _ranked(pi_ct)

    # -- Sampled enrichment: pubs, trials, datasets ---------------------- #
    sample = sorted(cores.values(), key=lambda p: p.total_cost or 0, reverse=True)[:sample_awards]
    brief.sample_size = len(sample)

    pubs: list[TopPublication] = []
    awards_with_pub = awards_with_trial = awards_with_dataset = 0
    for p in sample:
        core = p.core_project_num or p.project_number
        links = linkage.link_award_publications(
            core, enrich=True, metrics=True, limit=50, use_cache=use_cache
        )
        if links:
            awards_with_pub += 1
        brief.publication_count += len(links)
        for lk in links:
            rcr = lk.metrics.relative_citation_ratio if lk.metrics else None
            pubs.append(
                TopPublication(
                    pmid=lk.pmid,
                    title=lk.article.title if lk.article else None,
                    rcr=rcr,
                    citations=lk.metrics.citation_count if lk.metrics else None,
                )
            )
        trials = clinicaltrials.find_trials_for_grant(core, use_cache=use_cache)
        if trials:
            awards_with_trial += 1
        for t in trials:
            label = f"{t.nct_id} — {t.brief_title or ''}"
            if label not in brief.trials:
                brief.trials.append(label)
        # datasets via accession mining of the (public) abstract — inferred edges
        matches = accession.extract_accessions(core_abstract.get(core, ""))
        if matches:
            awards_with_dataset += 1
        for m in matches:
            tag = f"{m.repository}:{m.accession}"
            if tag not in brief.datasets:
                brief.datasets.append(tag)

    pubs.sort(key=lambda x: x.rcr or 0.0, reverse=True)
    brief.top_publications = pubs[:10]

    # -- Coverage (over the enriched sample) ----------------------------- #
    if brief.sample_size:
        n = brief.sample_size
        brief.coverage_notes.append(
            f"Output/translation enrichment sampled from the {n} highest-funded awards "
            f"(of {brief.distinct_awards}). Within that sample: "
            f"{awards_with_pub}/{n} have ≥1 authoritative publication, "
            f"{awards_with_trial}/{n} a self-reported trial, "
            f"{awards_with_dataset}/{n} a mined dataset accession (inferred)."
        )

    # -- Outcome pulse if the topic maps to a condition ------------------ #
    # An explicit condition overrides the keyword crosswalk (e.g. a "GLP-1"
    # portfolio whose outcome is diabetes, which the keywords wouldn't match).
    cond = (
        conditions_tool.get_condition(condition)
        if condition
        else conditions_tool.resolve_condition(topic)
    )
    if cond is not None:
        pulse = pulse_tool.condition_pulse(cond.key, years=years, use_cache=use_cache)
        if pulse is not None:
            brief.condition_label = pulse.condition_label
            brief.approved_drug_count = pulse.approved_drug_count
            rated = [m for m in pulse.mortality if m.aadr is not None]
            if len(rated) >= 2:
                a, b = rated[0], rated[-1]
                brief.mortality_summary = (
                    f"Age-adjusted death rate {a.aadr} ({a.year}) → {b.aadr} ({b.year}) "
                    f"({pulse.mortality_aadr_change_pct:+.0f}%)"
                )

    # -- Caveats + sources ----------------------------------------------- #
    brief.caveats = [
        "Linkage is largely authoritative-only; RePORTER publication attribution "
        "is incomplete and biased, so outputs are undercounted (recall ceiling).",
        "Dataset accessions are NLP-inferred from abstracts (low confidence), not asserted links.",
        "Funding/outputs are not age-adjusted; recent awards have had less time to produce.",
    ]
    if brief.condition_label:
        brief.caveats.append(
            "Health outcome is a juxtaposition, NOT causal attribution — decades of "
            "lag and many non-NIH drivers shape population mortality."
        )
    brief.sources = [
        "NIH RePORTER v2 (awards, publication links)",
        "NCBI E-utilities + iCite (publication metadata, RCR)",
        "ClinicalTrials.gov v2 (trials)",
    ]
    if brief.condition_label:
        brief.sources += ["CDC data.cdc.gov (mortality)", "openFDA (drug approvals)"]
    return brief


def render_brief_markdown(b: PortfolioBrief) -> str:
    """Render a :class:`PortfolioBrief` as a Markdown document."""
    L: list[str] = []
    L.append(f"# Portfolio brief: {b.topic}")
    L.append(f"*Audience: {b.audience} · generated {b.generated_at}*\n")

    L.append("## 1. Query & filters")
    yrs = f"FY{min(b.years)}–{max(b.years)}" if b.years else "all years"
    ics = ", ".join(b.institutes) if b.institutes else "all ICs"
    L.append(f"- Topic: **{b.topic}** · {yrs} · {ics}\n")

    L.append("## 2. Portfolio composition")
    L.append(f"- **{b.distinct_awards:,} distinct awards**, ${b.total_funding:,.0f} total\n")
    L.append("| FY | awards | funding |")
    L.append("|---|--:|--:|")
    for y in sorted(b.awards_by_year):
        L.append(f"| {y} | {b.awards_by_year[y]} | ${b.funding_by_year.get(y, 0):,.0f} |")
    L.append("")

    def table(title: str, rows: list[Ranked], show_funding: bool = False) -> None:
        L.append(f"### {title}")
        if show_funding:
            L.append("| | awards | funding |\n|---|--:|--:|")
            for r in rows:
                L.append(f"| {r.name} | {r.count} | ${r.funding:,.0f} |")
        else:
            L.append("| | awards |\n|---|--:|")
            for r in rows:
                L.append(f"| {r.name} | {r.count} |")
        L.append("")

    table("3. Top Institutes (ICs)", b.top_ics)
    table("4. Top mechanisms", b.top_mechanisms)
    table("5. Top institutions", b.top_institutions, show_funding=True)
    table("6. Top PIs", b.top_pis)

    L.append("## 7. Publications & impact")
    L.append(
        f"- {b.publication_count:,} linked publications across the {b.sample_size}-award sample"
    )
    if b.top_publications:
        L.append("\n| PMID | RCR | cites | title |")
        L.append("|---|--:|--:|---|")
        for p in b.top_publications:
            rcr = f"{p.rcr:.1f}" if p.rcr is not None else "—"
            L.append(f"| {p.pmid} | {rcr} | {p.citations or '—'} | {(p.title or '')[:70]} |")
    L.append("")

    L.append("## 8. Related clinical trials")
    L.extend(
        [f"- {t}" for t in b.trials[:10]] or ["- (none with a self-reported grant link in sample)"]
    )
    L.append("")

    L.append("## 9. Candidate reusable datasets (inferred)")
    L.extend(
        [f"- {d}" for d in b.datasets[:15]] or ["- (no accessions mined from sampled abstracts)"]
    )
    L.append("")

    if b.condition_label:
        L.append("## Population health context")
        L.append(f"- Condition: **{b.condition_label}**")
        if b.mortality_summary:
            L.append(f"- {b.mortality_summary}")
        if b.approved_drug_count is not None:
            L.append(f"- {b.approved_drug_count:,} FDA-approved drugs labeled for this condition")
        L.append("")

    L.append("## 10. Evidence gaps & caveats")
    L.extend([f"- {c}" for c in b.coverage_notes])
    L.extend([f"- {c}" for c in b.caveats])
    L.append("")

    L.append("## 11. Sources & retrieval")
    L.extend([f"- {s}" for s in b.sources])
    L.append(f"- Retrieved: {b.generated_at}")
    return "\n".join(L)
