"""Condition crosswalk — the connective tissue between funding and outcomes.

Maps the messy vocabularies of awards (free-text terms, ICs), trials (conditions),
and publications onto a small set of canonical disease conditions, each carrying
the handles needed to pull outcomes: a CDC leading-cause name, a PLACES prevalence
measure, an openFDA indication term, and the primary NIH IC.

v0 covers the conditions that have both a strong CDC outcome signal and a large
NIH portfolio (largely the NCHS leading causes of death). It is intentionally a
curated dictionary, not an ML matcher — transparent and auditable. Coverage of
this crosswalk is itself reportable (some funding maps to no condition here).
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class Condition(BaseModel):
    key: str
    label: str
    keywords: list[str] = Field(default_factory=list)
    cdc_cause_name: str | None = None  # NCHS leading-cause name (mortality)
    places_measure: str | None = None  # PLACES prevalence measure
    fda_indication: str | None = None  # openFDA indication search term
    primary_ic: str | None = None  # main NIH Institute/Center


CONDITIONS: list[Condition] = [
    Condition(
        key="heart_disease",
        label="Heart disease",
        keywords=[
            "heart disease",
            "cardiovascular",
            "coronary",
            "myocardial",
            "cardiac",
            "atherosclerosis",
            "ischemic heart",
        ],
        cdc_cause_name="Heart disease",
        places_measure="Coronary heart disease among adults",
        fda_indication="heart failure",
        primary_ic="NHLBI",
    ),
    Condition(
        key="cancer",
        label="Cancer",
        keywords=[
            "cancer",
            "carcinoma",
            "tumor",
            "tumour",
            "oncolog",
            "neoplasm",
            "malignan",
            "leukemia",
            "lymphoma",
            "melanoma",
        ],
        cdc_cause_name="Cancer",
        places_measure="Cancer (non-skin) or melanoma among adults",
        fda_indication="cancer",
        primary_ic="NCI",
    ),
    Condition(
        key="diabetes",
        label="Diabetes",
        keywords=[
            "diabetes",
            "diabetic",
            "insulin",
            "glycemic",
            "hyperglycemia",
            "type 2 diabetes",
            "type 1 diabetes",
        ],
        cdc_cause_name="Diabetes",
        places_measure="Diagnosed diabetes among adults",
        fda_indication="type 2 diabetes",
        primary_ic="NIDDK",
    ),
    Condition(
        key="stroke",
        label="Stroke",
        keywords=["stroke", "cerebrovascular", "ischemic stroke"],
        cdc_cause_name="Stroke",
        places_measure="Stroke among adults",
        fda_indication="ischemic stroke",
        primary_ic="NINDS",
    ),
    Condition(
        key="alzheimers",
        label="Alzheimer's disease",
        keywords=[
            "alzheimer",
            "dementia",
            "neurodegenerative",
            "amyloid",
            "tauopathy",
            "cognitive decline",
        ],
        cdc_cause_name="Alzheimer's disease",
        fda_indication="Alzheimer's disease",
        primary_ic="NIA",
    ),
    Condition(
        key="copd",
        label="Chronic lower respiratory disease (COPD)",
        keywords=[
            "copd",
            "chronic obstructive",
            "emphysema",
            "chronic lower respiratory",
            "chronic bronchitis",
        ],
        cdc_cause_name="CLRD",
        places_measure="Chronic obstructive pulmonary disease among adults",
        fda_indication="chronic obstructive pulmonary disease",
        primary_ic="NHLBI",
    ),
    Condition(
        key="kidney_disease",
        label="Kidney disease",
        keywords=["kidney", "renal", "nephro", "chronic kidney", "ckd", "end-stage renal"],
        cdc_cause_name="Kidney disease",
        places_measure="Chronic kidney disease among adults",
        fda_indication="chronic kidney disease",
        primary_ic="NIDDK",
    ),
    Condition(
        key="suicide",
        label="Suicide / self-harm",
        keywords=["suicide", "self-harm", "self harm", "suicidal"],
        cdc_cause_name="Suicide",
        places_measure="Depression among adults",
        fda_indication="major depressive disorder",
        primary_ic="NIMH",
    ),
    Condition(
        key="influenza_pneumonia",
        label="Influenza and pneumonia",
        keywords=["influenza", "pneumonia", "respiratory infection"],
        cdc_cause_name="Influenza and pneumonia",
        fda_indication="influenza",
        primary_ic="NIAID",
    ),
    Condition(
        key="overdose_injury",
        label="Unintentional injury / overdose",
        keywords=[
            "overdose",
            "opioid",
            "substance use",
            "drug abuse",
            "addiction",
            "unintentional injur",
        ],
        cdc_cause_name="Unintentional injuries",
        fda_indication="opioid use disorder",
        primary_ic="NIDA",
    ),
]

_BY_KEY = {c.key: c for c in CONDITIONS}


def list_conditions() -> list[Condition]:
    return list(CONDITIONS)


def get_condition(key: str) -> Condition | None:
    return _BY_KEY.get(key.lower())


def resolve_condition(text: str) -> Condition | None:
    """Resolve free text to a single condition: exact key, then keyword match."""
    if not text:
        return None
    t = text.lower().strip()
    if t in _BY_KEY:
        return _BY_KEY[t]
    matches = match_conditions(text)
    return matches[0] if matches else None


def match_conditions(text: str) -> list[Condition]:
    """Return every condition whose keywords appear in ``text`` (order-stable)."""
    if not text:
        return []
    t = text.lower()
    return [c for c in CONDITIONS if any(kw in t for kw in c.keywords)]
