# iCite connector (native R) — Relative Citation Ratio + clinical-citation signals.

icite_fetch_metrics <- function(pmids) {
  if (length(pmids) == 0) return(tibble::tibble())
  recs <- list()
  for (start in seq(1, length(pmids), by = 200)) {
    batch <- pmids[start:min(start + 199, length(pmids))]
    data <- cache_get_json("https://icite.od.nih.gov/api/pubs",
                           list(pmids = paste(batch, collapse = ",")), "icite")
    for (r in data$data) recs[[as.character(r$pmid)]] <- r
  }
  tibble::tibble(
    pmid           = names(recs),
    year           = vapply(recs, function(r) as.integer(r$year %||% NA), integer(1)),
    rcr            = vapply(recs, function(r) as.numeric(r$relative_citation_ratio %||% NA), numeric(1)),
    citations      = vapply(recs, function(r) as.integer(r$citation_count %||% NA), integer(1)),
    nih_percentile = vapply(recs, function(r) as.numeric(r$nih_percentile %||% NA), numeric(1)),
    title          = vapply(recs, function(r) r$title %||% NA_character_, character(1)),
    journal        = vapply(recs, function(r) r$journal %||% NA_character_, character(1))
  )
}

# Translation signals: APT + the precomputed list of clinical articles citing each paper.
icite_clinical_citers <- function(pmids) {
  if (length(pmids) == 0) return(list())
  out <- list()
  for (start in seq(1, length(pmids), by = 200)) {
    batch <- pmids[start:min(start + 199, length(pmids))]
    data <- cache_get_json("https://icite.od.nih.gov/api/pubs",
                           list(pmids = paste(batch, collapse = ",")), "icite")
    for (r in data$data) {
      out[[as.character(r$pmid)]] <- list(
        apt = as.numeric(r$apt %||% NA),
        clinical_citers = as.character(unlist(r$cited_by_clin %||% list()))
      )
    }
  }
  out
}
