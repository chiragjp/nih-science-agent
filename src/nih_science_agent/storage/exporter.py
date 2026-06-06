"""NIH ExPORTER bulk-file downloader.

ExPORTER (https://reporter.nih.gov/exporter) publishes the full annual award
tables as zipped CSVs — the bulk source needed for population-scale analyses
that the RePORTER API's offset cap (~15k) cannot serve. This module downloads
and extracts the per-fiscal-year *projects* files; the DuckDB store loads them.

Files are cached under ``data/raw/exporter/``; an existing download is reused.
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "nih-science-agent/0.1 (+https://reporter.nih.gov/exporter)"

# Each ExPORTER file kind: (download endpoint segment, local zip filename template).
# Projects are by fiscal year; publication link tables are by calendar year.
_KINDS = {
    "projects": ("projects", "RePORTER_PRJ_C_FY{year}.zip"),
    "publinks": ("linktables", "RePORTER_PUBLNK_C_{year}.zip"),
}


def _raw_dir() -> Path:
    from nih_science_agent.config import get_settings

    d = get_settings().raw_dir / "exporter"
    d.mkdir(parents=True, exist_ok=True)
    return d


def download(kind: str, year: int, force: bool = False, timeout: float = 600.0) -> Path:
    """Download an ExPORTER ``kind`` ('projects'|'publinks') for ``year`` (cached)."""
    segment, template = _KINDS[kind]
    dest = _raw_dir() / template.format(year=year)
    if dest.exists() and not force and dest.stat().st_size > 0:
        logger.info("ExPORTER %s %s cached (%s)", kind, year, dest.name)
        return dest

    url = f"https://reporter.nih.gov/exporter/{segment}/download/{year}"
    logger.info("Downloading ExPORTER %s %s", kind, year)
    tmp = dest.with_suffix(".zip.part")
    with httpx.stream(
        "GET", url, headers={"User-Agent": USER_AGENT}, timeout=timeout, follow_redirects=True
    ) as r:
        r.raise_for_status()
        with open(tmp, "wb") as fh:
            for chunk in r.iter_bytes(1 << 16):
                fh.write(chunk)
    tmp.replace(dest)
    return dest


def extract_csv(zip_path: Path) -> Path:
    """Extract the single CSV from a zip (cached). Returns the CSV path."""
    with zipfile.ZipFile(zip_path) as z:
        csv_names = [n for n in z.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError(f"no CSV in {zip_path.name}")
        name = csv_names[0]
        out = zip_path.parent / name
        if not out.exists() or out.stat().st_size == 0:
            z.extract(name, path=zip_path.parent)
    return out


def ensure_csv(kind: str, year: int, force: bool = False) -> Path:
    """Download + extract the CSV for ``kind``/``year``; return the CSV path."""
    return extract_csv(download(kind, year, force=force))


# Back-compat aliases (projects).
def ensure_projects_csv(year: int, force: bool = False) -> Path:
    return ensure_csv("projects", year, force=force)
