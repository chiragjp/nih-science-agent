"""Offline tests for the Task 5c outcomes layer: CDC, openFDA, crosswalk, pulse."""

from __future__ import annotations

import httpx

from nih_science_agent.connectors import cdc, clinicaltrials, fda, pubmed, reporter
from nih_science_agent.connectors.cdc import CDCClient
from nih_science_agent.connectors.fda import FDAClient, normalize_label
from nih_science_agent.connectors.reporter import ReporterProject
from nih_science_agent.tools import pulse as pulse_tool
from nih_science_agent.tools.conditions import (
    match_conditions,
    resolve_condition,
)

# --------------------------------------------------------------------------- #
# Condition crosswalk
# --------------------------------------------------------------------------- #


def test_resolve_condition_by_key_and_keyword() -> None:
    assert resolve_condition("diabetes").key == "diabetes"
    assert resolve_condition("a study of insulin resistance").key == "diabetes"
    assert resolve_condition("coronary artery disease").key == "heart_disease"
    assert resolve_condition("glioblastoma carcinoma").key == "cancer"
    assert resolve_condition("completely unrelated topic xyz") is None


def test_match_conditions_can_return_multiple() -> None:
    keys = {c.key for c in match_conditions("diabetes and chronic kidney disease comorbidity")}
    assert {"diabetes", "kidney_disease"} <= keys


def test_condition_handles_present() -> None:
    diabetes = resolve_condition("diabetes")
    assert diabetes.cdc_cause_name == "Diabetes"
    assert diabetes.primary_ic == "NIDDK"
    assert diabetes.places_measure


# --------------------------------------------------------------------------- #
# CDC connector
# --------------------------------------------------------------------------- #

CDC_MORTALITY_ROWS = [
    {
        "year": "1999",
        "cause_name": "Diabetes",
        "state": "United States",
        "deaths": "68399",
        "aadr": "25.0",
    },
    {
        "year": "2017",
        "cause_name": "Diabetes",
        "state": "United States",
        "deaths": "83564",
        "aadr": "21.5",
    },
]


def test_cdc_mortality_normalization(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=CDC_MORTALITY_ROWS)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = CDCClient(client=http, cache_dir=tmp_path, use_cache=False)
    series = client.leading_causes_of_death("Diabetes")
    assert [p.year for p in series] == [1999, 2017]
    assert series[0].aadr == 25.0
    assert series[1].deaths == 83564


def test_cdc_socrata_where_clause(tmp_path) -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=CDC_MORTALITY_ROWS)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = CDCClient(client=http, cache_dir=tmp_path, use_cache=False)
    client.leading_causes_of_death("Diabetes", state="California", years=[2010, 2015])
    where = dict(captured[0].url.params)["$where"]
    assert "cause_name='Diabetes'" in where
    assert "state='California'" in where
    assert "year >= '2010'" in where


def test_cdc_escapes_apostrophe_in_cause(tmp_path) -> None:
    # "Alzheimer's disease" must not break the SoQL clause (regression).
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=[])

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = CDCClient(client=http, cache_dir=tmp_path, use_cache=False)
    client.leading_causes_of_death("Alzheimer's disease")
    where = dict(captured[0].url.params)["$where"]
    assert "cause_name='Alzheimer''s disease'" in where  # apostrophe doubled


# --------------------------------------------------------------------------- #
# openFDA connector
# --------------------------------------------------------------------------- #

FDA_RESPONSE = {
    "meta": {"results": {"total": 142}},
    "results": [
        {
            "indications_and_usage": ["OZEMPIC is indicated for type 2 diabetes mellitus."],
            "openfda": {
                "brand_name": ["OZEMPIC"],
                "generic_name": ["SEMAGLUTIDE"],
                "manufacturer_name": ["Novo Nordisk"],
                "application_number": ["NDA209637"],
                "route": ["SUBCUTANEOUS"],
            },
        }
    ],
}


def test_fda_normalize_label() -> None:
    d = normalize_label(FDA_RESPONSE["results"][0])
    assert d.brand_names == ["OZEMPIC"]
    assert d.generic_names == ["SEMAGLUTIDE"]
    assert d.manufacturer == "Novo Nordisk"
    assert d.application_numbers == ["NDA209637"]
    assert "type 2 diabetes" in (d.indication_snippet or "")


def test_fda_drugs_for_indication(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=FDA_RESPONSE)

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = FDAClient(client=http, cache_dir=tmp_path, use_cache=False)
    total, drugs = client.drugs_for_indication("type 2 diabetes")
    assert total == 142
    assert drugs[0].brand_names == ["OZEMPIC"]


def test_fda_zero_results_on_404(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = FDAClient(client=http, cache_dir=tmp_path, use_cache=False)
    assert client.drugs_for_indication("nonexistent") == (0, [])


# --------------------------------------------------------------------------- #
# Pulse assembly (all connectors mocked)
# --------------------------------------------------------------------------- #


def test_condition_pulse_unknown_returns_none() -> None:
    assert pulse_tool.condition_pulse("totally unknown condition") is None


def test_condition_pulse_assembles_full_arc(monkeypatch) -> None:
    projects = [
        ReporterProject(core_project_num="R01DK1", fiscal_year=2010, total_cost=500_000),
        ReporterProject(
            core_project_num="R01DK1", fiscal_year=2011, total_cost=500_000
        ),  # same grant
        ReporterProject(core_project_num="R01DK2", fiscal_year=2011, total_cost=300_000),
    ]

    def fake_search(**kw):
        yrs = kw.get("fiscal_years") or []
        return [p for p in projects if p.fiscal_year in yrs] if yrs else projects

    monkeypatch.setattr(reporter, "search_projects", fake_search)
    monkeypatch.setattr(pubmed, "count", lambda q, use_cache=True: 12345)
    monkeypatch.setattr(
        clinicaltrials,
        "count_trials",
        lambda condition=None, has_results=None, use_cache=True: 50 if has_results else 800,
    )
    monkeypatch.setattr(
        fda,
        "drugs_for_indication",
        lambda ind, limit=10, use_cache=True: (
            142,
            [
                fda.DrugApproval(brand_names=["OZEMPIC"]),
                fda.DrugApproval(brand_names=["JARDIANCE"]),
            ],
        ),
    )
    monkeypatch.setattr(
        cdc,
        "leading_causes_of_death",
        lambda cause, state="United States", use_cache=True: [
            cdc.MortalityPoint(year=1999, aadr=25.0),
            cdc.MortalityPoint(year=2017, aadr=21.5),
        ],
    )
    monkeypatch.setattr(
        cdc,
        "places_prevalence",
        lambda measure, use_cache=True: [
            cdc.PrevalencePoint(value=10.0),
            cdc.PrevalencePoint(value=12.0),
        ],
    )

    p = pulse_tool.condition_pulse("diabetes", years=[2010, 2011])
    assert p is not None
    assert p.condition_key == "diabetes"
    # funding grouped by year, grants deduped
    assert p.distinct_awards == 2
    assert p.total_funding == 1_300_000
    assert {fy.fiscal_year for fy in p.funding_by_year} == {2010, 2011}
    # outputs
    assert p.publication_count == 12345
    assert p.trial_count == 800
    assert p.trials_with_results == 50
    # translation
    assert p.approved_drug_count == 142
    assert p.example_drugs == ["OZEMPIC", "JARDIANCE"]
    # outcome: AADR fell 25.0 -> 21.5 => -14%
    assert p.mortality_aadr_change_pct is not None and p.mortality_aadr_change_pct < 0
    assert round(p.mortality_aadr_change_pct, 0) == -14
    assert p.prevalence_value == 11.0  # mean of 10 and 12
    assert "NOT causal" in p.caveat
