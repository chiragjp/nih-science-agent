"""Offline tests for the curated benchmark definitions and check logic."""

from __future__ import annotations

from nih_science_agent.tools import benchmarks as bm
from nih_science_agent.tools.briefs import PortfolioBrief, Ranked


def test_benchmarks_well_formed() -> None:
    keys = [b.key for b in bm.list_benchmarks()]
    assert len(keys) == len(set(keys)) == 8  # unique, the curated set
    for b in bm.list_benchmarks():
        assert b.query and b.label and b.rationale
        assert b.expect.min_awards >= 0
        # an explicit outcome condition must resolve in the crosswalk
        if b.condition:
            assert bm.briefs_tool.conditions_tool.get_condition(b.condition) is not None


def _brief(**kw) -> PortfolioBrief:
    base = dict(
        topic="t", audience="NIH OD", distinct_awards=100, top_ics=[Ranked(name="NIDDK", count=50)]
    )
    base.update(kw)
    return PortfolioBrief(**base)


def test_check_passes_when_expectations_met() -> None:
    bench = bm.get_benchmark("glp1ra")
    brief = _brief(
        distinct_awards=120,
        top_ics=[Ranked(name="NIDDK", count=60), Ranked(name="NHLBI", count=20)],
        condition_label="Diabetes",
        trials=["NCT1 — a trial"],
        approved_drug_count=42,
    )
    res = bm.check_brief(bench, brief)
    assert res.passed
    assert {c.name for c in res.checks} == {"min_awards", "ics_any", "condition", "trials", "drugs"}


def test_check_fails_on_low_awards_and_missing_ic() -> None:
    bench = bm.get_benchmark("ai_biomedical")  # expects ≥200 awards, NLM/NIGMS/NIBIB
    brief = _brief(distinct_awards=5, top_ics=[Ranked(name="NCI", count=5)])
    res = bm.check_brief(bench, brief)
    assert not res.passed
    by = {c.name: c.passed for c in res.checks}
    assert by["min_awards"] is False
    assert by["ics_any"] is False


def test_check_condition_mismatch_fails() -> None:
    bench = bm.get_benchmark("alzheimers_dmt")
    brief = _brief(
        distinct_awards=100,
        top_ics=[Ranked(name="NIA", count=80)],
        condition_label="Diabetes",  # wrong condition
        trials=["NCT1 — x"],
        approved_drug_count=3,
    )
    res = bm.check_brief(bench, brief)
    assert not res.passed
    assert {c.name: c.passed for c in res.checks}["condition"] is False


def test_run_benchmark_uses_brief(monkeypatch) -> None:
    bench = bm.get_benchmark("exposome")
    captured = {}

    def fake_brief(topic, years=None, condition=None, use_cache=True):
        captured["topic"] = topic
        return _brief(distinct_awards=60, top_ics=[Ranked(name="NIEHS", count=40)])

    monkeypatch.setattr(bm.briefs_tool, "build_portfolio_brief", fake_brief)
    res = bm.run_benchmark(bench)
    assert captured["topic"] == "exposome"
    assert res.passed  # 60 ≥ 40 and NIEHS present
