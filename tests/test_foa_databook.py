"""Offline tests for the FOA connector and NIH Data Book connector."""
# ruff: noqa: E501  (fixture CSV rows are intentionally wide)

from __future__ import annotations

import duckdb
import httpx
import pytest

from nih_science_agent.connectors import databook
from nih_science_agent.connectors.foa import FoaClient, foa_type, guide_url, normalize_grants_gov
from nih_science_agent.storage import duckdb_store

# --------------------------------------------------------------------------- #
# FOA type classification + URL derivation
# --------------------------------------------------------------------------- #


def test_foa_type_by_prefix() -> None:
    assert foa_type("RFA-CA-19-039") == "RFA"
    assert foa_type("PAR-18-714") == "PAR"
    assert foa_type("PAS-21-100") == "PAS"
    assert foa_type("PA-20-185") == "PA"
    assert foa_type("NOT-OD-08-121") == "NOTICE"
    assert foa_type("XYZ-1") == "OTHER"
    assert foa_type(None) == "NONE"


def test_guide_url_folder_by_type() -> None:
    assert guide_url("PA-20-185").endswith("/pa-files/PA-20-185.html")
    assert guide_url("RFA-CA-19-039").endswith("/rfa-files/RFA-CA-19-039.html")
    assert guide_url("PAR-18-714").endswith("/pa-files/PAR-18-714.html")
    assert guide_url("NOT-OD-08-121").endswith("/notice-files/NOT-OD-08-121.html")


GRANTS_GOV_RESPONSE = {
    "data": {
        "oppHits": [
            {
                "number": "PA-20-185",
                "title": "NIH Research Project Grant (Parent R01)",
                "agencyCode": "HHS-NIH11",
                "oppStatus": "archived",
                "openDate": "05/05/2020",
                "closeDate": "01/01/2025",
            }
        ]
    }
}


def test_normalize_grants_gov() -> None:
    a = normalize_grants_gov(GRANTS_GOV_RESPONSE["data"]["oppHits"][0], "PA-20-185")
    assert a.foa_number == "PA-20-185"
    assert a.foa_type == "PA"
    assert a.status == "archived"
    assert a.agency == "HHS-NIH11"
    assert a.guide_url.endswith("/pa-files/PA-20-185.html")


def test_foa_client_get(tmp_path) -> None:
    http = httpx.Client(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=GRANTS_GOV_RESPONSE))
    )
    client = FoaClient(client=http, cache_dir=tmp_path, use_cache=False)
    a = client.get_foa("PA-20-185")
    assert a.title.startswith("NIH Research Project")
    assert a.source == "grants_gov"


def test_foa_client_falls_back_when_no_hit(tmp_path) -> None:
    http = httpx.Client(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(200, json={"data": {"oppHits": []}})
        )
    )
    client = FoaClient(client=http, cache_dir=tmp_path, use_cache=False)
    a = client.get_foa("RFA-XX-99-999")
    assert a.source == "derived"  # still returns type + Guide URL
    assert a.foa_type == "RFA"
    assert a.guide_url.endswith("/rfa-files/RFA-XX-99-999.html")


# --------------------------------------------------------------------------- #
# Population-scale productivity by FOA type (DuckDB)
# --------------------------------------------------------------------------- #

_PRJ_HEADER = (
    "APPLICATION_ID,ACTIVITY,ADMINISTERING_IC,IC_NAME,FY,FUNDING_MECHANISM,ORG_NAME,"
    "ORG_STATE,ORG_COUNTRY,OPPORTUNITY NUMBER,CORE_PROJECT_NUM,FULL_PROJECT_NUM,PI_IDS,"
    "PI_NAMEs,PROJECT_TITLE,TOTAL_COST"
)


def _row(appid, core, foa_num, cost):
    return (
        f"{appid},R01,CA,NCI,2021,Non-SBIR,ORG,MA,UNITED STATES,{foa_num},{core},"
        f'5{core}-01,1{appid},"PI, {appid}",T,{cost}'
    )


@pytest.fixture
def foa_db(tmp_path):
    csv_path = tmp_path / "RePORTER_PRJ_C_FY2021.csv"
    csv_path.write_text(
        _PRJ_HEADER
        + "\n"
        + _row(1, "R01CA000001", "RFA-CA-19-001", 1_000_000)
        + "\n"
        + _row(2, "R01CA000002", "PA-20-185", 1_000_000)
        + "\n"
        + _row(3, "R01CA000003", "PA-20-185", 1_000_000)
        + "\n"
    )
    publnk = tmp_path / "RePORTER_PUBLNK_C_2021.csv"
    # RFA grant -> 4 pubs; the two PA grants -> 1 pub each
    publnk.write_text(
        "PMID,PROJECT_NUMBER\n"
        "a,R01CA000001\nb,R01CA000001\nc,R01CA000001\nd,R01CA000001\n"
        "e,R01CA000002\nf,R01CA000003\n"
    )
    db_path = tmp_path / "nih.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(duckdb_store._LOAD_SQL.format(globs=f"'{csv_path}'"))
    con.execute(duckdb_store._AWARD_PI_SQL)
    con.execute(duckdb_store._LOAD_PUBLINKS_SQL.format(globs=f"'{publnk}'"))
    con.close()
    return db_path


def test_grant_level_panel(foa_db) -> None:
    con = duckdb_store.connect(foa_db)
    df = duckdb_store.grant_level_panel(con, ic="CA", years=[2021], funding_floor=0).df()
    con.close()
    assert set(df["foa_type"]) == {"RFA", "PA"}
    rfa = df[df.core_project_num == "R01CA000001"].iloc[0]
    assert rfa.foa_type == "RFA"
    assert rfa.pubs == 4  # lifetime distinct linked pubs
    assert rfa.funding == 1_000_000
    # one row per grant
    assert len(df) == df.core_project_num.nunique() == 3


def test_productivity_by_foa_type(foa_db) -> None:
    con = duckdb_store.connect(foa_db)
    rows = duckdb_store.productivity_by_foa_type(con, ic="CA", year=2021)
    con.close()
    by_type = {r.foa_type: r for r in rows}
    assert by_type["RFA"].awards == 1
    assert by_type["RFA"].publications == 4
    assert by_type["RFA"].pubs_per_million == 4.0  # 4 pubs / $1M
    assert by_type["PA"].awards == 2
    assert by_type["PA"].publications == 2  # 1 + 1
    assert by_type["PA"].total_funding == 2_000_000  # not fanned out by the join
    assert by_type["PA"].pubs_per_million == 1.0  # 2 pubs / $2M


# --------------------------------------------------------------------------- #
# Data Book success-rate parsing
# --------------------------------------------------------------------------- #


def test_success_rate_parsing(monkeypatch, tmp_path):
    import pandas as pd

    # Build an XLSX shaped like the real Data Book report (title row, header row 1).
    path = tmp_path / "report_665.xlsx"
    with pd.ExcelWriter(path) as w:
        df = pd.DataFrame(
            [
                ["Table #218: success rates ...", None, None, None, None, None, None],
                [
                    "FY",
                    "RPG reviewed",
                    "RPG awarded",
                    "RPG rate",
                    "R01 reviewed",
                    "R01 awarded",
                    "R01 rate",
                ],
                [2020, 54903, 11465, 0.209, 35147, 7767, 0.221],
                [2021, 56000, 11000, 0.196, 36000, 7800, 0.217],
                ["Note: footnote text", None, None, None, None, None, None],
            ]
        )
        df.to_excel(w, sheet_name="Table #218", header=False, index=False)

    monkeypatch.setattr(databook, "download_report", lambda rid, force=False: path)
    rows = databook.r01_success_rates()
    assert [r.fiscal_year for r in rows] == [2020, 2021]  # footnote row skipped
    assert rows[0].rpg_applications == 54903
    assert rows[0].r01_success_rate == pytest.approx(0.221)
