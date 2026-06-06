"""NIH Data Book connector — aggregate reference statistics.

The Data Book (report.nih.gov) publishes the *aggregate* tables that public
RePORTER structurally cannot: success/award rates, application counts, and the
like. These fill the application-funnel gap in the Open Mike catalog.

IMPORTANT: this is a **reference layer**, not a graph extension. The figures are
pre-aggregated over applications we do not have, so they provide context
*alongside* award-level analyses — they are not joinable to individual grants.

Reports download as XLSX from ``report.nih.gov/reportweb/web/displayreport?rId=N``
and are cached under ``data/raw/databook/``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pandas as pd
from pydantic import BaseModel

logger = logging.getLogger(__name__)

REPORT_URL = "https://report.nih.gov/reportweb/web/displayreport?rId={rid}"
USER_AGENT = "nih-science-agent/0.1 (+https://report.nih.gov)"

# Known Data Book reports (report id → cache filename).
R01_SUCCESS_RATES_RID = 665  # RPG & R01-equivalent success rates, FY1970–present


class SuccessRateRow(BaseModel):
    fiscal_year: int
    rpg_applications: int | None = None
    rpg_awards: int | None = None
    rpg_success_rate: float | None = None
    r01_applications: int | None = None
    r01_awards: int | None = None
    r01_success_rate: float | None = None


def _cache_dir() -> Path:
    from nih_science_agent.config import get_settings

    d = get_settings().raw_dir / "databook"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download_report(rid: int, force: bool = False, timeout: float = 120.0) -> Path:
    """Download a Data Book report XLSX by report id (cached)."""
    dest = _cache_dir() / f"report_{rid}.xlsx"
    if dest.exists() and not force and dest.stat().st_size > 0:
        return dest
    logger.info("Downloading NIH Data Book report rId=%s", rid)
    r = httpx.get(
        REPORT_URL.format(rid=rid),
        headers={"User-Agent": USER_AGENT},
        timeout=timeout,
        follow_redirects=True,
    )
    r.raise_for_status()
    dest.write_bytes(r.content)
    return dest


def _to_int(v: object) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _to_float(v: object) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_rate(v: object) -> float | None:
    """Normalize a success rate to a 0–1 fraction (some years store it as a percent)."""
    f = _to_float(v)
    if f is None:
        return None
    return f / 100.0 if f > 1.0 else f


def r01_success_rates(use_cache: bool = True) -> list[SuccessRateRow]:
    """NIH success rates by fiscal year (RPG and R01-equivalent), FY1970–present.

    Returns the application funnel — applications reviewed, awards made, and the
    success rate — that the funded-only RePORTER data cannot provide.
    """
    path = download_report(R01_SUCCESS_RATES_RID, force=not use_cache)
    # Header is on the second row; data follows, by position.
    df = pd.read_excel(path, sheet_name=0, header=1)
    out: list[SuccessRateRow] = []
    for row in df.itertuples(index=False):
        vals = list(row)
        fy = _to_int(vals[0])
        if fy is None or fy < 1900:  # skip footnote / blank rows
            continue
        out.append(
            SuccessRateRow(
                fiscal_year=fy,
                rpg_applications=_to_int(vals[1]) if len(vals) > 1 else None,
                rpg_awards=_to_int(vals[2]) if len(vals) > 2 else None,
                rpg_success_rate=_to_rate(vals[3]) if len(vals) > 3 else None,
                r01_applications=_to_int(vals[4]) if len(vals) > 4 else None,
                r01_awards=_to_int(vals[5]) if len(vals) > 5 else None,
                r01_success_rate=_to_rate(vals[6]) if len(vals) > 6 else None,
            )
        )
    return out
