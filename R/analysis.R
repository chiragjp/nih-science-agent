# Native-R analyses built on the connectors.

# --- diminishing returns: output-per-$ vs PI grant support -------------------
diminishing_returns <- function(institutes = NULL, query = NULL, fiscal_years = NULL,
                                funding_floor = 200000, max_pis = 30, pub_limit = 200) {
  projects <- reporter_search_projects(query = query, institutes = institutes,
                                       fiscal_years = fiscal_years, limit = 1500)
  fund <- list(); cores <- list()
  for (p in projects) {
    pi <- (p$pi_names %||% NA_character_)[1]
    if (is.na(pi)) next
    fund[[pi]] <- (fund[[pi]] %||% 0) + (as.numeric(p$total_cost) %||% 0)
    cores[[pi]] <- union(cores[[pi]] %||% character(0), p$core_project_num)
  }
  pis <- names(fund)[vapply(fund, function(x) x >= funding_floor, logical(1))]
  if (length(pis) > max_pis) {                     # stratified sample over funding range
    ord <- pis[order(unlist(fund[pis]))]
    pis <- ord[round(seq(1, length(ord), length.out = max_pis))]
  }
  rows <- lapply(pis, function(pi) {
    pmids <- unique(unlist(lapply(cores[[pi]], reporter_get_publications, limit = pub_limit)))
    rcr <- if (length(pmids)) sum(icite_fetch_metrics(pmids)$rcr, na.rm = TRUE) else 0
    tibble::tibble(pi = pi, funding = fund[[pi]], weighted_rcr = rcr,
                   rcr_per_million = rcr / (fund[[pi]] / 1e6))
  })
  out <- dplyr::bind_rows(rows)
  out$quartile <- dplyr::ntile(out$funding, 4)
  out
}

# --- discovery -> translation: basic-science reach into the clinic -----------
translation_scan <- function(query = NULL, institutes = NULL, fiscal_years = NULL,
                             max_grants = 15, pub_limit = 200) {
  projects <- reporter_search_projects(query = query, institutes = institutes,
                                       fiscal_years = fiscal_years, limit = max_grants * 4)
  seen <- list()
  for (p in projects) {
    core <- p$core_project_num
    if (is.null(core)) next
    if (is.null(seen[[core]]) ||
        ((p$fiscal_year %||% Inf) < (seen[[core]]$fy %||% Inf)))
      seen[[core]] <- list(title = p$project_title, fy = p$fiscal_year)
  }
  cores <- names(seen)[seq_len(min(length(seen), max_grants))]
  rows <- lapply(cores, function(core) {
    pmids <- reporter_get_publications(core, limit = pub_limit)
    reach <- 0; apts <- numeric(0)
    if (length(pmids)) {
      tr <- icite_clinical_citers(pmids)
      citers <- unique(unlist(lapply(tr, function(x) x$clinical_citers)))
      reach <- length(citers)
      apts <- vapply(tr, function(x) x$apt, numeric(1))
    }
    tibble::tibble(core = core, title = substr(seen[[core]]$title %||% "", 1, 48),
                   pubs = length(pmids), clinical_reach = reach,
                   mean_apt = round(mean(apts, na.rm = TRUE), 2))
  })
  dplyr::bind_rows(rows) |> dplyr::arrange(dplyr::desc(clinical_reach))
}
