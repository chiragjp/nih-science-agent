# openFDA connector (native R) — drugs labeled for an indication.

fda_drugs_for_indication <- function(indication, limit = 10) {
  out <- tryCatch(
    cache_get_json("https://api.fda.gov/drug/label.json",
      list(search = sprintf('indications_and_usage:"%s"', indication), limit = limit), "fda"),
    error = function(e) NULL)
  if (is.null(out)) return(list(total = 0L, drugs = character(0)))
  total <- out$meta$results$total %||% 0L
  brands <- unique(unlist(lapply(out$results, function(r) r$openfda$brand_name)))
  list(total = as.integer(total), drugs = head(brands, 8))
}
