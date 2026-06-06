"""Hand-curated benchmark topics for validating the platform end to end.

Per the design doc, each benchmark asserts the **expected kinds of results**, not
brittle exact IDs: a floor on award count, which ICs *should* appear, whether the
topic maps to a population-health condition, and whether trials / approved drugs
*should* exist. A runner builds the portfolio brief for each and checks these
loose expectations, so the suite catches regressions without breaking when NIH
data shifts.

The set is curated to be timely (2024–26), broad enough to return real
portfolios, and to span the platform's range — environmental-health / data-science
portfolios near the maintainer's research, plus translational "hero" topics that
stress the trials / FDA / outcome layers.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from nih_science_agent.tools import briefs as briefs_tool


class Expect(BaseModel):
    min_awards: int = 0
    ics_any: list[str] = Field(default_factory=list)  # ≥1 should appear in top ICs
    condition_key: str | None = None  # population-health condition the outcome maps to
    expect_trials: bool = False
    expect_drugs: bool = False


class Benchmark(BaseModel):
    key: str
    label: str
    query: str
    years: list[int] | None = None
    condition: str | None = None  # explicit outcome condition (overrides keywords)
    expect: Expect
    rationale: str


class Check(BaseModel):
    name: str
    passed: bool
    detail: str


class BenchmarkResult(BaseModel):
    key: str
    label: str
    passed: bool
    distinct_awards: int = 0
    checks: list[Check] = Field(default_factory=list)


_RECENT = list(range(2018, 2025))

BENCHMARKS: list[Benchmark] = [
    # -- environmental health / data science (near the maintainer's research) -- #
    Benchmark(
        key="exposome",
        label="Exposome & chronic disease",
        query="exposome",
        years=_RECENT,
        expect=Expect(min_awards=40, ics_any=["NIEHS"]),
        rationale="Broad environmental-exposure portfolio; NIEHS-led; no single condition.",
    ),
    Benchmark(
        key="pfas_cardiometabolic",
        label="PFAS & cardiometabolic disease",
        query="PFAS",
        years=_RECENT,
        expect=Expect(min_awards=30, ics_any=["NIEHS"]),
        rationale="Timely environmental contaminant; NIEHS; cardiometabolic outcomes.",
    ),
    Benchmark(
        key="climate_health",
        label="Climate change & human health",
        query="climate change health",
        years=_RECENT,
        expect=Expect(min_awards=20, ics_any=["NIEHS", "FIC"]),
        rationale="Fast-growing NIH priority; weak mortality crosswalk (coverage stress test).",
    ),
    Benchmark(
        key="ai_biomedical",
        label="AI / ML for biomedical discovery",
        query="machine learning",
        years=_RECENT,
        expect=Expect(min_awards=200, ics_any=["NLM", "NIGMS", "NIBIB"]),
        rationale="Large cross-IC methods portfolio; tests breadth + productivity tools.",
    ),
    # -- timely translational hero topics (stress trials / FDA / outcome) ----- #
    Benchmark(
        key="glp1ra",
        label="GLP-1 receptor agonists",
        query="GLP-1",
        years=_RECENT,
        condition="diabetes",
        expect=Expect(
            min_awards=30,
            ics_any=["NIDDK", "NHLBI"],
            condition_key="diabetes",
            expect_trials=True,
            expect_drugs=True,
        ),
        rationale="Full arc: funding → trials → FDA approvals → diabetes mortality pulse.",
    ),
    Benchmark(
        key="alzheimers_dmt",
        label="Alzheimer's disease-modifying therapies",
        query="Alzheimer",
        years=_RECENT,
        condition="alzheimers",
        expect=Expect(
            min_awards=80,
            ics_any=["NIA"],
            condition_key="alzheimers",
            expect_trials=True,
            expect_drugs=True,
        ),
        rationale="Timely approvals (lecanemab/donanemab); strong trials + FDA + mortality.",
    ),
    Benchmark(
        key="long_covid",
        label="Long COVID (PASC)",
        query="post-acute sequelae SARS-CoV-2",
        years=list(range(2021, 2025)),
        expect=Expect(min_awards=10, ics_any=["NHLBI", "NIAID"], expect_trials=True),
        rationale="RECOVER initiative; condition NOT in mortality crosswalk (honest gap).",
    ),
    Benchmark(
        key="crispr_therapeutics",
        label="CRISPR / gene-editing therapeutics",
        query="CRISPR gene editing",
        years=_RECENT,
        expect=Expect(min_awards=50, ics_any=["NHGRI", "NHLBI", "NIAID"]),
        rationale=(
            "Timely (Casgevy). Top-funded awards are basic science with few "
            "self-reported trial links — demonstrates the award→trial recall limit."
        ),
    ),
]

_BY_KEY = {b.key: b for b in BENCHMARKS}


def list_benchmarks() -> list[Benchmark]:
    return list(BENCHMARKS)


def get_benchmark(key: str) -> Benchmark | None:
    return _BY_KEY.get(key)


def check_brief(bench: Benchmark, brief: briefs_tool.PortfolioBrief) -> BenchmarkResult:
    """Evaluate a benchmark's expectations against a built brief (no network)."""
    e = bench.expect
    checks: list[Check] = []

    checks.append(
        Check(
            name="min_awards",
            passed=brief.distinct_awards >= e.min_awards,
            detail=f"{brief.distinct_awards} awards (expected ≥ {e.min_awards})",
        )
    )
    if e.ics_any:
        top = {r.name for r in brief.top_ics}
        hit = sorted(top & set(e.ics_any))
        checks.append(
            Check(
                name="ics_any",
                passed=bool(hit),
                detail=f"top ICs {sorted(top)} ∩ expected {e.ics_any} = {hit}",
            )
        )
    if e.condition_key is not None:
        cond = briefs_tool.conditions_tool.get_condition(e.condition_key)
        expected_label = cond.label if cond else e.condition_key
        checks.append(
            Check(
                name="condition",
                passed=brief.condition_label == expected_label,
                detail=f"condition {brief.condition_label!r} (expected {expected_label!r})",
            )
        )
    if e.expect_trials:
        checks.append(
            Check(
                name="trials",
                passed=len(brief.trials) > 0,
                detail=f"{len(brief.trials)} linked trials in sample",
            )
        )
    if e.expect_drugs:
        n = brief.approved_drug_count or 0
        checks.append(
            Check(name="drugs", passed=n > 0, detail=f"{n} FDA-approved drugs"),
        )

    return BenchmarkResult(
        key=bench.key,
        label=bench.label,
        distinct_awards=brief.distinct_awards,
        passed=all(c.passed for c in checks),
        checks=checks,
    )


def run_benchmark(bench: Benchmark, use_cache: bool = True) -> BenchmarkResult:
    """Build the portfolio brief for a benchmark and check its expectations (network).

    A transient API failure for one benchmark yields a failed result rather than
    crashing a full run.
    """
    try:
        brief = briefs_tool.build_portfolio_brief(
            bench.query, years=bench.years, condition=bench.condition, use_cache=use_cache
        )
    except Exception as exc:  # noqa: BLE001 - report, don't crash the suite
        return BenchmarkResult(
            key=bench.key,
            label=bench.label,
            passed=False,
            checks=[Check(name="completed", passed=False, detail=f"error: {exc}")],
        )
    return check_brief(bench, brief)
