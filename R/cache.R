# Cached HTTP helpers — mirror the Python CachedClient (GET/POST JSON with
# on-disk caching keyed by the full request). Responses cache under data/cache/.

`%||%` <- function(a, b) if (is.null(a) || length(a) == 0) b else a

.cache_dir <- function(sub) {
  d <- file.path("data", "cache", sub)
  dir.create(d, recursive = TRUE, showWarnings = FALSE)
  d
}

.UA <- "nih-science-agent-R/0.1 (+https://reporter.nih.gov)"

cache_get_json <- function(url, query = list(), sub) {
  key <- substr(digest::digest(list(m = "GET", url = url, q = query)), 1, 24)
  fp <- file.path(.cache_dir(sub), paste0(key, ".json"))
  if (file.exists(fp)) return(jsonlite::fromJSON(fp, simplifyVector = FALSE))
  resp <- httr2::request(url) |>
    httr2::req_url_query(!!!query) |>
    httr2::req_user_agent(.UA) |>
    httr2::req_retry(max_tries = 3) |>
    httr2::req_perform()
  body <- httr2::resp_body_string(resp)
  writeLines(body, fp)
  jsonlite::fromJSON(body, simplifyVector = FALSE)
}

cache_post_json <- function(url, body, sub) {
  key <- substr(digest::digest(list(m = "POST", url = url, b = body)), 1, 24)
  fp <- file.path(.cache_dir(sub), paste0("post_", key, ".json"))
  if (file.exists(fp)) return(jsonlite::fromJSON(fp, simplifyVector = FALSE))
  resp <- httr2::request(url) |>
    httr2::req_body_json(body) |>
    httr2::req_user_agent(.UA) |>
    httr2::req_retry(max_tries = 3) |>
    httr2::req_perform()
  out <- httr2::resp_body_string(resp)
  writeLines(out, fp)
  jsonlite::fromJSON(out, simplifyVector = FALSE)
}
