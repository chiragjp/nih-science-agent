"""Offline tests for the ClinicalTrials.gov connector (no network access)."""

from __future__ import annotations

import httpx

from nih_science_agent.connectors import clinicaltrials
from nih_science_agent.connectors.clinicaltrials import (
    ClinicalTrialsClient,
    Trial,
    normalize_trial,
)

# A trimmed v2 study object (single-study endpoint shape).
RAW_STUDY = {
    "protocolSection": {
        "identificationModule": {
            "nctId": "NCT04280705",
            "briefTitle": "Adaptive COVID-19 Treatment Trial (ACTT)",
            "officialTitle": "A Multicenter, Adaptive, Randomized Blinded Controlled Trial",
        },
        "statusModule": {
            "overallStatus": "COMPLETED",
            "startDateStruct": {"date": "2020-02-21", "type": "ACTUAL"},
            "completionDateStruct": {"date": "2020-05-21", "type": "ACTUAL"},
        },
        "sponsorCollaboratorsModule": {
            "leadSponsor": {
                "name": "National Institute of Allergy and Infectious Diseases (NIAID)"
            },
            "collaborators": [{"name": "Gilead Sciences"}],
        },
        "designModule": {"phases": ["PHASE3"], "enrollmentInfo": {"count": 1062}},
        "conditionsModule": {"conditions": ["COVID-19", "SARS-CoV-2 Infection"]},
        "armsInterventionsModule": {
            "interventions": [
                {"type": "DRUG", "name": "Remdesivir"},
                {"type": "OTHER", "name": "Placebo"},
            ]
        },
        "referencesModule": {
            "references": [
                {"pmid": "38657001", "type": "DERIVED", "citation": "Singh K, et al."},
                {"type": "BACKGROUND", "citation": "No PMID here"},
            ]
        },
    },
    "hasResults": True,
}

SEARCH_RESPONSE = {
    "totalCount": 1,
    "studies": [RAW_STUDY],
    "nextPageToken": None,
}


def _mock_client(payload: dict, captured: list[httpx.Request] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- #
# Normalization
# --------------------------------------------------------------------------- #


def test_normalize_full_trial() -> None:
    t = normalize_trial(RAW_STUDY)
    assert isinstance(t, Trial)
    assert t.nct_id == "NCT04280705"
    assert t.brief_title.startswith("Adaptive COVID-19")
    assert t.conditions == ["COVID-19", "SARS-CoV-2 Infection"]
    assert t.interventions == ["DRUG: Remdesivir", "OTHER: Placebo"]
    assert t.sponsors == ["National Institute of Allergy and Infectious Diseases (NIAID)"]
    assert t.collaborators == ["Gilead Sciences"]
    assert t.phase == "PHASE3"
    assert t.enrollment == 1062
    assert t.start_date == "2020-02-21"
    assert t.completion_date == "2020-05-21"
    assert t.overall_status == "COMPLETED"
    assert t.has_results is True
    assert t.source_url == "https://clinicaltrials.gov/study/NCT04280705"
    assert t.retrieved_at is not None


def test_normalize_references_keep_pmids() -> None:
    t = normalize_trial(RAW_STUDY)
    assert len(t.references) == 2
    assert t.references[0].pmid == "38657001"
    assert t.references[0].type == "DERIVED"
    assert t.references[1].pmid is None  # background ref without a PMID


def test_normalize_multi_phase_joins() -> None:
    raw = {"protocolSection": {"designModule": {"phases": ["PHASE1", "PHASE2"]}}}
    assert normalize_trial(raw).phase == "PHASE1/PHASE2"


def test_normalize_empty_and_partial() -> None:
    t = normalize_trial({})
    assert t.nct_id is None
    assert t.conditions == []
    assert t.interventions == []
    assert t.phase is None
    assert t.has_results is None
    assert t.retrieved_at is not None


def test_normalize_falls_back_to_primary_completion() -> None:
    raw = {
        "protocolSection": {"statusModule": {"primaryCompletionDateStruct": {"date": "2021-09"}}}
    }
    assert normalize_trial(raw).completion_date == "2021-09"


# --------------------------------------------------------------------------- #
# Search request construction
# --------------------------------------------------------------------------- #


def test_search_builds_query_params(tmp_path) -> None:
    captured: list[httpx.Request] = []
    http = _mock_client(SEARCH_RESPONSE, captured)
    client = ClinicalTrialsClient(client=http, cache_dir=tmp_path, use_cache=False)
    trials = client.search_trials(
        condition="COVID-19", intervention="remdesivir", sponsor="NIAID", has_results=True, limit=5
    )
    assert len(trials) == 1
    q = dict(captured[0].url.params)
    assert q["query.cond"] == "COVID-19"
    assert q["query.intr"] == "remdesivir"
    assert q["query.spons"] == "NIAID"
    assert q["aggFilters"] == "results:with"
    assert q["pageSize"] == "5"


def test_search_has_results_false_filter(tmp_path) -> None:
    captured: list[httpx.Request] = []
    http = _mock_client(SEARCH_RESPONSE, captured)
    client = ClinicalTrialsClient(client=http, cache_dir=tmp_path, use_cache=False)
    client.search_trials(query="asthma", has_results=False, limit=1)
    assert dict(captured[0].url.params)["aggFilters"] == "results:without"


def test_search_no_filter_omits_aggfilters(tmp_path) -> None:
    captured: list[httpx.Request] = []
    http = _mock_client(SEARCH_RESPONSE, captured)
    client = ClinicalTrialsClient(client=http, cache_dir=tmp_path, use_cache=False)
    client.search_trials(query="asthma", limit=1)
    assert "aggFilters" not in dict(captured[0].url.params)


def test_get_trial(tmp_path) -> None:
    http = _mock_client(RAW_STUDY)
    client = ClinicalTrialsClient(client=http, cache_dir=tmp_path, use_cache=False)
    t = client.get_trial("NCT04280705")
    assert t is not None
    assert t.enrollment == 1062


def test_get_trial_not_found_returns_none(tmp_path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "not found"})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = ClinicalTrialsClient(client=http, cache_dir=tmp_path, use_cache=False)
    assert client.get_trial("NCT00000000") is None


def test_module_level_wrapper(monkeypatch, tmp_path) -> None:
    http = _mock_client(SEARCH_RESPONSE)
    monkeypatch.setattr(
        clinicaltrials,
        "ClinicalTrialsClient",
        lambda **kw: ClinicalTrialsClient(client=http, cache_dir=tmp_path, use_cache=False),
    )
    trials = clinicaltrials.search_trials(query="covid", limit=1)
    assert trials[0].nct_id == "NCT04280705"
