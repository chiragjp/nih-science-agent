"""Meta-research: research-on-research analyses over NIH portfolios.

First function reproduces Open Mike's "diminishing returns" finding (Lauer et al.,
"Award or Reward?"): as a principal investigator's total grant **support** rises,
research **output per dollar** falls. We approximate the original Grant Support
Index with total RePORTER funding, and output with summed iCite Relative Citation
Ratio (RCR) over the PI's authoritatively linked publications.

Honest limits (these belong in any write-up):
- Publication/RCR counts are lifetime, not windowed; recent awards are
  right-censored (less time to publish) and look less productive.
- PI support is proxied by total funding, not NIH's internal point-based GSI.
- Linkage is authoritative-only (RePORTER pub lists undercount), so output is a
  floor. The analysis is bounded/sampled and discloses it.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from nih_science_agent.connectors import clinicaltrials, icite, pubmed, reporter
from nih_science_agent.linkage import accession_extraction as accession


class PIProductivity(BaseModel):
    pi: str
    profile_id: int | None = None
    grant_count: int = 0
    total_funding: float = 0.0
    publication_count: int = 0
    weighted_rcr: float = 0.0  # sum of RCR over the PI's linked publications
    rcr_per_million: float | None = None  # output per $1M of support


class SupportBin(BaseModel):
    label: str
    n_pis: int
    mean_funding: float
    mean_weighted_rcr: float
    mean_rcr_per_million: float


class DiminishingReturnsResult(BaseModel):
    criteria: dict = Field(default_factory=dict)
    pis_total: int = 0
    pis_analyzed: int = 0
    rows: list[PIProductivity] = Field(default_factory=list)
    bins: list[SupportBin] = Field(default_factory=list)
    spearman_funding_vs_output_per_dollar: float | None = None
    notes: list[str] = Field(default_factory=list)


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    """Spearman rank correlation (ties broken by order; adequate for a trend)."""
    n = len(xs)
    if n < 3:
        return None

    def ranks(v: list[float]) -> list[float]:
        order = sorted(range(n), key=lambda i: v[i])
        r = [0.0] * n
        for rank, i in enumerate(order):
            r[i] = float(rank)
        return r

    rx, ry = ranks(xs), ranks(ys)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    vy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return cov / (vx * vy) if vx and vy else None


def _stratified_sample(pis: list[PIProductivity], cap: int) -> list[PIProductivity]:
    """Sample ``cap`` PIs spanning the funding range (not just the top)."""
    if len(pis) <= cap:
        return pis
    ordered = sorted(pis, key=lambda p: p.total_funding)
    step = len(ordered) / cap
    return [ordered[int(i * step)] for i in range(cap)]


def grant_support_vs_productivity(
    institutes: list[str] | None = None,
    fiscal_years: list[int] | None = None,
    query: str | None = None,
    funding_floor: float = 150_000.0,
    max_records: int = 1500,
    max_pis: int = 60,
    pub_limit: int = 200,
    use_cache: bool = True,
) -> DiminishingReturnsResult:
    """Reproduce the diminishing-returns pattern over a bounded PI population.

    Groups a portfolio's awards by PI, then for a stratified sample of PIs
    (spanning the support range) computes summed RCR output and output-per-dollar.
    Bins by funding quartile and reports the Spearman correlation between support
    and output-per-dollar (negative ⇒ diminishing returns).
    """
    result = DiminishingReturnsResult(
        criteria={"institutes": institutes, "fiscal_years": fiscal_years, "query": query}
    )

    projects = reporter.search_projects(
        query=query,
        institutes=institutes,
        fiscal_years=fiscal_years,
        limit=max_records,
        use_cache=use_cache,
    )
    if len(projects) >= max_records:
        result.notes.append(
            f"Award population capped at {max_records} records; PI support is a floor."
        )

    # Group awards by PI (profile_id preferred; name fallback).
    pi_funding: dict[str, float] = defaultdict(float)
    pi_name: dict[str, str] = {}
    pi_profile: dict[str, int | None] = {}
    pi_cores: dict[str, set[str]] = defaultdict(set)
    for p in projects:
        core = p.core_project_num or p.project_number
        for pi in p.principal_investigators:
            if not pi.full_name:
                continue
            key = f"profile:{pi.profile_id}" if pi.profile_id else f"name:{pi.full_name.lower()}"
            pi_funding[key] += p.total_cost or 0.0
            pi_name.setdefault(key, pi.full_name)
            pi_profile.setdefault(key, pi.profile_id)
            if core:
                pi_cores[key].add(core)

    all_pis = [
        PIProductivity(
            pi=pi_name[k],
            profile_id=pi_profile[k],
            grant_count=len(pi_cores[k]),
            total_funding=pi_funding[k],
        )
        for k in pi_funding
    ]
    result.pis_total = len(all_pis)

    # Drop PIs below the funding floor: tiny windowed allocations (co-PIs with a
    # near-$0 split) otherwise explode output-per-dollar into meaningless outliers.
    eligible = [p for p in all_pis if p.total_funding >= funding_floor]
    if len(eligible) < len(all_pis):
        result.notes.append(
            f"Excluded {len(all_pis) - len(eligible)} PIs below the ${funding_floor:,.0f} "
            "funding floor (tiny windowed allocations distort output-per-dollar)."
        )

    sample = _stratified_sample(eligible, max_pis)
    if len(sample) < len(eligible):
        result.notes.append(
            f"Analyzed a stratified sample of {len(sample)} of {len(eligible)} eligible PIs "
            "spanning the funding range (publication fetch is the cost)."
        )

    # Enrich each sampled PI with summed RCR over their linked publications.
    key_for = {pi_name[k]: k for k in pi_funding}
    for row in sample:
        k = key_for.get(row.pi)
        pmids: set[str] = set()
        for core in pi_cores.get(k, set()):
            for link in reporter.get_publications(core, limit=pub_limit, use_cache=use_cache):
                if link.pmid:
                    pmids.add(link.pmid)
        row.publication_count = len(pmids)
        if pmids:
            metrics = icite.fetch_metrics(sorted(pmids), use_cache=use_cache)
            row.weighted_rcr = sum(m.relative_citation_ratio or 0.0 for m in metrics)
        if row.total_funding > 0:
            row.rcr_per_million = row.weighted_rcr / (row.total_funding / 1_000_000)

    analyzed = [r for r in sample if r.rcr_per_million is not None]
    result.pis_analyzed = len(analyzed)
    result.rows = sorted(analyzed, key=lambda r: r.total_funding)

    # Funding-quartile bins → mean output-per-dollar per bin.
    if len(analyzed) >= 4:
        ordered = sorted(analyzed, key=lambda r: r.total_funding)
        q = len(ordered) // 4
        chunks = [ordered[:q], ordered[q : 2 * q], ordered[2 * q : 3 * q], ordered[3 * q :]]
        for i, chunk in enumerate(chunks, 1):
            if not chunk:
                continue
            result.bins.append(
                SupportBin(
                    label=f"Q{i}",
                    n_pis=len(chunk),
                    mean_funding=sum(c.total_funding for c in chunk) / len(chunk),
                    mean_weighted_rcr=sum(c.weighted_rcr for c in chunk) / len(chunk),
                    mean_rcr_per_million=sum(c.rcr_per_million or 0 for c in chunk) / len(chunk),
                )
            )

    result.spearman_funding_vs_output_per_dollar = _spearman(
        [r.total_funding for r in analyzed], [r.rcr_per_million or 0.0 for r in analyzed]
    )
    result.notes.append(
        "Output is lifetime summed RCR over authoritative pub links; recent awards "
        "are right-censored. Support is total funding, a proxy for the Grant Support Index."
    )
    return result


# --------------------------------------------------------------------------- #
# Translation lineage: basic grant -> clinical work, via the citation graph
# --------------------------------------------------------------------------- #


class TranslationLineage(BaseModel):
    """How a grant's research reaches clinical work, via citations (inferred)."""

    core_project_num: str
    grant_fiscal_year: int | None = None
    publications: int = 0
    pubs_with_clinical_citation: int = 0
    clinical_citation_reach: int = 0  # distinct clinical papers citing the grant's work
    mean_apt: float | None = None  # mean Approximate Potential to Translate of the grant's pubs
    years_to_first_clinical_citation: int | None = None  # translation latency
    example_clinical_citers: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def _utcnow_year() -> int:
    return datetime.now(UTC).year


def translation_lineage(
    core_project_num: str,
    grant_fiscal_year: int | None = None,
    pub_limit: int = 300,
    use_cache: bool = True,
) -> TranslationLineage:
    """Trace a grant's basic→clinical reach through the citation graph.

    award → its publications (RePORTER links) → the *clinical* articles that cite
    them (iCite ``cited_by_clin``, precomputed) → translation latency. This is an
    INFERRED lineage (citation-mediated), not a funded award→trial link; it catches
    the basic-science grant that *enabled* a therapy it never directly funded.
    """
    result = TranslationLineage(
        core_project_num=core_project_num, grant_fiscal_year=grant_fiscal_year
    )
    if grant_fiscal_year is None:
        project = reporter.get_project(core_project_num, use_cache=use_cache)
        result.grant_fiscal_year = project.fiscal_year if project else None

    links = reporter.get_publications(core_project_num, limit=pub_limit, use_cache=use_cache)
    pmids = [link.pmid for link in links if link.pmid]
    result.publications = len(pmids)
    if not pmids:
        result.notes.append("No authoritative publication links for this grant.")
        return result

    recs = icite.fetch_translation(pmids, use_cache=use_cache)
    apts = [r.apt for r in recs if r.apt is not None]
    result.mean_apt = round(sum(apts) / len(apts), 3) if apts else None

    clinical_citers: set[str] = set()
    for r in recs:
        if r.clinical_citers:
            result.pubs_with_clinical_citation += 1
            clinical_citers.update(r.clinical_citers)
    result.clinical_citation_reach = len(clinical_citers)
    result.example_clinical_citers = sorted(clinical_citers)[:8]

    # Translation latency: years from the grant to the earliest clinical citation.
    if clinical_citers and result.grant_fiscal_year:
        citer_recs = icite.fetch_translation(sorted(clinical_citers), use_cache=use_cache)
        years = [c.year for c in citer_recs if c.year]
        if years:
            delta = min(years) - result.grant_fiscal_year
            if delta >= 0:
                result.years_to_first_clinical_citation = delta
            else:
                # The reported FY is a later support year than the cited work, so
                # latency is meaningless here — don't clamp it to a false 0.
                result.notes.append(
                    "Latency not computed: the grant's reported fiscal year is a later "
                    "support year than its cited work. Pass the grant start year for latency."
                )

    result.notes.append(
        "Inferred (citation-mediated), not a funded award→trial link. Clinical "
        "citers are iCite's cited_by_clin (precomputed). Latency is right-censored "
        "for recent grants. Publication linkage is authoritative-only (a floor)."
    )
    return result


class TranslationScanRow(BaseModel):
    core_project_num: str
    title: str | None = None
    start_fiscal_year: int | None = None
    publications: int = 0
    clinical_citation_reach: int = 0
    mean_apt: float | None = None
    years_to_first_clinical_citation: int | None = None


def scan_translation(
    query: str | None = None,
    institutes: list[str] | None = None,
    fiscal_years: list[int] | None = None,
    max_grants: int = 30,
    use_cache: bool = True,
) -> list[TranslationScanRow]:
    """Rank a portfolio's grants by basic→clinical citation reach.

    Dedups award-years to grants (keeping the earliest = start year, so latency is
    computed correctly), then runs the translation lineage on each and sorts by
    distinct clinical papers reached. Identifies the discovery→translation cases.
    """
    projects = reporter.search_projects(
        query=query,
        institutes=institutes,
        fiscal_years=fiscal_years,
        limit=max_grants * 4,
        use_cache=use_cache,
    )
    grants: dict[str, dict] = {}
    for p in projects:
        core = p.core_project_num or p.project_number
        if not core:
            continue
        g = grants.setdefault(core, {"title": p.project_title, "fy": p.fiscal_year})
        if p.fiscal_year is not None and (g["fy"] is None or p.fiscal_year < g["fy"]):
            g["fy"] = p.fiscal_year  # earliest = start year

    rows: list[TranslationScanRow] = []
    for core, meta in list(grants.items())[:max_grants]:
        lin = translation_lineage(core, grant_fiscal_year=meta["fy"], use_cache=use_cache)
        rows.append(
            TranslationScanRow(
                core_project_num=core,
                title=meta["title"],
                start_fiscal_year=meta["fy"],
                publications=lin.publications,
                clinical_citation_reach=lin.clinical_citation_reach,
                mean_apt=lin.mean_apt,
                years_to_first_clinical_citation=lin.years_to_first_clinical_citation,
            )
        )
    rows.sort(key=lambda r: (r.clinical_citation_reach, r.mean_apt or 0), reverse=True)
    return rows


# --------------------------------------------------------------------------- #
# Portfolio redundancy: topically near-duplicate grants
# --------------------------------------------------------------------------- #


class GrantPair(BaseModel):
    core_a: str
    core_b: str
    title_a: str | None = None
    title_b: str | None = None
    jaccard: float
    shared_terms: list[str] = Field(default_factory=list)
    same_pi: bool = False


class RedundancyResult(BaseModel):
    criteria: dict = Field(default_factory=dict)
    grants_analyzed: int = 0
    pairs: list[GrantPair] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


def detect_portfolio_redundancy(
    query: str | None = None,
    institutes: list[str] | None = None,
    fiscal_years: list[int] | None = None,
    max_grants: int = 80,
    min_jaccard: float = 0.4,
    min_terms: int = 8,
    use_cache: bool = True,
) -> RedundancyResult:
    """Surface topically near-duplicate grant pairs in a portfolio (term overlap).

    Similarity is Jaccard overlap of RePORTER project terms. High overlap is NOT
    proof of waste — it may be replication, complementary work, or one team's
    program. Same-PI pairs (a continuing program) are flagged separately; the
    *cross-PI* high-overlap pairs are the ones worth a human look.
    """
    result = RedundancyResult(
        criteria={"query": query, "institutes": institutes, "fiscal_years": fiscal_years}
    )
    projects = reporter.search_projects(
        query=query,
        institutes=institutes,
        fiscal_years=fiscal_years,
        limit=max_grants * 4,
        use_cache=use_cache,
    )

    # Dedup to grants; keep the record with the richest term set.
    grants: dict[str, dict] = {}
    for p in projects:
        core = p.core_project_num or p.project_number
        if not core:
            continue
        terms = {t.lower() for t in p.terms}
        pis = {pi.profile_id for pi in p.principal_investigators if pi.profile_id}
        g = grants.get(core)
        if g is None or len(terms) > len(g["terms"]):
            grants[core] = {"terms": terms, "title": p.project_title, "pis": pis}
        elif g is not None:
            g["pis"] |= pis

    items = [(c, g) for c, g in grants.items() if len(g["terms"]) >= min_terms][:max_grants]
    result.grants_analyzed = len(items)

    pairs: list[GrantPair] = []
    for i in range(len(items)):
        ca, ga = items[i]
        for j in range(i + 1, len(items)):
            cb, gb = items[j]
            inter = ga["terms"] & gb["terms"]
            union = ga["terms"] | gb["terms"]
            if not union:
                continue
            jac = len(inter) / len(union)
            if jac >= min_jaccard:
                pairs.append(
                    GrantPair(
                        core_a=ca,
                        core_b=cb,
                        title_a=ga["title"],
                        title_b=gb["title"],
                        jaccard=round(jac, 3),
                        shared_terms=sorted(inter)[:12],
                        same_pi=bool(ga["pis"] & gb["pis"]),
                    )
                )
    pairs.sort(key=lambda p: p.jaccard, reverse=True)
    result.pairs = pairs[:25]

    cross = sum(1 for p in pairs if not p.same_pi)
    result.notes.append(
        f"{len(pairs)} grant pairs above Jaccard {min_jaccard} ({cross} cross-PI). "
        "High term overlap ≠ waste — may be replication or complementary work. "
        "Terms are RePORTER's auto-tagged project terms (noisy)."
    )
    return result


# --------------------------------------------------------------------------- #
# Open-science signals + meta-research brief
# --------------------------------------------------------------------------- #


class OpenScienceSignals(BaseModel):
    core_project_num: str
    publications: int = 0
    pmc_fraction: float | None = None  # share of pubs available in PubMed Central
    dataset_accessions: list[str] = Field(default_factory=list)  # mined from the abstract
    has_registered_trial: bool = False
    notes: list[str] = Field(default_factory=list)


def assess_open_science_signals(
    core_project_num: str, pub_limit: int = 200, use_cache: bool = True
) -> OpenScienceSignals:
    """Open-science signals for a grant: PMC availability, datasets, trial registration.

    Composes signals from data we already have — a publication's PMC id (open
    access), dataset accessions mined from the award abstract, and whether the
    grant has a registered clinical trial. All are partial proxies (abstract-only
    mining undercounts datasets; PMC ≠ fully open), so this is a floor.
    """
    sig = OpenScienceSignals(core_project_num=core_project_num)
    links = reporter.get_publications(core_project_num, limit=pub_limit, use_cache=use_cache)
    pmids = [link.pmid for link in links if link.pmid]
    sig.publications = len(pmids)
    if pmids:
        articles = pubmed.fetch_summaries(pmids, use_cache=use_cache)
        if articles:
            with_pmc = sum(1 for a in articles if a.pmcid)
            sig.pmc_fraction = round(with_pmc / len(articles), 3)

    project = reporter.get_project(core_project_num, use_cache=use_cache)
    if project and project.abstract_text:
        matches = accession.extract_accessions(project.abstract_text)
        sig.dataset_accessions = [f"{m.repository}:{m.accession}" for m in matches]

    sig.has_registered_trial = bool(
        clinicaltrials.find_trials_for_grant(core_project_num, use_cache=use_cache)
    )
    sig.notes.append(
        "Partial proxies: dataset mining is abstract-only (undercounts), PMC "
        "availability ≠ full open access, trial registration applies only to trials."
    )
    return sig


class MetaResearchBrief(BaseModel):
    topic: str
    years: list[int] = Field(default_factory=list)
    distinct_grants: int = 0
    total_funding: float = 0.0
    top_pis: list[str] = Field(default_factory=list)
    redundant_pairs_cross_pi: int = 0
    open_science_pmc_fraction: float | None = None
    open_science_sampled: int = 0
    grants_with_trial: int = 0
    top_translation: list[dict] = Field(default_factory=list)  # core, title, clinical reach
    caveats: list[str] = Field(default_factory=list)
    generated_at: str | None = None


def build_meta_research_brief(
    topic: str,
    years: list[int] | None = None,
    sample_grants: int = 8,
    use_cache: bool = True,
) -> MetaResearchBrief:
    """Synthesize the meta-research dimensions for a topic into one brief.

    Composition + redundancy (topic overlap) + open-science signals (sampled) +
    translation reach (sampled). A research-on-research counterpart to the
    portfolio brief; every number ships with its caveat.
    """
    years = years or list(range(2014, 2024))
    brief = MetaResearchBrief(
        topic=topic,
        years=years,
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
    )

    projects = reporter.search_projects(
        query=topic, fiscal_years=years, limit=1500, use_cache=use_cache
    )
    cores: dict[str, reporter.ReporterProject] = {}
    pi_count: dict[str, int] = defaultdict(int)
    funding_by_core: dict[str, float] = defaultdict(float)
    for p in projects:
        core = p.core_project_num or p.project_number
        if not core:
            continue
        funding_by_core[core] += p.total_cost or 0.0
        if core not in cores:
            cores[core] = p
            for pi in p.principal_investigators:
                if pi.full_name:
                    pi_count[pi.full_name] += 1
    brief.distinct_grants = len(cores)
    brief.total_funding = sum(funding_by_core.values())
    brief.top_pis = [n for n, _ in sorted(pi_count.items(), key=lambda kv: -kv[1])[:5]]

    red = detect_portfolio_redundancy(query=topic, fiscal_years=years, use_cache=use_cache)
    brief.redundant_pairs_cross_pi = sum(1 for p in red.pairs if not p.same_pi)

    # Sample the highest-funded grants for the expensive signals.
    sample = sorted(cores, key=lambda c: funding_by_core[c], reverse=True)[:sample_grants]
    pmc_fracs: list[float] = []
    for core in sample:
        sig = assess_open_science_signals(core, use_cache=use_cache)
        if sig.pmc_fraction is not None:
            pmc_fracs.append(sig.pmc_fraction)
        if sig.has_registered_trial:
            brief.grants_with_trial += 1
    brief.open_science_sampled = len(sample)
    if pmc_fracs:
        brief.open_science_pmc_fraction = round(sum(pmc_fracs) / len(pmc_fracs), 3)

    scan = scan_translation(
        query=topic, fiscal_years=years, max_grants=sample_grants, use_cache=use_cache
    )
    brief.top_translation = [
        {
            "core": r.core_project_num,
            "title": (r.title or "")[:60],
            "reach": r.clinical_citation_reach,
        }
        for r in scan[:5]
    ]

    brief.caveats = [
        "Composition over a capped search; redundancy from noisy auto-tagged terms.",
        "Open-science + translation are sampled from the top-funded grants (disclosed).",
        "All linkage is authoritative-only (a floor); translation reach is inferred.",
    ]
    return brief
