# Condition crosswalk + pulse (native R).
#
# Maps free-text disease names onto a small curated dictionary of canonical
# conditions, each carrying the handles needed to pull outcomes: a CDC
# leading-cause name, an openFDA indication term, and the primary NIH IC.
# `condition_pulse()` juxtaposes NIH inputs (funding) against the nation's
# health outcome (mortality) for one condition. This is a *juxtaposition*,
# NOT a causal attribution — see CONDITION_CAVEAT.

CONDITION_CAVEAT <- paste(
  "Juxtaposition only - NOT causal attribution. Population mortality reflects",
  "10-30y research lag and many non-NIH drivers; funding often rises as a",
  "problem worsens. Do not infer NIH impact per death from these series.")

CONDITIONS <- list(
  list(key="heart_disease", label="Heart disease",
       keywords=c("heart disease","cardiovascular","coronary","myocardial","cardiac",
                  "atherosclerosis","ischemic heart"),
       cdc_cause_name="Heart disease", fda_indication="heart failure", primary_ic="NHLBI"),
  list(key="cancer", label="Cancer",
       keywords=c("cancer","carcinoma","tumor","tumour","oncolog","neoplasm",
                  "malignan","leukemia","lymphoma","melanoma"),
       cdc_cause_name="Cancer", fda_indication="cancer", primary_ic="NCI"),
  list(key="diabetes", label="Diabetes",
       keywords=c("diabetes","diabetic","insulin","glycemic","hyperglycemia",
                  "type 2 diabetes","type 1 diabetes"),
       cdc_cause_name="Diabetes", fda_indication="type 2 diabetes", primary_ic="NIDDK"),
  list(key="stroke", label="Stroke",
       keywords=c("stroke","cerebrovascular","ischemic stroke"),
       cdc_cause_name="Stroke", fda_indication="ischemic stroke", primary_ic="NINDS"),
  list(key="alzheimers", label="Alzheimer's disease",
       keywords=c("alzheimer","dementia","neurodegenerative","amyloid","tauopathy",
                  "cognitive decline"),
       cdc_cause_name="Alzheimer's disease", fda_indication="Alzheimer's disease",
       primary_ic="NIA"),
  list(key="copd", label="Chronic lower respiratory disease (COPD)",
       keywords=c("copd","chronic obstructive","emphysema","chronic lower respiratory",
                  "chronic bronchitis"),
       cdc_cause_name="CLRD", fda_indication="chronic obstructive pulmonary disease",
       primary_ic="NHLBI"),
  list(key="kidney_disease", label="Kidney disease",
       keywords=c("kidney","renal","nephro","chronic kidney","ckd","end-stage renal"),
       cdc_cause_name="Kidney disease", fda_indication="chronic kidney disease",
       primary_ic="NIDDK"),
  list(key="suicide", label="Suicide / self-harm",
       keywords=c("suicide","self-harm","self harm","suicidal"),
       cdc_cause_name="Suicide", fda_indication="major depressive disorder",
       primary_ic="NIMH"),
  list(key="influenza_pneumonia", label="Influenza and pneumonia",
       keywords=c("influenza","pneumonia","respiratory infection"),
       cdc_cause_name="Influenza and pneumonia", fda_indication="influenza",
       primary_ic="NIAID"),
  list(key="overdose_injury", label="Unintentional injury / overdose",
       keywords=c("overdose","opioid","substance use","drug abuse","addiction",
                  "unintentional injur"),
       cdc_cause_name="Unintentional injuries", fda_indication="opioid use disorder",
       primary_ic="NIDA")
)
names(CONDITIONS) <- vapply(CONDITIONS, function(c) c$key, character(1))

list_conditions <- function() {
  tibble::tibble(
    key      = vapply(CONDITIONS, function(c) c$key, character(1)),
    label    = vapply(CONDITIONS, function(c) c$label, character(1)),
    cdc      = vapply(CONDITIONS, function(c) c$cdc_cause_name %||% NA_character_, character(1)),
    fda      = vapply(CONDITIONS, function(c) c$fda_indication %||% NA_character_, character(1)),
    ic       = vapply(CONDITIONS, function(c) c$primary_ic %||% NA_character_, character(1)))
}

# Resolve free text to one condition: exact key, then first keyword match.
resolve_condition <- function(text) {
  if (is.null(text) || !nchar(text)) return(NULL)
  t <- tolower(trimws(text))
  if (t %in% names(CONDITIONS)) return(CONDITIONS[[t]])
  for (c in CONDITIONS)
    if (any(vapply(c$keywords, function(kw) grepl(kw, t, fixed = TRUE), logical(1))))
      return(c)
  NULL
}

# Juxtapose NIH funding against the nation's outcome for one condition.
# Funding is queried per fiscal year (a single multi-year search sorts
# newest-first and exhausts the cap inside the latest year, hiding the trend).
condition_pulse <- function(condition, years = 2008:2017,
                            state = "United States", per_year_cap = 1000) {
  cond <- resolve_condition(condition)
  if (is.null(cond)) return(NULL)
  fq <- cond$keywords[1]

  is_floor <- FALSE; all_cores <- character(0); fy_rows <- list()
  for (y in years) {
    projects <- reporter_search_projects(query = fq, fiscal_years = y, limit = per_year_cap)
    if (length(projects) >= per_year_cap) is_floor <- TRUE
    cores <- unique(vapply(projects, function(p) p$core_project_num %||% NA_character_, character(1)))
    cores <- cores[!is.na(cores)]
    all_cores <- union(all_cores, cores)
    fund <- sum(vapply(projects, function(p) as.numeric(p$total_cost %||% 0), numeric(1)), na.rm = TRUE)
    fy_rows[[length(fy_rows) + 1]] <- tibble::tibble(
      fiscal_year = as.integer(y), total_funding = fund, award_count = length(cores))
  }
  funding_by_year <- dplyr::bind_rows(fy_rows)

  trial_count   <- ct_count_trials(condition = cond$label)
  trials_results <- ct_count_trials(condition = cond$label, has_results = TRUE)

  drugs <- if (!is.null(cond$fda_indication))
    fda_drugs_for_indication(cond$fda_indication, limit = 10) else list(total = 0L, drugs = character(0))

  mortality <- if (!is.null(cond$cdc_cause_name))
    cdc_mortality(cond$cdc_cause_name, state = state, years = years) else tibble::tibble()
  aadr_change <- NA_real_
  if (nrow(mortality) >= 2) {
    rated <- mortality[!is.na(mortality$aadr), ]
    if (nrow(rated) >= 2 && rated$aadr[1] != 0)
      aadr_change <- (rated$aadr[nrow(rated)] - rated$aadr[1]) / rated$aadr[1] * 100
  }

  list(
    condition_key = cond$key, condition_label = cond$label, years = years,
    funding_by_year = funding_by_year,
    total_funding = sum(funding_by_year$total_funding),
    distinct_awards = length(all_cores), funding_is_floor = is_floor,
    trial_count = trial_count, trials_with_results = trials_results,
    approved_drug_count = drugs$total, example_drugs = drugs$drugs,
    mortality = mortality, mortality_aadr_change_pct = aadr_change,
    caveat = CONDITION_CAVEAT)
}
