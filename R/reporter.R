# NIH RePORTER v2 connector (native R).

reporter_normalize <- function(raw) {
  ic <- raw$agency_ic_admin
  ic_name <- if (is.list(ic)) (ic$abbreviation %||% ic$name %||% ic$code) else ic
  terms_raw <- raw$terms %||% ""
  terms <- if (nchar(terms_raw))
    trimws(strsplit(gsub("^<|>$", "", terms_raw), "><")[[1]]) else character(0)
  pis <- vapply(raw$principal_investigators %||% list(),
                function(p) p$full_name %||% NA_character_, character(1))
  list(
    project_number   = raw$project_num %||% raw$core_project_num,
    core_project_num = raw$core_project_num %||% raw$project_num,
    application_id   = raw$appl_id,
    project_title    = raw$project_title %||% NA_character_,
    abstract_text    = raw$abstract_text %||% NA_character_,
    fiscal_year      = raw$fiscal_year %||% NA_integer_,
    ic               = ic_name %||% NA_character_,
    activity_code    = raw$activity_code %||% NA_character_,
    organization     = raw$organization$org_name %||% NA_character_,
    pi_names         = pis,
    total_cost       = raw$award_amount %||% raw$total_cost %||% NA_real_,
    terms            = terms,
    foa_number       = raw$opportunity_number %||% raw$full_foa,
    source_url       = if (!is.null(raw$appl_id))
      sprintf("https://reporter.nih.gov/project-details/%s", raw$appl_id) else NA_character_
  )
}

reporter_search_projects <- function(query = NULL, fiscal_years = NULL,
                                     institutes = NULL, mechanisms = NULL,
                                     pi_names = NULL, limit = 100) {
  criteria <- list()
  if (!is.null(query))
    criteria$advanced_text_search <- list(operator = "and",
      search_field = "projecttitle,terms,abstracttext", search_text = query)
  if (!is.null(fiscal_years)) criteria$fiscal_years <- as.list(as.integer(fiscal_years))
  if (!is.null(institutes))   criteria$agencies <- as.list(institutes)
  if (!is.null(mechanisms))   criteria$activity_codes <- as.list(mechanisms)
  if (!is.null(pi_names))
    criteria$pi_names <- lapply(pi_names, function(n) list(any_name = n))

  results <- list(); offset <- 0
  repeat {
    page_size <- min(500, limit - length(results))
    if (page_size <= 0) break
    body <- list(criteria = criteria, offset = offset, limit = page_size,
                 sort_field = "fiscal_year", sort_order = "desc")
    data <- cache_post_json("https://api.reporter.nih.gov/v2/projects/search", body, "reporter")
    page <- data$results
    if (is.null(page) || length(page) == 0) break
    results <- c(results, page)
    total <- data$meta$total %||% 0
    offset <- offset + page_size
    if (offset >= total) break
  }
  lapply(results[seq_len(min(length(results), limit))], reporter_normalize)
}

reporter_get_publications <- function(core_project_num, limit = 500) {
  links <- list(); offset <- 0
  repeat {
    page_size <- min(500, limit - length(links))
    if (page_size <= 0) break
    body <- list(criteria = list(core_project_nums = list(core_project_num)),
                 offset = offset, limit = page_size)
    data <- cache_post_json("https://api.reporter.nih.gov/v2/publications/search", body, "reporter")
    page <- data$results
    if (is.null(page) || length(page) == 0) break
    links <- c(links, page)
    total <- data$meta$total %||% 0
    offset <- offset + page_size
    if (offset >= total) break
  }
  vapply(links[seq_len(min(length(links), limit))],
         function(r) as.character(r$pmid), character(1))
}

# Convert a list of normalized projects to a tidy tibble for display.
reporter_as_tibble <- function(projects) {
  tibble::tibble(
    core_project_num = vapply(projects, function(p) p$core_project_num %||% NA_character_, character(1)),
    fiscal_year      = vapply(projects, function(p) as.integer(p$fiscal_year %||% NA), integer(1)),
    ic               = vapply(projects, function(p) p$ic %||% NA_character_, character(1)),
    total_cost       = vapply(projects, function(p) as.numeric(p$total_cost %||% NA), numeric(1)),
    organization     = vapply(projects, function(p) p$organization %||% NA_character_, character(1)),
    title            = vapply(projects, function(p) p$project_title %||% NA_character_, character(1))
  )
}
