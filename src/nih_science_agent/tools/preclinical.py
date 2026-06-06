"""Preclinical / NAMs portfolio mapping (ORIVA / DNICEATM-facing).

Maps the NIH preclinical portfolio by **New Approach Methodology (NAM)** in the
three design-doc areas — immunology, AD/ADRD, cardiometabolic. Which mechanisms
and areas are producing organ-on-chip, microphysiological systems, iPSC-derived
models, organoids, in-silico tox, and validated alternative assays?

This is a portfolio map, not a validation ledger: it classifies a grant by NAM
terms appearing in its RePORTER terms/title/abstract. Method→validation→
regulatory-acceptance edges (OECD/ICCVAM/FDA) are a further step. Coverage is
reported, and "no NAM term found" means *unknown*, not *no NAM used*.
"""

from __future__ import annotations

from collections import defaultdict

from pydantic import BaseModel, Field

from nih_science_agent.connectors import reporter

# Preclinical areas → RePORTER text-search anchors.
AREAS = {
    "immunology": "immunology",
    "ad_adrd": "Alzheimer",
    "cardiometabolic": "cardiometabolic",
}

# NAM method categories → match terms (lower-cased substring match).
NAM_METHODS: dict[str, list[str]] = {
    "organ_on_chip": [
        "organ-on-chip",
        "organ on a chip",
        "microphysiological",
        "tissue chip",
        "organ-on-a-chip",
    ],
    "ipsc": ["induced pluripotent", "ipsc", "ips cell", "ips-derived"],
    "organoid": ["organoid", "cerebral organoid", "spheroid"],
    "in_silico": ["in silico", "computational toxicolog", "qsar", "in-silico"],
    "hts_in_vitro": [
        "high-throughput screen",
        "high throughput screen",
        "in vitro screen",
        "tox21",
        "toxcast",
    ],
    "skin_sensitization": ["skin sensitization", "dpra", "keratinosens", "h-clat", "llna"],
    "3d_culture": ["3d culture", "three-dimensional culture", "bioprint"],
}


class PreclinicalAssets(BaseModel):
    area: str
    grants_scanned: int = 0
    grants_with_nam: int = 0
    by_method: dict[str, int] = Field(default_factory=dict)
    examples: dict[str, list[str]] = Field(default_factory=dict)  # method -> example cores
    coverage_note: str | None = None


def _classify(text: str) -> list[str]:
    t = text.lower()
    return [m for m, terms in NAM_METHODS.items() if any(term in t for term in terms)]


def find_preclinical_assets(
    area: str,
    years: list[int] | None = None,
    max_records: int = 1500,
    use_cache: bool = True,
) -> PreclinicalAssets:
    """Map NAM usage across an NIH preclinical area's grants.

    Searches the area, then classifies each grant by NAM method terms found in its
    RePORTER terms/title/abstract. Returns counts and examples per method.
    """
    anchor = AREAS.get(area, area)
    result = PreclinicalAssets(area=area)

    projects = reporter.search_projects(
        query=anchor, fiscal_years=years, limit=max_records, use_cache=use_cache
    )
    by_method: dict[str, int] = defaultdict(int)
    examples: dict[str, list[str]] = defaultdict(list)
    seen: set[str] = set()
    grants_with_nam: set[str] = set()

    for p in projects:
        core = p.core_project_num or p.project_number
        if not core or core in seen:
            continue
        seen.add(core)
        text = " ".join(filter(None, [p.project_title, p.abstract_text, " ".join(p.terms)]))
        methods = _classify(text)
        if methods:
            grants_with_nam.add(core)
        for m in methods:
            by_method[m] += 1
            if len(examples[m]) < 5:
                examples[m].append(core)

    result.grants_scanned = len(seen)
    result.grants_with_nam = len(grants_with_nam)
    result.by_method = dict(sorted(by_method.items(), key=lambda kv: -kv[1]))
    result.examples = dict(examples)
    nam_pct = (len(grants_with_nam) / len(seen)) if seen else 0.0
    result.coverage_note = (
        f"{len(grants_with_nam)}/{len(seen)} grants ({nam_pct:.0%}) mention a NAM term. "
        "Absence of a term means unknown, not 'no NAM used'; matching is on "
        "RePORTER terms/title/abstract (noisy)."
    )
    return result


class NamsPortfolioMap(BaseModel):
    years: list[int] = Field(default_factory=list)
    areas: list[PreclinicalAssets] = Field(default_factory=list)


def nams_portfolio_map(
    areas: list[str] | None = None,
    years: list[int] | None = None,
    use_cache: bool = True,
) -> NamsPortfolioMap:
    """NAM portfolio map across the preclinical areas (immunology, AD/ADRD, cardiometabolic)."""
    areas = areas or list(AREAS)
    return NamsPortfolioMap(
        years=years or [],
        areas=[find_preclinical_assets(a, years=years, use_cache=use_cache) for a in areas],
    )
