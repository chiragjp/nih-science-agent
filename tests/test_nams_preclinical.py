"""Offline tests for the NAMs (ICE) connector and preclinical portfolio tool."""

from __future__ import annotations

import httpx

from nih_science_agent.connectors.nams import IceClient, normalize_assay
from nih_science_agent.connectors.reporter import ReporterProject
from nih_science_agent.tools import preclinical
from nih_science_agent.tools.preclinical import _classify, find_preclinical_assets

ICE_RESPONSE = {
    "endPoints": [
        {
            "casrn": "80-05-7",
            "assay": "Tox21 ER-LUC BG1",
            "endpoint": "Estrogen receptor",
            "value": 1.2,
            "unit": "uM",
            "species": "human",
            "substanceType": "single",
        },
        {
            "casrn": "80-05-7",
            "assay": "Tox21 AR-LUC",
            "endpoint": "Androgen receptor",
            "value": 3.4,
            "unit": "uM",
            "species": "human",
            "substanceType": "single",
        },
        {
            "casrn": "80-05-7",
            "assay": "Tox21 ER-LUC BG1",
            "endpoint": "Estrogen receptor",
            "value": 0.9,
            "unit": "uM",
            "species": "human",
            "substanceType": "single",
        },
    ]
}


def test_normalize_assay() -> None:
    a = normalize_assay(ICE_RESPONSE["endPoints"][0])
    assert a.casrn == "80-05-7"
    assert a.assay == "Tox21 ER-LUC BG1"
    assert a.value == "1.2"
    assert a.species == "human"


def test_ice_summary(tmp_path) -> None:
    http = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=ICE_RESPONSE))
    )
    client = IceClient(client=http, cache_dir=tmp_path, use_cache=False)
    s = client.chemical_assay_summary("80-05-7")
    assert s.total_records == 3
    assert s.distinct_assays == 2  # ER-LUC, AR-LUC
    assert s.top_assays[0]["assay"] == "Tox21 ER-LUC BG1"  # 2 records, most common
    assert s.top_assays[0]["n"] == 2


# --------------------------------------------------------------------------- #
# Preclinical NAM classification
# --------------------------------------------------------------------------- #


def test_classify_nam_methods() -> None:
    assert "organoid" in _classify("cerebral organoid model of dementia")
    assert "ipsc" in _classify("iPSC-derived neurons")
    assert "organ_on_chip" in _classify("a microphysiological liver-on-chip system")
    assert "skin_sensitization" in _classify("KeratinoSens skin sensitization assay")
    assert _classify("a standard mouse study") == []


def test_find_preclinical_assets(monkeypatch) -> None:
    monkeypatch.setattr(
        preclinical.reporter,
        "search_projects",
        lambda **kw: [
            ReporterProject(
                core_project_num="R01A",
                project_title="Cerebral organoid model",
                terms=["organoid", "alzheimer"],
            ),
            ReporterProject(
                core_project_num="R01B",
                project_title="iPSC neurons in AD",
                terms=["induced pluripotent stem cells"],
            ),
            ReporterProject(
                core_project_num="R01C",
                project_title="A mouse model of AD",
                terms=["mouse", "amyloid"],
            ),  # no NAM
        ],
    )
    res = find_preclinical_assets("ad_adrd")
    assert res.grants_scanned == 3
    assert res.grants_with_nam == 2  # A (organoid), B (ipsc); C has none
    assert res.by_method.get("organoid") == 1
    assert res.by_method.get("ipsc") == 1
    assert "R01A" in res.examples["organoid"]


def test_portfolio_map_covers_areas(monkeypatch) -> None:
    monkeypatch.setattr(preclinical.reporter, "search_projects", lambda **kw: [])
    pmap = preclinical.nams_portfolio_map()
    assert [a.area for a in pmap.areas] == ["immunology", "ad_adrd", "cardiometabolic"]
