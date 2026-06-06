# Knowledge Creation at NIH — design note

*Status: framing note, written alongside the v0 build. Captures the thesis the
platform has grown into and how it maps to a paper and an agent.*

## Thesis

NIH is the world's largest public funder of biomedical research, and its outputs
are unusually **public and traceable**: awards, abstracts, publications, citation
metrics, clinical trials, dataset accessions, drug approvals, and — at the far
end — population health outcomes. Each of these is reachable through an open API.

The claim of this work is not "we collected the data." It is a method:

> **You can measure how knowledge is created from public research investment —
> award → people → publications → datasets → trials → therapies → population
> health — *if and only if* every link carries its provenance and every analysis
> ships with its coverage and its bias.**

The interesting object is the **discipline**, not the dataset. Most "research ROI"
metrics are gameable or quietly wrong; the contribution here is a way to measure
the arc that refuses those failure modes.

## The measurement arc

The platform reconstructs, with a typed provenance-bearing edge at every hop:

```
  $ (RePORTER)
    → people / institutions          (RePORTER project fields)
    → publications                   (RePORTER authoritative pub links)
        · impact                     (iCite Relative Citation Ratio)
        · datasets                   (accession mining — inferred)
    → clinical trials                (CT.gov self-reported grant numbers)
    → approved therapies             (openFDA labels)
    → population health outcome      (CDC mortality / prevalence)
```

This is the design doc's Input → Process → Output → Reuse → Translation chain,
made concrete and queryable.

## Methodological principles (the paper's real argument)

1. **Authoritative vs inferred, always labeled.** A RePORTER publication link and
   an NLP-mined dataset accession are *not* the same evidence. Every edge carries
   `authoritative: bool`, `method`, `confidence`, and an `evidence_pointer`.
   Downstream tools never silently mix the two.
2. **Coverage ships with every analysis.** Numerator/denominator per edge type,
   stratified by IC, mechanism, and award age. An estimate without its coverage
   is treated as incomplete, not as truth.
3. **No gameable superlatives.** Productivity (pubs-per-$) is split by mechanism
   family so shared-facility cores can't win on borrowed acknowledgments; the
   health pulse is a *juxtaposition* and the tool refuses to compute a "return
   per death averted."
4. **Conservative identity resolution.** Disambiguation never merges distinct PIs
   with similar names; failing to merge beats a wrong merge in a funding graph.
5. **Determinism first, agent second.** Every capability is a typed, testable
   function callable from a CLI or notebook. An LLM orchestrates these tools; it
   does not replace them.

## The agent

The tools are the trustworthy substrate; the agent is the planner. Wrapped as an
**MCP server**, a question like *"map the translation lag of the NIH diabetes
portfolio and where its linkage is weakest"* decomposes into deterministic calls:
`search_projects → link_award_publications → find_trials_for_grant →
condition_pulse → coverage_report`. The agent sequences and narrates; the numbers
remain auditable. This is the "tool-first, chatbot-later" bet — the v0 library is
already clean enough to drive this way (see `notebooks/`).

## Paper outline (working)

1. **Motivation** — public research investment is traceable but rarely measured
   end-to-end without gameable metrics.
2. **Data & methods** — the open sources; the typed-edge linkage model; coverage
   and bias auditing.
3. **The arc, demonstrated** — a few portfolios (e.g. PFAS cardiometabolic,
   GLP-1, AI-in-biomedicine) traced from dollars to outcomes.
4. **Where measurement fails honestly** — recall ceilings, attribution limits.
5. **An agentic interface** — the MCP layer as a usable instrument for the NIH
   Director / ORIVA office.

## Limitations to lead with (a reviewer will raise all of these)

- **Recall ceiling.** Linkage is largely *authoritative-only*; RePORTER's
  publication attribution is known to be incomplete and biased. High precision,
  not-yet-high recall.
- **Crosswalk scope.** Outcomes map through ~10 canonical conditions (the NCHS
  leading causes); rare diseases and basic science that doesn't map to a leading
  cause are invisible to the pulse.
- **No population-level causal attribution.** Mortality reflects decades of lag,
  countless non-NIH drivers, and reactive funding. Juxtaposition only.
- **Bounded sampling.** Portfolio analyses cap the awards they deep-enrich and
  must disclose it (no silent truncation).

## Future direction: the basic → preclinical → clinical chain (penciled in)

Our current award→trial edge is the **direct** one (a trial self-reports the
grant number). It captures grants that *fund* trials but misses the harder,
more interesting link: a **basic-science grant** (e.g. a CRISPR mechanism study)
that *enabled* a therapy years later. Bridging that translational chain is the
deepest version of "knowledge creation," and a genuinely hard, inferred,
multi-hop problem. Candidate signals to combine (each weak alone):

1. **Citation chains (VALIDATED — the most tractable route).** iCite already
   precomputes the hard part: each paper's record carries `cited_by_clin` — the
   list of *clinical* articles that cite it — plus `apt` (Approximate Potential
   to Translate) and the Triangle-of-Biomedicine coordinates. So the chain is
   mostly a lookup, not an inference:
   `award → publication (publinks) → cited_by_clin (iCite) → clinical paper →
   trial (CT.gov reference PMIDs)`. Verified live: the foundational CRISPR paper
   (PMID 22745249) has 11,510 citers and a precomputed clinical-citer list — a
   concrete basic→clinical lineage with no NLP needed.
2. **iCite translational metrics** — APT and the human/animal/molecular Triangle
   of Biomedicine give a per-paper basic↔clinical position to weight the chain.
3. **Shared-target inference** — gene/target/intervention overlap between a
   grant's topic (e.g. a CRISPR-edited gene) and a trial's intervention.
4. **PI / lab continuity** — the same lab's later translational grants and trials.

These would be **inferred, low-confidence edges** (`authoritative=False`) with an
explicit provenance trail — exactly what the linkage layer's confidence/coverage
model is built to carry. Worth scoping as a Task 8 (meta-research) "translation
latency / lineage" capability once the citation graph is loaded at scale.

## Status of the build (v0)

Connectors (RePORTER, PubMed, iCite, ClinicalTrials.gov, CDC, openFDA); a typed
linkage layer (authoritative award→publication and award→trial edges, accession
extraction, disambiguation, coverage/bias audit); a NetworkX knowledge graph;
and analysis tools (pubs-per-$ productivity, the condition health pulse). Next:
the portfolio brief generator (synthesis), then the meta-research module.
