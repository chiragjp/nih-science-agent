# Notebooks (native R)

Pure-R **R Markdown** notebooks — no Python. They call a small R implementation
of the platform's connectors (`../R/`, built on `httr2` + `jsonlite`) against the
live NIH APIs, with on-disk caching under `data/cache/`. The population-scale
notebooks (02, 03, 08) open the same `data/processed/nih.duckdb` the Python
`bulk build` produced — R's `duckdb` reads that file directly, no rebuild.

| Notebook | Shows |
|---|---|
| `01_awards_to_outputs.Rmd` | RePORTER awards & PI search → authoritative publication links (iCite RCR) → funded clinical trials |
| `02_linkage_coverage.Rmd` | Knowledge-graph edges, and **where they're missing** — link coverage stratified by IC, mechanism, and grant age (DuckDB) |
| `03_research_productivity.Rmd` | Publications per $1M at scale, split by mechanism family so cores/training don't contaminate the ranking (DuckDB) |
| `04_health_pulse.Rmd` | One condition's full arc — funding · trials · FDA approvals · mortality trend — as a *juxtaposition*, not attribution |
| `05_diminishing_returns.Rmd` | Output-per-$ vs PI grant support — Open Mike's diminishing-returns finding |
| `06_rfa_vs_productivity.Rmd` | RFA vs PA output per dollar by FOA type (DuckDB) |
| `07_discovery_to_translation.Rmd` | A grant's basic→clinical reach via the citation graph; GWAS / single-cell / exposomics compared |
| `08_population_scale.Rmd` | Funding concentration (Gini), output-per-$, FOA type, and grant→pub latency — no API caps (DuckDB) |

## The R implementation

`../R/` holds the native-R connectors that the notebooks source:

| file | provides |
|---|---|
| `cache.R` | cached GET/POST JSON helpers (`httr2`), `%||%` |
| `reporter.R` | NIH RePORTER v2: `reporter_search_projects`, `reporter_get_publications` |
| `icite.R` | iCite RCR (`icite_fetch_metrics`) + clinical-citation signals |
| `clinicaltrials.R` | ClinicalTrials.gov v2: search, `ct_find_trials_for_grant`, `ct_count_trials` |
| `cdc.R` | CDC Socrata: `cdc_mortality` (NCHS), `cdc_prevalence_mean` (PLACES) |
| `fda.R` | openFDA: `fda_drugs_for_indication` |
| `conditions.R` | condition crosswalk + `condition_pulse` (funding ⋅ trials ⋅ approvals ⋅ mortality) |
| `analysis.R` | `diminishing_returns`, `translation_scan` |
| `duckdb_store.R` | population-scale queries over the ExPORTER→DuckDB store (concentration, productivity-by-mechanism, coverage, FOA types, latency, panel) |
| `load.R` | sources all of the above (from the project root) |

## Render

```sh
Rscript -e 'rmarkdown::render("notebooks/01_awards_to_outputs.Rmd")'
```
or open in RStudio / Positron and **Knit**. Requires `httr2`, `jsonlite`,
`dplyr`, `tibble`, `ggplot2`, `digest`, `knitr`, `rmarkdown`, and (for 02/03/08)
`DBI` + `duckdb`. The DuckDB notebooks also need `data/processed/nih.duckdb`,
which the Python `nih-agent bulk build 2000:2025` / `build-pubs 2000:2025` create.
