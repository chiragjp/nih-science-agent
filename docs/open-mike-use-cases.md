# Open Mike use-case catalog

A catalog of the recurring **analytical questions** the NIH Extramural Nexus /
"Open Mike" blog (Mike Lauer, OER) poses, with a verdict on whether this
public-data platform can reproduce each. Doubles as a backlog of notebook use
cases.

> **Sourcing note.** The historical Open Mike archive at `nexus.od.nih.gov` now
> 301-redirects into the migrated `grants.nih.gov` news site, which collapsed the
> category/tag structure. Questions below were reconstructed from individual
> posts and their cited metrics (links in *Sources*). Reproducibility is judged
> against our public connectors only: RePORTER (awards), PubMed/iCite, CT.gov,
> CDC, openFDA.

## The boundary line (the key finding)

Public data cleanly covers the **funded portfolio → outputs → translation →
outcomes** arc. It does **not** cover the **application funnel** or **PI
demographics** — those require NIH-internal data (the NIH Data Book's aggregate
tables). That boundary is itself a result worth stating in the paper.

| Verdict | Meaning |
|---|---|
| ✅ | Reproducible now with our tools |
| ⚠️ | Partially / by inference, with caveats |
| ❌ | Needs NIH-internal data (applications, PII demographics) — not in public RePORTER |

## A. Funded portfolio  — ✅ our strength

| Question | Verdict | Tool |
|---|---|---|
| How much does NIH invest in a topic / IC / mechanism, by year? | ✅ | `brief`, `awards search` |
| Which institutions and PIs receive the most funding in an area? | ✅ | `brief` (top institutions/PIs) |
| How **concentrated** is funding among PIs (does the top X% hold most $)? | ✅ | aggregate RePORTER by PI |
| Research Commitment Index / Grant Support Index per PI (point system by activity code) | ✅ | computable from activity codes |
| How has the count of funded awards / unique PIs changed over time? | ✅ | per-year RePORTER counts |
| How many awards involve **foreign components** / organizations? | ⚠️ | RePORTER org country (subawards partial) |

## B. Productivity & citation impact — ✅ our strength

| Question | Verdict | Tool |
|---|---|---|
| What is the **RCR / citation impact** of NIH-funded work in an area? | ✅ | iCite via `awards pubs --metrics` |
| **Diminishing returns**: does output-per-dollar fall as grant support rises? ("Award or Reward?") | ✅ | `awards productivity` + RCR |
| Publications per dollar, by mechanism family | ✅ | `awards productivity` |
| Grant-size vs weighted-RCR productivity | ✅ | productivity tool + iCite |

## C. Translation & population outcomes — ✅ our novel extension

| Question | Verdict | Tool |
|---|---|---|
| How many clinical trials / approved drugs flow from a portfolio? | ✅ | `find_trials_for_grant`, openFDA |
| How does funding track the nation's health outcome for a condition? | ✅ | `pulse` (juxtaposition, not causal) |

> Open Mike rarely closes this loop to population outcomes — it's the platform's
> distinctive contribution.

## D. Application funnel — ❌ needs NIH Data Book

| Question | Verdict | Why |
|---|---|---|
| **Success / award rates** by year, IC, mechanism, career stage | ❌ | RePORTER exposes *funded* awards only — no application denominator |
| **Cumulative investigator funding rate** (5-yr window; 34.8%→37.6%, 2014–17) | ❌ | needs the applicant pool |
| Resubmission (A1) success rates | ❌ | application-level data |
| Number of applications / unique applicants | ❌ | applications not public |

## E. Workforce & demographics — ❌ mostly (privacy / internal)

| Question | Verdict | Why |
|---|---|---|
| **Average age at first R01**; PI age distribution | ❌ | PII; NIH publishes aggregate tables only |
| **ESI** counts supported per FY (1,423 FY24; 1,144 FY25) | ❌ | ESI status not in RePORTER |
| New-investigator **dropout** / time to next R01 (57% renew; ~5y dropout) | ⚠️ | inferable from per-PI award histories, noisy/right-censored |
| Funding & success rates by **race/ethnicity, gender** | ❌ | per-grant PII not public; aggregate only |

## F. Clinical research demographics — ⚠️

| Question | Verdict | Tool |
|---|---|---|
| Clinical-trial **enrollment** demographics (FY25 enrollment release) | ⚠️ | CT.gov results sections (uneven) |
| Diversity in trials reported to ClinicalTrials.gov | ⚠️ | CT.gov, partial |

## Candidate notebook use cases (the ✅/⚠️ rows)

1. **Funding concentration** — Lorenz curve / top-decile share of $ for an IC or topic.
2. **Diminishing returns** — reproduce "Award or Reward?": per-PI grant support
   (RCI/GSI) vs weighted RCR, showing the concavity.
3. **Citation impact of a portfolio** — RCR distribution vs the NIH benchmark of 1.0.
4. **Portfolio brief** — the `brief` command as a one-page OD memo (already built).
5. **Funding → outcome pulse** — the platform's novel extension (already built).
6. **Investigator persistence** (⚠️) — careful, right-censored look at award continuity.

## Novel platform use cases (beyond Open Mike)

### Discovery → translation scan (basic-science reach into the clinic)
**`nih-agent meta translation-scan <topic>`** ranks a portfolio's grants by how
far their discoveries reach clinical work, via the citation graph:
`award → publications → iCite cited_by_clin (clinical citers) → distinct clinical
papers reached + APT + latency`. Catches the basic grant that *enabled* a therapy
it never directly funded — the link the direct award→trial edge misses.

**Cross-technology finding (run live):** different discovery technologies reach
the clinic in different *shapes*, not just amounts:
- **GWAS** — most translated; reach concentrated in **pharmacogenomics** (anti-TNF
  response, drug transporters), APT up to 0.73 (e.g. K23DK097142, U19GM061390).
- **Single-cell** — younger, more basic; organoids and genetic modifiers, lower reach.
- **Exposomics** — reach runs through **infrastructure**: centers and cohorts
  (HERCULES P30ES019776, ECHO UH3OD023275/248, HHEAR U2CES026555) and training
  grants, *not* a drug pipeline. Clearest individual translational bet:
  K08ES028825 (APT 0.73, exposome → longitudinal clinical cohorts).

The platform makes this *characterizable from public data* — a paper-worthy
observation about how funding mechanisms and fields translate differently.
Caveats: reach is inferred (clinical-flagged citers ≠ therapies); center/training
grants inflate reach (umbrella many papers — APT helps separate); younger fields
are right-censored.

## Complementary data sources (to close the ❌ gaps)

- **NIH Data Book** (report.nih.gov/nihdatabook) — *aggregate* tables for the
  application funnel and workforce: success/award rates, application counts,
  cumulative investigator rate, ESI counts, average age at first R01, funding by
  IC/mechanism/year. Fills the ❌ rows — but it is pre-aggregated, **not joinable
  to individual grants**. Use as a reference/context layer, not a graph extension.
- **NIH Guide (FOA/RFA content)** — the grant→FOA *id* is already in ExPORTER
  (`foa_number`, 62% filled, 17,752 distinct). The FOA/RFA *content* is fetchable
  by a deterministic URL: `grants.nih.gov/grants/guide/{pa|rfa|par|notice}-files/{FOA}.html`
  (resolves even for expired FOAs — durable archive). Active opportunities also via
  the grants.gov Search2 API and grants.nih.gov/funding/explore-nih-opportunities.
  Enables a policy→outcome angle (do targeted RFAs produce different productivity
  than parent PAs?), joinable on the existing `foa_number` column.

## Sources

- [How Many Researchers, Revisited: Cumulative Investigator Funding Rates](https://nexus.od.nih.gov/all/2018/03/07/how-many-researchers-revisited-a-look-at-cumulative-investigator-funding-rates/)
- [Research Commitment Index: A New Tool for Describing Grant Support](https://nexus.od.nih.gov/all/2017/01/26/research-commitment-index-a-new-tool-for-describing-grant-support/)
- [NIH Support for Early Stage Investigators in FYs 2024 and 2025](https://grants.nih.gov/news-events/nih-extramural-nexus-news/2026/02/nih-support-for-early-stage-investigators-in-fys-2024-and-2025)
- [Early Stage Investigator Related Data](https://grants.nih.gov/policy-and-compliance/policy-topics/early-stage-investigators/related-data)
- [FY25 Enrollment Data from NIH-Supported Clinical Research](https://grants.nih.gov/news-events/nih-extramural-nexus-news/2026/05/fy25-enrollment-data-from-nih-supported-clinical-research-now-available)
- [Award or Reward? Which comes first, NIH funding or research impact? (preprint)](https://www.biorxiv.org/content/10.1101/193755.full.pdf)
- [Diminishing marginal returns on NIH grant funding to institutions (preprint)](https://www.biorxiv.org/content/10.1101/367847.full.pdf)
- [NIH Extramural Nexus (migrated)](https://grants.nih.gov/news-events/nih-extramural-nexus-news)
