"""CDC connector — population health outcomes via the Socrata Open Data API.

Pulls national/state health signals from data.cdc.gov: NCHS leading-causes-of-death
(age-adjusted death rates, 1999–2017) and PLACES local prevalence estimates
(BRFSS-derived). JSON/REST, no key required (an app token only raises rate limits).

These are the *outcome* layer — the nation's health pulse. They are juxtaposed
with NIH funding, never causally attributed to it (see tools/pulse.py).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from nih_science_agent.connectors._http import CachedClient

SOCRATA_BASE = "https://data.cdc.gov/resource"
MORTALITY_DATASET = "bi63-dtpu"  # NCHS leading causes of death, by state/year
PLACES_DATASET = "swc5-untb"  # PLACES county-level health estimates

# NCHS leading-cause short names (the dataset's ``cause_name`` field values).
MORTALITY_CAUSES = {
    "All causes",
    "Alzheimer's disease",
    "CLRD",
    "Cancer",
    "Diabetes",
    "Heart disease",
    "Influenza and pneumonia",
    "Kidney disease",
    "Stroke",
    "Suicide",
    "Unintentional injuries",
}


class MortalityPoint(BaseModel):
    year: int
    cause_name: str | None = None
    state: str | None = None
    deaths: int | None = None
    aadr: float | None = None  # age-adjusted death rate per 100k
    source_url: str | None = None
    retrieved_at: str | None = None


class PrevalencePoint(BaseModel):
    measure: str | None = None
    location: str | None = None
    state: str | None = None
    year: int | None = None
    value: float | None = None  # percent
    unit: str | None = None


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _to_int(v: Any) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_float(v: Any) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _soql_str(v: str) -> str:
    """Quote a string literal for a SoQL ``$where`` clause (escapes apostrophes)."""
    return "'" + v.replace("'", "''") + "'"


class CDCClient(CachedClient):
    """Cached client over the CDC Socrata Open Data API."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(cache_subdir="cdc", **kwargs)

    def leading_causes_of_death(
        self,
        cause_name: str,
        state: str = "United States",
        years: list[int] | None = None,
    ) -> list[MortalityPoint]:
        """Age-adjusted death-rate time series for one cause in one geography."""
        where = [f"cause_name={_soql_str(cause_name)}", f"state={_soql_str(state)}"]
        if years:
            where.append(f"year >= '{min(years)}' AND year <= '{max(years)}'")
        params = {
            "$select": "year,cause_name,state,deaths,aadr",
            "$where": " AND ".join(where),
            "$order": "year",
            "$limit": 5000,
        }
        ts = _utcnow_iso()
        url = f"{SOCRATA_BASE}/{MORTALITY_DATASET}.json"
        rows = self.get_json_list(url, params)
        return [
            MortalityPoint(
                year=_to_int(r.get("year")) or 0,
                cause_name=r.get("cause_name"),
                state=r.get("state"),
                deaths=_to_int(r.get("deaths")),
                aadr=_to_float(r.get("aadr")),
                source_url=f"https://data.cdc.gov/d/{MORTALITY_DATASET}",
                retrieved_at=ts,
            )
            for r in rows
        ]

    def places_prevalence(
        self, measure: str, state: str | None = None, year: int | None = None, limit: int = 5000
    ) -> list[PrevalencePoint]:
        """Local prevalence estimates for a PLACES measure (BRFSS-derived)."""
        where = [f"measure={_soql_str(measure)}"]
        if state:
            where.append(f"statedesc={_soql_str(state)}")
        if year:
            where.append(f"year='{year}'")
        params = {
            "$select": "measure,locationname,statedesc,year,data_value,data_value_unit",
            "$where": " AND ".join(where),
            "$limit": limit,
        }
        url = f"{SOCRATA_BASE}/{PLACES_DATASET}.json"
        rows = self.get_json_list(url, params)
        return [
            PrevalencePoint(
                measure=r.get("measure"),
                location=r.get("locationname"),
                state=r.get("statedesc"),
                year=_to_int(r.get("year")),
                value=_to_float(r.get("data_value")),
                unit=r.get("data_value_unit"),
            )
            for r in rows
        ]


# --------------------------------------------------------------------------- #
# Module-level convenience functions
# --------------------------------------------------------------------------- #


def leading_causes_of_death(
    cause_name: str,
    state: str = "United States",
    years: list[int] | None = None,
    use_cache: bool = True,
) -> list[MortalityPoint]:
    with CDCClient(use_cache=use_cache) as client:
        return client.leading_causes_of_death(cause_name, state=state, years=years)


def places_prevalence(
    measure: str, state: str | None = None, year: int | None = None, use_cache: bool = True
) -> list[PrevalencePoint]:
    with CDCClient(use_cache=use_cache) as client:
        return client.places_prevalence(measure, state=state, year=year)
