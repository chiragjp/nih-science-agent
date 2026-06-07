# ClinicalTrials.gov v2 connector (native R).

.ct_grant_re <- "[A-Z][A-Z0-9][0-9][A-Z]{2}[0-9]{6}"

ct_extract_nih_grants <- function(ident) {
  grants <- character(0)
  for (sid in ident$secondaryIdInfos %||% list()) {
    typ <- toupper(sid$type %||% "")
    is_nih <- typ == "NIH" || grepl("NIH", toupper(sid$domain %||% "")) ||
              (typ == "OTHER_GRANT" && grepl("NIH", toupper(sid$id %||% "")))
    if (!is_nih) next
    m <- regmatches(toupper(gsub(" ", "", sid$id %||% "")),
                    regexpr(.ct_grant_re, toupper(gsub(" ", "", sid$id %||% ""))))
    if (length(m) && nchar(m)) grants <- union(grants, m)
  }
  grants
}

ct_normalize_trial <- function(raw) {
  ps <- raw$protocolSection
  ident <- ps$identificationModule
  list(
    nct_id          = ident$nctId,
    brief_title     = ident$briefTitle,
    overall_status  = ps$statusModule$overallStatus,
    phase           = paste(unlist(ps$designModule$phases %||% list()), collapse = "/"),
    conditions      = unlist(ps$conditionsModule$conditions %||% list()),
    lead_sponsor    = ps$sponsorCollaboratorsModule$leadSponsor$name,
    nih_grant_numbers = ct_extract_nih_grants(ident)
  )
}

ct_search_trials <- function(query = NULL, condition = NULL, intervention = NULL,
                             sponsor = NULL, limit = 50) {
  q <- list(countTotal = "true", pageSize = as.character(min(1000, limit)))
  if (!is.null(condition))    q[["query.cond"]]  <- condition
  if (!is.null(intervention)) q[["query.intr"]]  <- intervention
  if (!is.null(sponsor))      q[["query.spons"]] <- sponsor
  if (!is.null(query))        q[["query.term"]]  <- query
  data <- cache_get_json("https://clinicaltrials.gov/api/v2/studies", q, "clinicaltrials")
  lapply((data$studies %||% list())[seq_len(min(length(data$studies %||% list()), limit))],
         ct_normalize_trial)
}

# Trials that authoritatively self-report this grant (verified, not text match).
ct_find_trials_for_grant <- function(core_project_num, limit = 100) {
  core <- toupper(core_project_num)
  cand <- ct_search_trials(query = core_project_num, limit = limit)
  Filter(function(t) core %in% toupper(t$nih_grant_numbers), cand)
}

ct_count_trials <- function(query = NULL, condition = NULL, has_results = NULL) {
  q <- list(countTotal = "true", pageSize = "1")
  if (!is.null(condition)) q[["query.cond"]] <- condition
  if (!is.null(query))     q[["query.term"]] <- query
  if (isTRUE(has_results)) q[["aggFilters"]] <- "results:with"
  data <- cache_get_json("https://clinicaltrials.gov/api/v2/studies", q, "clinicaltrials")
  as.integer(data$totalCount %||% 0)
}
