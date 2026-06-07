# Portfolio synthesis report (native R).
#
# Assembles the full arc for one Institute/Center paired with one health
# condition into a single object the `portfolio_report.Rmd` renders:
#
#   Inputs     IC funding trend + concentration (Gini)         [DuckDB, pop-scale]
#   Outputs    linked publications + a sampled RCR read         [DuckDB + iCite]
#   Translate  FDA approvals + clinical trials for the condition [openFDA + CT.gov]
#   Outcome    age-adjusted mortality trend                     [CDC NCHS]
#   Coverage   linkage % by mechanism family + grant->pub latency [DuckDB]
#
# The IC lens (administrative portfolio) and the condition lens (population
# outcome) are DIFFERENT slices deliberately juxtaposed — never equated. The
# report carries that caveat explicitly.
#
# `condition` is OPTIONAL. Pass NULL (or "", "none", "-") for a cross-cutting
# Institute/Center with no single home condition (e.g. NIEHS, NIGMS, NHGRI):
# the report then shows the structural portfolio metrics only and omits the
# translation/outcome juxtaposition rather than forcing a misleading one.

build_portfolio_report <- function(ic, condition = NULL, years,
                                   db = "data/processed/nih.duckdb",
                                   rcr_sample = 250) {
  blank <- is.null(condition) || !nzchar(trimws(condition)) ||
    tolower(trimws(condition)) %in% c("none", "-", "na")
  cond <- if (blank) NULL else resolve_condition(condition)
  if (!blank && is.null(cond))
    stop(sprintf("condition '%s' not in the crosswalk; see list_conditions() (or pass none)",
                 condition))

  con <- bulk_connect(db)
  on.exit(bulk_disconnect(con), add = TRUE)

  # -- Inputs (IC, population-scale) ----------------------------------- #
  trend <- bulk_funding_trend(con, ic = ic, years = years)
  conc  <- bulk_concentration(con, ic = ic, year = max(years), top_n = 5)

  # -- Outputs (IC pubs + a bounded RCR read) -------------------------- #
  pmids <- bulk_sample_pmids(con, ic = ic, years = years, n = rcr_sample)
  rcr <- if (length(pmids)) icite_fetch_metrics(pmids) else tibble::tibble()
  prod <- bulk_productivity_by_mechanism(con, ic = ic, years = years)

  # -- Coverage + latency (IC) ----------------------------------------- #
  cov <- bulk_coverage_stratified(con, by = "family", ic = ic, years = years, min_grants = 1)
  lat <- bulk_latency(con, years = years, ic = ic)

  # -- Translation + Outcome (condition, live APIs) — only if a condition #
  trials <- NA_integer_; trials_res <- NA_integer_
  drugs <- list(total = NA_integer_, drugs = character(0))
  mort <- tibble::tibble(); aadr_change <- NA_real_
  if (!is.null(cond)) {
    trials     <- ct_count_trials(condition = cond$label)
    trials_res <- ct_count_trials(condition = cond$label, has_results = TRUE)
    if (!is.null(cond$fda_indication))
      drugs <- fda_drugs_for_indication(cond$fda_indication, limit = 10)
    if (!is.null(cond$cdc_cause_name))
      mort <- cdc_mortality(cond$cdc_cause_name, years = years)
    if (nrow(mort) >= 2) {
      rated <- mort[!is.na(mort$aadr), ]
      if (nrow(rated) >= 2 && rated$aadr[1] != 0)
        aadr_change <- (rated$aadr[nrow(rated)] - rated$aadr[1]) / rated$aadr[1] * 100
    }
  }

  list(
    ic = toupper(ic), condition = cond, has_condition = !is.null(cond), years = years,
    funding_trend = trend, concentration = conc,
    rcr = rcr, n_pmids_sampled = length(pmids), productivity = prod,
    coverage = cov, latency = lat,
    trials = trials, trials_with_results = trials_res,
    drugs = drugs, mortality = mort, mortality_aadr_change_pct = aadr_change,
    caveat = CONDITION_CAVEAT)
}
