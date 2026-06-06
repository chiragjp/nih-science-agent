# nih-science-agent

A tool-first AI-for-science platform that maps NIH-funded research from awards to discoveries.

Public-first prototype. See [`../NIH_Science_Agent_Starter_with_MetaResearch.md`](../NIH_Science_Agent_Starter_with_MetaResearch.md) for the full vision and design — primary audience is the NIH Director and the proposed ORIVA office (DAIBR + DNICEATM/NICEATM).

## Status

| Task | Description | State |
|---|---|---|
| 1 | Repo scaffold (Python 3.11+, pydantic, httpx, duckdb, typer, pytest) | ✅ |
| 2 | RePORTER connector with normalization, caching, and tests | ✅ |
| 3 | PubMed + iCite connectors | ✅ |
| 4 | ClinicalTrials.gov connector | ✅ |
| 5 | Graph builder | ✅ NetworkX; award↔PI/IC/FOA/pub/trial/topic edges |
| 5b | Linkage layer (provenance + coverage) | ✅ edges, accession extraction, disambiguation, coverage/bias audit |
| 5c | Population health outcomes (CDC/FDA) | ✅ condition crosswalk + funding→outcome "pulse" |
| 5d | ExPORTER bulk → DuckDB | ✅ FY2000–2025 awards + 6.1M pub links; concentration, scaled productivity |
| 5e | FOA/RFA + NIH Data Book | ✅ grants.gov FOA resolution, RFA-vs-PA productivity, success rates |
| 6 | Brief generator | ✅ 11-section portfolio brief → Markdown |
| 7 | Tests + benchmark sets | ✅ 8 curated timely topics, expectation checks |
| 8 | Meta-research module | ✅ diminishing-returns, translation lineage, latency, redundancy, open-science, meta-brief |
| 9 | Preclinical / NAMs connectors and tools | 🛠️ NICEATM ICE connector + NAM portfolio map |

## Installation

Requires Python 3.11+. Homebrew Python 3.12 works.

```sh
make install   # uses python3.12 by default; override with `make install PY=python3.11`
```

Or manually:

```sh
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
```

## Quickstart

```sh
# Search NIH RePORTER for awards
nih-agent awards search "PFAS proteomics" --years 2015:2025 --ic NIEHS

# Search by principal investigator (use a full name to disambiguate;
# a bare last name matches every PI with that surname)
nih-agent awards search --pi "Purvesh Khatri"
nih-agent awards search --pi-id 9608896          # unambiguous, by RePORTER profile id

# Fetch a single project by project number
nih-agent awards get R01ES032470

# Search PubMed, optionally attaching iCite citation metrics (RCR, NIH percentile)
nih-agent pubs search "CRISPR gene editing" --limit 5 --metrics

# Fetch iCite metrics for specific PMIDs
nih-agent pubs metrics 22745249 36656942

# Link an award to the publications NIH attributes to it (authoritative edges),
# enriched with PubMed metadata and iCite metrics
nih-agent awards pubs R01ES032470 --metrics

# Rank a bounded grant population by linked publications per $1M
# (mechanism-family split; discloses population size and any cap)
nih-agent awards productivity --ic NIAID -m DP2 --years 2015:2024 --family research

# Search ClinicalTrials.gov, and fetch a single study by NCT id
nih-agent trials search --condition asthma --intervention mepolizumab --has-results
nih-agent trials get NCT04280705

# Build a knowledge graph around an award (PIs, institution, IC, FOA, topics,
# linked publications, and NIH-funded clinical trials), then summarize it
nih-agent graph build R01DK075877

# Report linkage coverage over a portfolio, and audit bias by IC/mechanism/age
nih-agent graph coverage --ic NIAID -m DP2 --years 2015:2024
nih-agent graph coverage "organoid" --years 2016:2022 --stratify ic --predicate produced

# Juxtapose NIH funding with the nation's health outcome for a condition
# (funding · publications · trials · FDA approvals · mortality trend)
nih-agent pulse conditions          # list the condition crosswalk
nih-agent pulse show diabetes --years 2008:2017

# Generate a synthesis portfolio brief (composition → outputs → translation →
# outcome → coverage & caveats), rendered as Markdown
nih-agent brief "diabetes" --years 2016:2020 --ic NIDDK --out brief.md

# Reproduce Open Mike's "diminishing returns": output-per-$ vs PI grant support
nih-agent meta diminishing-returns "beta cell" --ic NIDDK --years 2014:2015

# Trace a basic-science grant's reach into clinical work via the citation graph
# (award → publications → clinical citers → translation latency)
nih-agent meta translation R01GM094780
nih-agent meta translation-scan "GWAS" --years 2008:2014   # rank grants by clinical reach

# Find topically near-duplicate grants in a portfolio (term overlap; flags cross-PI)
nih-agent meta redundancy "PFAS exposure" --years 2018:2023 --cross-pi

# Open-science signals for a grant, and a synthesizing meta-research brief
nih-agent meta open-science U01HG007437
nih-agent meta brief "exposome" --years 2016:2022

# Preclinical / NAMs (alternative methods): chemical bioactivity (NICEATM ICE)
# and the NIH NAM portfolio map across preclinical areas
nih-agent nams chemical 80-05-7                      # bisphenol A assay coverage
nih-agent nams portfolio --area ad_adrd --years 2018:2023

# Population-scale: load ExPORTER bulk award files + publication link tables into
# DuckDB, then run all-portfolio analyses with no API caps
nih-agent bulk build 2000:2025          # awards (2.1M rows)
nih-agent bulk build-pubs 2000:2025     # award→publication links (6.1M)
nih-agent bulk concentration --ic NCI --year 2021    # funding concentration + Gini
nih-agent bulk productivity --year 2010              # diminishing returns at scale
nih-agent bulk foa-types --ic NCI --year 2010        # output per $ by FOA type (RFA vs PA)
nih-agent bulk latency --years 2005:2012             # grant→first-pub latency (censored)

# Resolve a funding opportunity (grants.gov + NIH Guide), and pull the
# application-funnel success rates RePORTER can't provide (NIH Data Book)
nih-agent foa get RFA-CA-19-039
nih-agent databook success-rates --since 2014

# Run the curated benchmark topics (checks expected kinds of results, not IDs)
nih-agent benchmark list
nih-agent benchmark run glp1ra

# Run tests (no network required)
make test
```

Responses are cached under `data/cache/reporter/`. Pass `--no-cache` to bypass the cache for a single call, delete the directory to force a refresh, or run `make clean`.

## Layout

```
src/nih_science_agent/
  config.py              # runtime settings + cache paths
  logging.py             # logging setup
  cli.py                 # typer-based CLI (entry point: nih-agent)
  connectors/            # typed API clients
    _http.py             # shared cached httpx client base
    reporter.py          # NIH RePORTER v2 ✅
    pubmed.py            # NCBI E-utilities (esearch + esummary) ✅
    icite.py             # iCite citation metrics (RCR) ✅
    clinicaltrials.py    # ClinicalTrials.gov v2 ✅
    cdc.py               # CDC Socrata: mortality + PLACES prevalence ✅
    fda.py               # openFDA drug approvals ✅
    foa.py               # grants.gov FOA/RFA resolution ✅
    databook.py          # NIH Data Book success rates (aggregate reference) ✅
    nams.py              # NCATS Tox21 + NICEATM ICE (Task 9)
    ...
  linkage/               # first-class linkage layer — typed provenance edges ✅
  graph/                 # NetworkX knowledge graph + award-centered builder ✅
  storage/               # ExPORTER bulk download + DuckDB analytics store ✅
  tools/                 # productivity ✅, conditions ✅, pulse ✅, briefs ✅, meta-research ✅
docs/                    # design notes (knowledge-creation-at-nih.md) ✅
notebooks/               # Quarto (.qmd) use-case walkthroughs ✅
tests/                   # 81 offline tests (no network) ✅
```

## Design notes

- **Tool-first, not chat-first.** Every connector and tool is callable from the CLI; LLMs can orchestrate later via MCP.
- **Public-first.** The v0 build uses only public APIs and metadata — no controlled-access data leaves the platform's deterministic tools.
- **Linkage as a first-class layer.** Every edge between awards, publications, trials, datasets, methods, and regulatory acceptance carries provenance, method, and confidence; coverage is reported alongside every analysis. See §5A of the design doc.
