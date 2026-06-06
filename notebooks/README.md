# Notebooks

Quarto (`.qmd`) notebooks demonstrating the platform's use cases end to end.
Each runs the library's Python API directly — the same deterministic functions
the `nih-agent` CLI calls.

| Notebook | Shows |
|---|---|
| `01_awards_to_outputs.qmd` | RePORTER awards & PI search → authoritative publication links (iCite RCR) → funded clinical trials |
| `02_knowledge_graph_and_coverage.qmd` | Assembling an award-centered knowledge graph; per-edge-type coverage and bias auditing |
| `03_research_productivity.qmd` | Publications-per-dollar with the mechanism-family split; why naïve rankings mislead |
| `04_health_pulse.qmd` | Juxtaposing NIH funding with national health outcomes (CDC mortality, openFDA approvals) |
| `05_diminishing_returns.qmd` | Reproducing Open Mike's diminishing-returns finding (output-per-$ vs PI grant support) |
| `06_rfa_vs_productivity.qmd` | RFA vs PA productivity — confounded raw means corrected by a controlled regression (needs bulk DuckDB) |
| `07_discovery_to_translation.qmd` | Tracing a grant's basic→clinical reach via the citation graph; GWAS / single-cell / exposomics compared |
| `08_population_scale.qmd` | All-NIH analyses on the ExPORTER→DuckDB store: concentration, output-per-$, FOA type, latency (needs bulk build) |

## Rendering

These need [Quarto](https://quarto.org/docs/get-started/) plus a Jupyter kernel
backed by the project venv:

```sh
# one-time: Jupyter support in the venv (Quarto installed separately)
source .venv/bin/activate
pip install -e '.[notebooks]'
python -m ipykernel install --user --name nih-science-agent

# render one notebook (or `quarto render notebooks/` for all)
quarto render notebooks/01_awards_to_outputs.qmd
```

The first run hits live public APIs (NIH RePORTER, NCBI, ClinicalTrials.gov,
CDC, openFDA); responses cache under `data/cache/`, so re-renders are fast and
work offline. No API keys are required.
