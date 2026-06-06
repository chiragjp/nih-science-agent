"""Offline tests for the ExPORTER → DuckDB store (no network; synthetic CSV)."""
# ruff: noqa: E501  (the fixture CSV rows are intentionally wide)

from __future__ import annotations

import duckdb
import pytest

from nih_science_agent.storage import duckdb_store

# Minimal ExPORTER-shaped projects CSV (the columns the loader selects).
CSV = """APPLICATION_ID,ACTIVITY,ADMINISTERING_IC,IC_NAME,FY,FUNDING_MECHANISM,ORG_NAME,ORG_STATE,ORG_COUNTRY,OPPORTUNITY NUMBER,CORE_PROJECT_NUM,FULL_PROJECT_NUM,PI_IDS,PI_NAMEs,PROJECT_TITLE,TOTAL_COST
1,R01,CA,National Cancer Institute,2021,Non-SBIR,HARVARD,MA,UNITED STATES,PA-20-185,R01CA000001,5R01CA000001-03,111 (contact),"DOE, JANE (contact)",Cancer study,1000000
2,R01,CA,National Cancer Institute,2021,Non-SBIR,STANFORD,CA,UNITED STATES,PA-20-185,R01CA000002,5R01CA000002-01,111 (contact); 222,"DOE, JANE (contact); ROE, JOHN",Second Doe grant,500000
3,U01,CA,National Cancer Institute,2021,Non-SBIR,MIT,MA,UNITED STATES,PA-20-185,U01CA000003,5U01CA000003-02,333,"SMITH, ANN",Smith grant,250000
4,R21,HL,NHLBI,2021,Non-SBIR,YALE,CT,UNITED STATES,PA-20-185,R21HL000004,5R21HL000004-01,444,"LEE, KIM",Heart grant,90000
5,R01,CA,National Cancer Institute,2021,Non-SBIR,UCLA,CA,UNITED STATES,PA-20-185,R01CA000005,5R01CA000005-01,,,No PI grant,300000
"""


@pytest.fixture
def db(tmp_path):
    csv_path = tmp_path / "RePORTER_PRJ_C_FY2021.csv"
    csv_path.write_text(CSV)
    db_path = tmp_path / "nih.duckdb"
    # Publication link table: Doe's grant 1 has 3 pubs, Smith's has 1.
    publnk = tmp_path / "RePORTER_PUBLNK_C_2021.csv"
    publnk.write_text(
        "PMID,PROJECT_NUMBER\np1,R01CA000001\np2,R01CA000001\np3,R01CA000001\np9,U01CA000003\n"
    )
    # build directly from the CSV paths (bypass network download)
    con = duckdb.connect(str(db_path))
    con.execute(duckdb_store._LOAD_SQL.format(globs=f"'{csv_path}'"))
    con.execute(duckdb_store._AWARD_PI_SQL)
    con.execute(duckdb_store._LOAD_PUBLINKS_SQL.format(globs=f"'{publnk}'"))
    con.close()
    return db_path


def test_awards_table_loaded_and_typed(db) -> None:
    con = duckdb_store.connect(db)
    rows = con.execute("SELECT count(*), sum(total_cost) FROM awards").fetchone()
    assert rows[0] == 5
    assert rows[1] == 1000000 + 500000 + 250000 + 90000 + 300000
    # types coerced
    fy = con.execute("SELECT DISTINCT fiscal_year FROM awards").fetchall()
    assert fy == [(2021,)]
    con.close()


def test_contact_pi_parsing(db) -> None:
    con = duckdb_store.connect(db)
    # award 2 has "111 (contact); 222" -> contact is 111 / DOE, JANE
    pid, pname = con.execute(
        "SELECT contact_pi_id, contact_pi_name FROM award_pi WHERE application_id = 2"
    ).fetchone()
    assert pid == "111"
    assert pname == "DOE, JANE"
    con.close()


def test_funding_concentration_aggregates_by_pi(db) -> None:
    con = duckdb_store.connect(db)
    res = duckdb_store.funding_concentration(con, ic="CA")
    con.close()
    # CA PIs: Doe(111)=1.0M+0.5M=1.5M, Smith(333)=0.25M; award 5 has no PI -> excluded
    assert res.n_pis == 2
    assert res.total_funding == 1_750_000
    top = res.top_pis[0]
    assert top["pi_id"] == "111" and top["funding"] == 1_500_000
    assert top["awards"] == 2  # Doe holds two grants
    # Doe alone (top of 2) holds 1.5M/1.75M
    assert res.top10_pct_share == pytest.approx(1_500_000 / 1_750_000)
    assert 0.0 <= res.gini <= 1.0


def test_concentration_ic_filter(db) -> None:
    con = duckdb_store.connect(db)
    hl = duckdb_store.funding_concentration(con, ic="HL")
    con.close()
    assert hl.n_pis == 1
    assert hl.top_pis[0]["pi_name"] == "LEE, KIM"


def test_funding_vs_output_no_join_fanout(tmp_path) -> None:
    # 4 CA PIs, each a single $1M grant. PI 1's grant has 3 linked pubs; the
    # LEFT JOIN to publinks must NOT inflate that PI's funding to $3M.
    rows = "\n".join(
        f"{i},R01,CA,NCI,2021,Non-SBIR,ORG{i},MA,UNITED STATES,PA,R01CA00000{i},"
        f'5R01CA00000{i}-01,{i}00,"PI, {i}",Grant {i},1000000'
        for i in range(1, 5)
    )
    header = (
        "APPLICATION_ID,ACTIVITY,ADMINISTERING_IC,IC_NAME,FY,FUNDING_MECHANISM,ORG_NAME,"
        "ORG_STATE,ORG_COUNTRY,OPPORTUNITY NUMBER,CORE_PROJECT_NUM,FULL_PROJECT_NUM,PI_IDS,"
        "PI_NAMEs,PROJECT_TITLE,TOTAL_COST"
    )
    csv_path = tmp_path / "RePORTER_PRJ_C_FY2021.csv"
    csv_path.write_text(header + "\n" + rows + "\n")
    publnk = tmp_path / "RePORTER_PUBLNK_C_2021.csv"
    publnk.write_text("PMID,PROJECT_NUMBER\np1,R01CA000001\np2,R01CA000001\np3,R01CA000001\n")

    db_path = tmp_path / "nih.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(duckdb_store._LOAD_SQL.format(globs=f"'{csv_path}'"))
    con.execute(duckdb_store._AWARD_PI_SQL)
    con.execute(duckdb_store._LOAD_PUBLINKS_SQL.format(globs=f"'{publnk}'"))
    con.close()

    con = duckdb_store.connect(db_path)
    res = duckdb_store.funding_vs_output(con, ic="CA", funding_floor=0)
    con.close()
    assert res.n_pis == 4
    total_funding = sum(b.mean_funding * b.n_pis for b in res.bins)
    assert total_funding == 4_000_000  # 4 x $1M, not inflated by PI 1's 3 pubs


def test_grant_to_output_latency(tmp_path) -> None:
    # G1 starts FY2010, first pub 2013 → latency 3. G2 starts 2011, no pubs → censored.
    rows = (
        '1,R01,CA,NCI,2010,Non-SBIR,O,MA,US,PA,R01CA1,5R01CA1-01,1,"PI, 1",T,500000\n'
        '2,R01,CA,NCI,2011,Non-SBIR,O,MA,US,PA,R01CA2,5R01CA2-01,2,"PI, 2",T,500000\n'
    )
    header = (
        "APPLICATION_ID,ACTIVITY,ADMINISTERING_IC,IC_NAME,FY,FUNDING_MECHANISM,ORG_NAME,"
        "ORG_STATE,ORG_COUNTRY,OPPORTUNITY NUMBER,CORE_PROJECT_NUM,FULL_PROJECT_NUM,PI_IDS,"
        "PI_NAMEs,PROJECT_TITLE,TOTAL_COST"
    )
    csv_path = tmp_path / "RePORTER_PRJ_C_FY2010.csv"
    csv_path.write_text(header + "\n" + rows)
    # link-table file named by pub year so the loader recovers pub_year=2013
    publnk = tmp_path / "RePORTER_PUBLNK_C_2013.csv"
    publnk.write_text("PMID,PROJECT_NUMBER\npx,R01CA1\n")

    db_path = tmp_path / "nih.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(duckdb_store._LOAD_SQL.format(globs=f"'{csv_path}'"))
    con.execute(duckdb_store._AWARD_PI_SQL)
    con.execute(duckdb_store._LOAD_PUBLINKS_SQL.format(globs=f"'{publnk}'"))
    con.close()

    con = duckdb_store.connect(db_path)
    res = duckdb_store.grant_to_output_latency(con, ic="CA")
    con.close()
    assert res.grants_total == 2
    assert res.grants_with_output == 1
    assert res.grants_censored == 1  # G2 has no pub
    assert res.median_years_to_first_pub == 3.0  # 2013 - 2010


def test_ic_abbreviation_resolves() -> None:
    assert duckdb_store._resolve_ic("NCI") == "CA"
    assert duckdb_store._resolve_ic("ca") == "CA"  # already a code, passthrough
    assert duckdb_store._resolve_ic("NIDDK") == "DK"
