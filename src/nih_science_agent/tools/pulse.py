"""Condition pulse — juxtapose NIH inputs against the nation's health outcome.

For one canonical condition, assembles the full arc on a single page:
  Input/Process : NIH funding by year, award count (RePORTER)
  Output        : publications (PubMed), clinical trials (ClinicalTrials.gov)
  Translation   : FDA-approved drugs for the condition (openFDA)
  Outcome       : age-adjusted mortality trend, local prevalence (CDC)

CRITICAL: this is a *juxtaposition*, not an attribution. Population mortality
reflects decades of lag, countless non-NIH drivers, and reactive funding
(dollars often rise as a problem worsens). The report therefore carries explicit
caveats and never computes a "return per death averted." See the design doc's
anti-gameable-metric stance.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from nih_science_agent.connectors import cdc, clinicaltrials, fda, pubmed, reporter
from nih_science_agent.tools.conditions import Condition, resolve_condition

CAVEAT = (
    "Juxtaposition only — NOT causal attribution. Population mortality reflects "
    "10-30y research lag and many non-NIH drivers; funding often rises as a "
    "problem worsens. Do not infer NIH impact per death from these series."
)


class FundingYear(BaseModel):
    fiscal_year: int
    total_funding: float
    award_count: int


class ConditionPulse(BaseModel):
    condition_key: str
    condition_label: str
    years: list[int] = Field(default_factory=list)
    # Input
    funding_by_year: list[FundingYear] = Field(default_factory=list)
    total_funding: float = 0.0
    distinct_awards: int = 0
    funding_is_floor: bool = False  # True if any year hit the per-year record cap
    # Output
    publication_count: int = 0
    trial_count: int = 0
    trials_with_results: int = 0
    # Translation
    approved_drug_count: int = 0
    example_drugs: list[str] = Field(default_factory=list)
    # Outcome
    mortality: list[cdc.MortalityPoint] = Field(default_factory=list)
    mortality_aadr_change_pct: float | None = None
    prevalence_value: float | None = None
    prevalence_measure: str | None = None
    caveat: str = CAVEAT


def _funding_query(condition: Condition) -> str:
    # Primary label plus a couple of strong synonyms keeps the RePORTER text
    # search focused without drifting into adjacent topics.
    return condition.keywords[0]


def condition_pulse(
    condition: str,
    years: list[int] | None = None,
    state: str = "United States",
    per_year_cap: int = 1500,
    use_cache: bool = True,
) -> ConditionPulse | None:
    """Build a :class:`ConditionPulse` for a condition key or free-text name."""
    cond = resolve_condition(condition)
    if cond is None:
        return None

    years = years or list(range(2008, 2018))
    pulse = ConditionPulse(condition_key=cond.key, condition_label=cond.label, years=years)

    # -- Input: NIH funding by fiscal year ------------------------------- #
    # Query per fiscal year: a single multi-year search would sort newest-first
    # and exhaust the record cap inside the latest year, hiding the trend. Each
    # year is independently capped; a hit means that year's funding is a floor.
    all_cores: set[str] = set()
    for y in years:
        projects = reporter.search_projects(
            query=_funding_query(cond),
            fiscal_years=[y],
            limit=per_year_cap,
            use_cache=use_cache,
        )
        if len(projects) >= per_year_cap:
            pulse.funding_is_floor = True
        year_funding = 0.0
        year_cores: set[str] = set()
        for p in projects:
            year_funding += p.total_cost or 0.0
            core = p.core_project_num or p.project_number
            if core:
                year_cores.add(core)
                all_cores.add(core)
        pulse.funding_by_year.append(
            FundingYear(fiscal_year=y, total_funding=year_funding, award_count=len(year_cores))
        )
        pulse.total_funding += year_funding
    pulse.distinct_awards = len(all_cores)

    # -- Output: publications + trials ----------------------------------- #
    pulse.publication_count = pubmed.count(cond.label, use_cache=use_cache)
    pulse.trial_count = clinicaltrials.count_trials(condition=cond.label, use_cache=use_cache)
    pulse.trials_with_results = clinicaltrials.count_trials(
        condition=cond.label, has_results=True, use_cache=use_cache
    )

    # -- Translation: FDA approvals -------------------------------------- #
    if cond.fda_indication:
        total, drugs = fda.drugs_for_indication(cond.fda_indication, limit=10, use_cache=use_cache)
        pulse.approved_drug_count = total
        seen: list[str] = []
        for d in drugs:
            for name in d.brand_names:
                if name and name not in seen:
                    seen.append(name)
        pulse.example_drugs = seen[:8]

    # -- Outcome: mortality + prevalence --------------------------------- #
    if cond.cdc_cause_name:
        series = cdc.leading_causes_of_death(cond.cdc_cause_name, state=state, use_cache=use_cache)
        pulse.mortality = series
        rated = [pt for pt in series if pt.aadr is not None]
        if len(rated) >= 2:
            first, last = rated[0].aadr, rated[-1].aadr
            if first:
                pulse.mortality_aadr_change_pct = (last - first) / first * 100.0

    if cond.places_measure:
        rows = cdc.places_prevalence(cond.places_measure, use_cache=use_cache)
        vals = [r.value for r in rows if r.value is not None]
        if vals:
            # Unweighted mean of local estimates — an approximate national signal,
            # NOT a population-weighted prevalence.
            pulse.prevalence_value = round(sum(vals) / len(vals), 1)
            pulse.prevalence_measure = cond.places_measure

    return pulse
