"""PI and institution disambiguation.

Resolves investigator and organization mentions to stable identities. Strong
identifiers win: ORCID, then RePORTER PI ``profile_id`` (for PIs) or ROR (for
institutions). Only when no identifier is present do we fall back to a
normalized name (plus institution, for PIs).

Deliberately conservative: two mentions with *different* strong identifiers are
NEVER merged, even if their names are identical — silently collapsing distinct
people is the costliest error in a funding-attribution graph. The name fallback
preserves every token (initials included) so "John A Smith" and "John B Smith"
stay distinct.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")

# Institutional suffixes/qualifiers stripped before comparison.
_INST_STOPWORDS = {
    "the",
    "inc",
    "incorporated",
    "llc",
    "ltd",
    "university",
    "univ",
    "college",
    "school",
    "of",
    "at",
    "medicine",
    "medical",
    "center",
    "centre",
    "hospital",
    "institute",
    "institutes",
    "and",
}


def normalize_name(name: str) -> str:
    """Lower-case, strip punctuation, collapse whitespace; keep all tokens.

    Token order and initials are preserved on purpose — we would rather fail to
    merge a name variant than merge two different investigators.
    """
    s = _PUNCT_RE.sub(" ", (name or "").lower())
    return _WS_RE.sub(" ", s).strip()


def normalize_institution(name: str) -> str:
    """Normalize an organization name to a comparison key (suffix words removed)."""
    s = _PUNCT_RE.sub(" ", (name or "").lower())
    tokens = [t for t in s.split() if t and t not in _INST_STOPWORDS]
    return " ".join(tokens)


class PIMention(BaseModel):
    full_name: str
    profile_id: int | None = None
    orcid: str | None = None
    institution: str | None = None


class ResolvedPI(BaseModel):
    key: str  # canonical cluster id
    display_name: str
    profile_id: int | None = None
    orcid: str | None = None
    name_variants: list[str] = []
    institutions: list[str] = []
    mention_count: int = 0
    resolved_by: str  # "orcid" | "profile_id" | "name+institution" | "name"


class InstitutionMention(BaseModel):
    name: str
    ror_id: str | None = None


class ResolvedInstitution(BaseModel):
    key: str
    display_name: str
    ror_id: str | None = None
    name_variants: list[str] = []
    mention_count: int = 0
    resolved_by: str  # "ror" | "name"


def _pi_key(m: PIMention) -> tuple[str, str]:
    """Return (cluster_key, resolved_by) for a PI mention."""
    if m.orcid:
        return f"orcid:{m.orcid.strip().lower()}", "orcid"
    if m.profile_id is not None:
        return f"profile:{m.profile_id}", "profile_id"
    norm = normalize_name(m.full_name)
    if m.institution:
        return f"name:{norm}|inst:{normalize_institution(m.institution)}", "name+institution"
    return f"name:{norm}", "name"


def disambiguate_pis(mentions: list[PIMention]) -> list[ResolvedPI]:
    """Cluster PI mentions into resolved identities (order-stable)."""
    clusters: dict[str, ResolvedPI] = {}
    order: list[str] = []
    for m in mentions:
        key, resolved_by = _pi_key(m)
        rp = clusters.get(key)
        if rp is None:
            rp = ResolvedPI(
                key=key,
                display_name=m.full_name,
                profile_id=m.profile_id,
                orcid=m.orcid,
                resolved_by=resolved_by,
            )
            clusters[key] = rp
            order.append(key)
        if m.full_name and m.full_name not in rp.name_variants:
            rp.name_variants.append(m.full_name)
        if m.institution and m.institution not in rp.institutions:
            rp.institutions.append(m.institution)
        rp.mention_count += 1
    return [clusters[k] for k in order]


def _institution_key(m: InstitutionMention) -> tuple[str, str]:
    if m.ror_id:
        return f"ror:{m.ror_id.strip().lower()}", "ror"
    return f"name:{normalize_institution(m.name)}", "name"


def disambiguate_institutions(mentions: list[InstitutionMention]) -> list[ResolvedInstitution]:
    """Cluster institution mentions into resolved identities (order-stable)."""
    clusters: dict[str, ResolvedInstitution] = {}
    order: list[str] = []
    for m in mentions:
        key, resolved_by = _institution_key(m)
        ri = clusters.get(key)
        if ri is None:
            ri = ResolvedInstitution(
                key=key, display_name=m.name, ror_id=m.ror_id, resolved_by=resolved_by
            )
            clusters[key] = ri
            order.append(key)
        if m.name and m.name not in ri.name_variants:
            ri.name_variants.append(m.name)
        ri.mention_count += 1
    return [clusters[k] for k in order]
