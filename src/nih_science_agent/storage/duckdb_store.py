"""DuckDB analytics store over ExPORTER bulk award files.

Loads the per-year projects CSVs into a single ``awards`` table and exposes
population-scale queries that the RePORTER API cannot serve (it caps result
offsets well below NIH's annual award count). This is what makes all-NIH
questions — funding concentration, most-funded PIs — actually answerable.

PI attribution: each award's TOTAL_COST is attributed to its **contact PI**
(parsed from the ExPORTER ``PI_IDS``/``PI_NAMEs`` multi-PI fields). That matches
the standard way funding concentration is computed; multi-PI splitting is a
known simplification.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, Field

from nih_science_agent.storage import exporter

logger = logging.getLogger(__name__)

# Quoted because the ExPORTER header has a space / mixed case in these names.
# parallel=false: ExPORTER abstracts/titles contain quoted newlines, which the
# parallel scanner cannot combine with null_padding.
_LOAD_SQL = """
CREATE OR REPLACE TABLE awards AS
SELECT
    TRY_CAST("APPLICATION_ID" AS BIGINT)        AS application_id,
    "CORE_PROJECT_NUM"                          AS core_project_num,
    "FULL_PROJECT_NUM"                          AS full_project_num,
    "ACTIVITY"                                  AS activity_code,
    "ADMINISTERING_IC"                          AS ic,
    "IC_NAME"                                   AS ic_name,
    TRY_CAST("FY" AS INTEGER)                   AS fiscal_year,
    "FUNDING_MECHANISM"                         AS funding_mechanism,
    "ORG_NAME"                                  AS org_name,
    "ORG_STATE"                                 AS org_state,
    "ORG_COUNTRY"                               AS org_country,
    "OPPORTUNITY NUMBER"                        AS foa_number,
    "PI_IDS"                                    AS pi_ids,
    "PI_NAMEs"                                  AS pi_names,
    "PROJECT_TITLE"                             AS project_title,
    TRY_CAST("TOTAL_COST" AS DOUBLE)            AS total_cost
FROM read_csv([{globs}], header=true, all_varchar=true, union_by_name=true,
              encoding='utf-8', ignore_errors=true, null_padding=true, parallel=false);
"""

# Publication link table: PMID <-> core project number (ExPORTER PUBLNK files).
# filename=true lets us recover the publication year from each file's name
# (RePORTER_PUBLNK_C_YYYY) — the PUBLNK files are organized by publication year.
_LOAD_PUBLINKS_SQL = """
CREATE OR REPLACE TABLE publinks AS
SELECT DISTINCT "PMID" AS pmid, "PROJECT_NUMBER" AS core_project_num,
       TRY_CAST(regexp_extract(filename, '_C_([0-9]{{4}})', 1) AS INTEGER) AS pub_year
FROM read_csv([{globs}], header=true, all_varchar=true, union_by_name=true,
              encoding='utf-8', ignore_errors=true, null_padding=true,
              parallel=false, filename=true)
WHERE "PROJECT_NUMBER" IS NOT NULL AND "PMID" IS NOT NULL;
"""

# Contact PI: prefer the id/name flagged "(contact)", else the first listed.
_AWARD_PI_SQL = r"""
CREATE OR REPLACE VIEW award_pi AS
SELECT
    *,
    COALESCE(
        NULLIF(regexp_extract(pi_ids, '([0-9]+) \(contact\)', 1), ''),
        regexp_extract(pi_ids, '([0-9]+)', 1)
    ) AS contact_pi_id,
    COALESCE(
        NULLIF(trim(regexp_extract(pi_names, '([^;]+) \(contact\)', 1)), ''),
        trim(split_part(pi_names, ';', 1))
    ) AS contact_pi_name
FROM awards;
"""


def default_db_path() -> Path:
    from nih_science_agent.config import get_settings

    d = get_settings().processed_dir
    d.mkdir(parents=True, exist_ok=True)
    return d / "nih.duckdb"


def build_awards_db(
    years: list[int],
    db_path: Path | None = None,
    download: bool = True,
) -> Path:
    """Load ExPORTER projects for ``years`` into the DuckDB ``awards`` table."""
    db_path = db_path or default_db_path()
    csvs: list[str] = []
    for y in years:
        csv_path = (
            exporter.ensure_projects_csv(y)
            if download
            else (exporter._raw_dir() / f"RePORTER_PRJ_C_FY{y}.csv")
        )
        csvs.append(str(csv_path))

    globs = ", ".join(f"'{c}'" for c in csvs)
    con = duckdb.connect(str(db_path))
    try:
        con.execute(_LOAD_SQL.format(globs=globs))
        con.execute(_AWARD_PI_SQL)
        n = con.execute("SELECT count(*) FROM awards").fetchone()[0]
        logger.info("Loaded %s award rows from %s file(s)", n, len(csvs))
    finally:
        con.close()
    return db_path


def build_publinks_db(years: list[int], db_path: Path | None = None, download: bool = True) -> Path:
    """Load ExPORTER publication link tables for ``years`` into ``publinks``."""
    db_path = db_path or default_db_path()
    csvs: list[str] = []
    for y in years:
        csv_path = (
            exporter.ensure_csv("publinks", y)
            if download
            else (exporter._raw_dir() / f"RePORTER_PUBLNK_C_{y}.csv")
        )
        csvs.append(str(csv_path))
    globs = ", ".join(f"'{c}'" for c in csvs)
    con = duckdb.connect(str(db_path))
    try:
        con.execute(_LOAD_PUBLINKS_SQL.format(globs=globs))
        n = con.execute("SELECT count(*) FROM publinks").fetchone()[0]
        logger.info("Loaded %s publication links from %s file(s)", n, len(csvs))
    finally:
        con.close()
    return db_path


def connect(db_path: Path | None = None) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(db_path or default_db_path()), read_only=True)


# --------------------------------------------------------------------------- #
# Population-scale queries
# --------------------------------------------------------------------------- #


class ConcentrationResult(BaseModel):
    scope: dict = Field(default_factory=dict)
    n_pis: int = 0
    total_funding: float = 0.0
    top1_pct_share: float | None = None  # share of $ held by the top 1% of PIs
    top5_pct_share: float | None = None
    top10_pct_share: float | None = None
    gini: float | None = None
    top_pis: list[dict[str, Any]] = Field(default_factory=list)


# ExPORTER's ADMINISTERING_IC uses 2-letter codes; map common abbreviations.
IC_ABBREV_TO_CODE = {
    "NCI": "CA",
    "NIAID": "AI",
    "NIGMS": "GM",
    "NHLBI": "HL",
    "NIA": "AG",
    "NIDDK": "DK",
    "NINDS": "NS",
    "NIMH": "MH",
    "NICHD": "HD",
    "NIEHS": "ES",
    "NIDA": "DA",
    "NEI": "EY",
    "NIDCD": "DC",
    "NIDCR": "DE",
    "NIAMS": "AR",
    "NIBIB": "EB",
    "NHGRI": "HG",
    "NIAAA": "AA",
    "NCATS": "TR",
    "NIMHD": "MD",
    "NLM": "LM",
    "NINR": "NR",
    "NCCIH": "AT",
    "FIC": "TW",
    "NIMH ": "MH",
}


def _resolve_ic(ic: str) -> str:
    u = ic.upper().strip()
    return IC_ABBREV_TO_CODE.get(u, u)  # pass through if already a 2-letter code


def _where(ic: str | None, year: int | None) -> tuple[str, list]:
    clauses, params = ["contact_pi_id <> ''", "total_cost > 0"], []
    if ic:
        clauses.append("ic = ?")
        params.append(_resolve_ic(ic))
    if year:
        clauses.append("fiscal_year = ?")
        params.append(year)
    return " AND ".join(clauses), params


def funding_concentration(
    con: duckdb.DuckDBPyConnection,
    ic: str | None = None,
    year: int | None = None,
    top_n: int = 15,
) -> ConcentrationResult:
    """Funding concentration across contact PIs (top-share + Gini)."""
    where, params = _where(ic, year)
    rows = con.execute(
        f"""
        SELECT contact_pi_id,
               any_value(contact_pi_name) AS pi_name,
               sum(total_cost) AS funding,
               count(*) AS awards
        FROM award_pi
        WHERE {where}
        GROUP BY contact_pi_id
        ORDER BY funding DESC
        """,
        params,
    ).fetchall()

    res = ConcentrationResult(scope={"ic": ic, "year": year})
    if not rows:
        return res

    fundings = [r[2] for r in rows]
    total = sum(fundings)
    n = len(rows)
    res.n_pis = n
    res.total_funding = total

    def top_share(frac: float) -> float:
        k = max(1, round(n * frac))
        return sum(fundings[:k]) / total if total else 0.0

    res.top1_pct_share = top_share(0.01)
    res.top5_pct_share = top_share(0.05)
    res.top10_pct_share = top_share(0.10)

    # Gini over PI funding (ascending order).
    cum = 0.0
    weighted = 0.0
    for v in sorted(fundings):
        cum += v
        weighted += cum
    res.gini = (n + 1 - 2 * (weighted / total)) / n if total else None

    res.top_pis = [
        {"pi_id": r[0], "pi_name": r[1], "funding": r[2], "awards": r[3]} for r in rows[:top_n]
    ]
    return res


def _spearman(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    rx = [0.0] * n
    ry = [0.0] * n
    for r, i in enumerate(sorted(range(n), key=lambda i: xs[i])):
        rx[i] = float(r)
    for r, i in enumerate(sorted(range(n), key=lambda i: ys[i])):
        ry[i] = float(r)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((rx[i] - mx) * (ry[i] - my) for i in range(n))
    vx = sum((rx[i] - mx) ** 2 for i in range(n)) ** 0.5
    vy = sum((ry[i] - my) ** 2 for i in range(n)) ** 0.5
    return cov / (vx * vy) if vx and vy else None


class ProductivityBin(BaseModel):
    label: str
    n_pis: int
    mean_funding: float
    mean_pubs: float
    mean_pubs_per_million: float


class BulkProductivityResult(BaseModel):
    scope: dict = Field(default_factory=dict)
    n_pis: int = 0
    bins: list[ProductivityBin] = Field(default_factory=list)
    spearman_funding_vs_pubs_per_dollar: float | None = None
    notes: list[str] = Field(default_factory=list)


def funding_vs_output(
    con: duckdb.DuckDBPyConnection,
    ic: str | None = None,
    year: int | None = None,
    funding_floor: float = 250_000.0,
) -> BulkProductivityResult:
    """Population-scale diminishing-returns: pubs-per-dollar vs PI grant support.

    Output = distinct linked publications (from the ExPORTER link tables) over a
    PI's grants — the whole population, no API sampling. Funding and publications
    are aggregated separately then joined, so the LEFT JOIN to ``publinks`` never
    fans out and inflates the funding sum.
    """
    where, params = _where(ic, year)
    sql = f"""
        WITH f AS (
            SELECT contact_pi_id,
                   sum(total_cost) AS funding
            FROM award_pi WHERE {where}
            GROUP BY contact_pi_id
        ),
        p AS (
            SELECT ap.contact_pi_id, count(DISTINCT pl.pmid) AS pubs
            FROM award_pi ap
            JOIN publinks pl ON pl.core_project_num = ap.core_project_num
            WHERE {where}
            GROUP BY ap.contact_pi_id
        )
        SELECT f.funding, COALESCE(p.pubs, 0) AS pubs
        FROM f LEFT JOIN p USING (contact_pi_id)
        WHERE f.funding >= ?
        ORDER BY f.funding
    """
    rows = con.execute(sql, params + params + [funding_floor]).fetchall()

    res = BulkProductivityResult(scope={"ic": ic, "year": year})
    if len(rows) < 4:
        res.notes.append("Too few PIs above the funding floor for a stable trend.")
        return res
    res.n_pis = len(rows)

    fundings = [r[0] for r in rows]
    pubs = [r[1] for r in rows]
    per_m = [p / (f / 1_000_000) if f else 0.0 for f, p in rows]

    q = len(rows) // 4
    chunks = [(0, q), (q, 2 * q), (2 * q, 3 * q), (3 * q, len(rows))]
    for i, (a, b) in enumerate(chunks, 1):
        if b <= a:
            continue
        res.bins.append(
            ProductivityBin(
                label=f"Q{i}",
                n_pis=b - a,
                mean_funding=sum(fundings[a:b]) / (b - a),
                mean_pubs=sum(pubs[a:b]) / (b - a),
                mean_pubs_per_million=sum(per_m[a:b]) / (b - a),
            )
        )
    res.spearman_funding_vs_pubs_per_dollar = _spearman(fundings, per_m)
    res.notes.append(
        "Output is distinct linked publications (lifetime; RePORTER-authoritative, "
        "a floor). Support is total funding for the scoped fiscal year(s)."
    )
    return res


# FOA type classification by number prefix (mirrors connectors/foa.py).
_FOA_TYPE_SQL = """
    CASE
        WHEN foa_number IS NULL OR foa_number = '' THEN 'NONE'
        WHEN foa_number LIKE 'RFA-%' THEN 'RFA'
        WHEN foa_number LIKE 'PAR-%' THEN 'PAR'
        WHEN foa_number LIKE 'PAS-%' THEN 'PAS'
        WHEN foa_number LIKE 'PA-%'  THEN 'PA'
        WHEN foa_number LIKE 'NOT-%' THEN 'NOTICE'
        ELSE 'OTHER'
    END
"""


class LatencyResult(BaseModel):
    scope: dict = Field(default_factory=dict)
    grants_total: int = 0
    grants_with_output: int = 0
    grants_censored: int = 0  # no linked publication yet
    pct_censored: float = 0.0
    median_years_to_first_pub: float | None = None
    p25_years: float | None = None
    p75_years: float | None = None
    notes: list[str] = Field(default_factory=list)


def grant_to_output_latency(
    con: duckdb.DuckDBPyConnection,
    ic: str | None = None,
    years: list[int] | None = None,
    current_year: int = 2026,
) -> LatencyResult:
    """Time from a grant's start year to its first linked publication (bulk, scaled).

    Uses each grant's earliest fiscal year (start) and the earliest publication
    year from the link tables. Grants with no linked publication are **censored**
    (counted, not dropped) — a silent drop would bias latency downward. Recent
    grants are right-censored (less time to publish), so the median is reported
    with the censoring rate alongside.
    """
    clauses = ["total_cost > 0", "core_project_num IS NOT NULL"]
    params: list = []
    if ic:
        clauses.append("ic = ?")
        params.append(_resolve_ic(ic))
    if years:
        clauses.append(f"fiscal_year IN ({','.join('?' * len(years))})")
        params += list(years)
    where = " AND ".join(clauses)

    sql = f"""
        WITH g AS (
            SELECT core_project_num, min(fiscal_year) AS start_fy
            FROM awards WHERE {where} GROUP BY core_project_num
        ),
        firstpub AS (
            SELECT core_project_num, min(pub_year) AS first_pub
            FROM publinks WHERE pub_year IS NOT NULL GROUP BY core_project_num
        )
        SELECT g.start_fy, fp.first_pub
        FROM g LEFT JOIN firstpub fp USING (core_project_num)
    """
    rows = con.execute(sql, params).fetchall()

    res = LatencyResult(scope={"ic": ic, "years": years})
    res.grants_total = len(rows)
    if not rows:
        return res

    latencies: list[int] = []
    for start_fy, first_pub in rows:
        if first_pub is not None and start_fy is not None and first_pub >= start_fy:
            latencies.append(first_pub - start_fy)
    res.grants_with_output = len(latencies)
    res.grants_censored = res.grants_total - res.grants_with_output
    res.pct_censored = res.grants_censored / res.grants_total

    if latencies:
        latencies.sort()

        def pct(p: float) -> float:
            return float(latencies[min(len(latencies) - 1, int(p * len(latencies)))])

        res.median_years_to_first_pub = pct(0.5)
        res.p25_years = pct(0.25)
        res.p75_years = pct(0.75)

    res.notes.append(
        f"{res.pct_censored:.0%} of grants have no linked publication (censored — "
        "recent grants are right-censored; RePORTER linkage is also incomplete)."
    )
    return res


def grant_level_panel(
    con: duckdb.DuckDBPyConnection,
    ic: str | None = None,
    years: list[int] | None = None,
    funding_floor: float = 100_000.0,
):
    """One row per grant for regression: funding, mechanism, year, FOA type, pubs.

    Aggregates award-years to the core grant (funding summed, earliest FY, a
    representative FOA/mechanism) and attaches lifetime distinct linked
    publications. Returns a DuckDB relation (use ``.df()`` for a DataFrame).
    """
    clauses = ["total_cost > 0", "core_project_num IS NOT NULL"]
    params: list = []
    if ic:
        clauses.append("ic = ?")
        params.append(_resolve_ic(ic))
    if years:
        clauses.append(f"fiscal_year IN ({','.join('?' * len(years))})")
        params += list(years)
    where = " AND ".join(clauses)

    sql = f"""
        WITH g AS (
            SELECT core_project_num,
                   any_value(activity_code) AS activity_code,
                   min(fiscal_year) AS fiscal_year,
                   max(foa_number) AS foa_number,
                   sum(total_cost) AS funding
            FROM awards WHERE {where}
            GROUP BY core_project_num
        ),
        pc AS (
            SELECT core_project_num, count(DISTINCT pmid) AS pubs
            FROM publinks GROUP BY core_project_num
        )
        SELECT g.core_project_num, g.activity_code, g.fiscal_year,
               {_FOA_TYPE_SQL} AS foa_type, g.funding, COALESCE(pc.pubs, 0) AS pubs
        FROM g LEFT JOIN pc USING (core_project_num)
        WHERE g.funding >= ?
    """
    return con.execute(sql, params + [funding_floor])


class FoaTypeRow(BaseModel):
    foa_type: str
    awards: int
    total_funding: float
    publications: int
    pubs_per_million: float | None = None


def productivity_by_foa_type(
    con: duckdb.DuckDBPyConnection,
    ic: str | None = None,
    year: int | None = None,
) -> list[FoaTypeRow]:
    """Linked-publications-per-dollar by FOA type (RFA vs PA …), at population scale.

    Does a targeted RFA produce different output per dollar than a parent program
    announcement? Funding and pubs are aggregated separately per type, then
    combined — no join fan-out.
    """
    # Portfolio-level FOA stats don't need a contact PI; drop that clause.
    where, params = _where(ic, year)
    where = where.replace("contact_pi_id <> '' AND ", "")
    # publinks shares no column names with awards (except the join key), so the
    # WHERE clause is unambiguous in both CTEs without qualification.
    sql = f"""
        WITH f AS (
            SELECT {_FOA_TYPE_SQL} AS foa_type,
                   count(*) AS awards,
                   sum(total_cost) AS funding
            FROM awards WHERE {where}
            GROUP BY 1
        ),
        p AS (
            SELECT {_FOA_TYPE_SQL} AS foa_type,
                   count(DISTINCT pl.pmid) AS pubs
            FROM awards
            JOIN publinks pl ON pl.core_project_num = awards.core_project_num
            WHERE {where}
            GROUP BY 1
        )
        SELECT f.foa_type, f.awards, f.funding, COALESCE(p.pubs, 0)
        FROM f LEFT JOIN p USING (foa_type)
        ORDER BY f.funding DESC
    """
    rows = con.execute(sql, params + params).fetchall()
    out: list[FoaTypeRow] = []
    for ftype, awards, funding, pubs in rows:
        ppm = pubs / (funding / 1_000_000) if funding else None
        out.append(
            FoaTypeRow(
                foa_type=ftype,
                awards=awards,
                total_funding=funding or 0.0,
                publications=pubs,
                pubs_per_million=ppm,
            )
        )
    return out
