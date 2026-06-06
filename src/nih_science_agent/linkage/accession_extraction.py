"""Pattern-based extraction of data-repository accessions from text.

Scans free text (abstracts, and PMC full text where licensing permits) for
accession identifiers from dbGaP, GEO, SRA/INSDC, BioProject/BioSample, ImmPort,
ArrayExpress, and TCGA. Each hit records its surface form, a surrounding text
window, and a confidence score, and becomes an **inferred** PUBLICATION→DATASET
edge (never authoritative — these are NLP guesses, not asserted links).

Precision over recall: patterns are anchored and specific so that gene symbols
and ordinary tokens that merely resemble accessions are not matched.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from pydantic import BaseModel

from nih_science_agent.linkage.edges import DATASET, PUBLICATION, Edge

# Each repository pattern is anchored with word boundaries and requires the
# digit run that real accessions carry, so bare gene-like tokens never match.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("dbGaP", re.compile(r"\bphs\d{6}(?:\.v\d+\.p\d+)?\b")),
    ("GEO", re.compile(r"\bG(?:SE|SM|PL|DS)\d{3,}\b")),
    ("SRA", re.compile(r"\b(?:SR[APRSXZ]|ERR|ERX|ERP|ERS|DRR|DRX|DRP)\d{4,}\b")),
    ("BioProject", re.compile(r"\bPRJ(?:NA|EB|DB)\d{3,}\b")),
    ("BioSample", re.compile(r"\bSAM(?:N|EA|D)\d{4,}\b")),
    ("ImmPort", re.compile(r"\bSDY\d{2,}\b")),
    ("ArrayExpress", re.compile(r"\bE-[A-Z]{4}-\d+\b")),
    ("TCGA", re.compile(r"\bTCGA-[0-9A-Z]{2}-[0-9A-Z]{4}\b")),
]

# Context words that make a nearby token much more likely a real deposit.
_CONTEXT_CUES = (
    "accession",
    "deposit",
    "available",
    "archiv",
    "repositor",
    "database",
    "under",
    "geo",
    "dbgap",
    "sequence read archive",
    "data have been",
    "data are",
    "data is",
    "raw data",
)

_CONTEXT_WINDOW = 60


class AccessionMatch(BaseModel):
    accession: str  # normalized surface form (upper-cased except dbGaP 'phs')
    repository: str
    surface_form: str  # exactly as it appeared
    context: str  # surrounding text window
    confidence: float
    start: int


def _utcnow_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _normalize(repo: str, surface: str) -> str:
    # dbGaP study ids are conventionally lower-case 'phs'; others upper-case.
    return surface if repo == "dbGaP" else surface.upper()


def extract_accessions(text: str, context_window: int = _CONTEXT_WINDOW) -> list[AccessionMatch]:
    """Extract repository accessions from ``text``.

    Returns one :class:`AccessionMatch` per occurrence, de-duplicated by
    (repository, normalized accession) keeping the highest-confidence hit.
    """
    if not text:
        return []

    best: dict[tuple[str, str], AccessionMatch] = {}
    for repo, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            surface = m.group(0)
            start = m.start()
            ctx = text[max(0, start - context_window) : m.end() + context_window]
            ctx_low = ctx.lower()
            confidence = 0.9
            if any(cue in ctx_low for cue in _CONTEXT_CUES):
                confidence = min(0.99, confidence + 0.07)
            match = AccessionMatch(
                accession=_normalize(repo, surface),
                repository=repo,
                surface_form=surface,
                context=ctx.strip(),
                confidence=confidence,
                start=start,
            )
            key = (repo, match.accession)
            if key not in best or match.confidence > best[key].confidence:
                best[key] = match

    return sorted(best.values(), key=lambda a: a.start)


def accession_edges(
    pmid: str, matches: list[AccessionMatch], retrieved_at: str | None = None
) -> list[Edge]:
    """Convert extracted accessions into inferred PUBLICATION→DATASET edges."""
    ts = retrieved_at or _utcnow_iso()
    edges: list[Edge] = []
    for m in matches:
        edges.append(
            Edge(
                subject=f"{PUBLICATION}:{pmid}",
                predicate="mentions_dataset",
                object=f"{DATASET}:{m.repository}:{m.accession}",
                source="text_mining",
                method="accession_regex",
                authoritative=False,  # inferred, not asserted by any source
                confidence=m.confidence,
                evidence_pointer=f"pmid:{pmid} ~ '{m.surface_form}'",
                retrieved_at=ts,
            )
        )
    return edges
