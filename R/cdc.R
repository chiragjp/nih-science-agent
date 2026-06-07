# CDC connector (native R) — mortality (NCHS) + PLACES prevalence via Socrata.

.soql_str <- function(v) sprintf("'%s'", gsub("'", "''", v))
.col <- function(recs, k, fn = as.character) {
  vapply(recs, function(r) { v <- r[[k]]; if (is.null(v)) NA else fn(v) },
         FUN.VALUE = fn(NA))
}

cdc_mortality <- function(cause_name, state = "United States", years = NULL) {
  where <- sprintf("cause_name=%s AND state=%s", .soql_str(cause_name), .soql_str(state))
  if (!is.null(years))
    where <- sprintf("%s AND year >= '%d' AND year <= '%d'", where, min(years), max(years))
  recs <- cache_get_json("https://data.cdc.gov/resource/bi63-dtpu.json",
    list(`$select` = "year,deaths,aadr", `$where` = where,
         `$order` = "year", `$limit` = 5000), "cdc")
  if (length(recs) == 0) return(tibble::tibble())
  tibble::tibble(year = .col(recs, "year", as.integer),
                 deaths = .col(recs, "deaths", as.integer),
                 aadr = .col(recs, "aadr", as.numeric))
}

cdc_prevalence_mean <- function(measure) {
  recs <- cache_get_json("https://data.cdc.gov/resource/swc5-untb.json",
    list(`$select` = "data_value", `$where` = sprintf("measure=%s", .soql_str(measure)),
         `$limit` = 5000), "cdc")
  vals <- suppressWarnings(as.numeric(.col(recs, "data_value", as.character)))
  round(mean(vals, na.rm = TRUE), 1)
}
