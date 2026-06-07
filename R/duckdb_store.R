# Population-scale queries over the ExPORTER→DuckDB store (native R).
# Opens the same data/processed/nih.duckdb the Python `bulk build` produced.

bulk_connect <- function(db = "data/processed/nih.duckdb") {
  DBI::dbConnect(duckdb::duckdb(), db, read_only = TRUE)
}
bulk_disconnect <- function(con) DBI::dbDisconnect(con, shutdown = TRUE)

.ic_map <- c(NCI="CA", NIAID="AI", NIGMS="GM", NHLBI="HL", NIA="AG", NIDDK="DK",
             NINDS="NS", NIMH="MH", NICHD="HD", NIEHS="ES", NIDA="DA", NEI="EY",
             NIDCD="DC", NIDCR="DE", NIAMS="AR", NIBIB="EB", NHGRI="HG", NIAAA="AA",
             NCATS="TR", NIMHD="MD", NLM="LM", NINR="NR", NCCIH="AT", FIC="TW")
.resolve_ic <- function(ic) { u <- toupper(ic); if (u %in% names(.ic_map)) unname(.ic_map[u]) else u }

.where <- function(ic = NULL, year = NULL, extra = character(0)) {
  cl <- extra
  if (!is.null(ic))   cl <- c(cl, sprintf("ic = '%s'", .resolve_ic(ic)))
  if (!is.null(year)) cl <- c(cl, sprintf("fiscal_year = %d", as.integer(year)))
  paste(cl, collapse = " AND ")
}

.FOA_TYPE_SQL <- "CASE
  WHEN foa_number IS NULL OR foa_number = '' THEN 'NONE'
  WHEN foa_number LIKE 'RFA-%' THEN 'RFA' WHEN foa_number LIKE 'PAR-%' THEN 'PAR'
  WHEN foa_number LIKE 'PAS-%' THEN 'PAS' WHEN foa_number LIKE 'PA-%' THEN 'PA'
  WHEN foa_number LIKE 'NOT-%' THEN 'NOTICE' ELSE 'OTHER' END"

bulk_concentration <- function(con, ic = NULL, year = NULL, top_n = 10) {
  w <- .where(ic, year, c("contact_pi_id <> ''", "total_cost > 0"))
  df <- DBI::dbGetQuery(con, sprintf(
    "SELECT contact_pi_id, any_value(contact_pi_name) pi_name, sum(total_cost) funding,
            count(*) awards FROM award_pi WHERE %s GROUP BY contact_pi_id ORDER BY funding DESC", w))
  n <- nrow(df); total <- sum(df$funding)
  share <- function(f) sum(head(df$funding, max(1, round(n * f)))) / total
  asc <- sort(df$funding)
  list(n_pis = n, total = total,
       top1 = share(.01), top5 = share(.05), top10 = share(.10),
       gini = (n + 1 - 2 * sum(cumsum(asc)) / total) / n,
       top = head(df, top_n))
}

bulk_funding_vs_output <- function(con, ic = NULL, year = NULL, funding_floor = 250000) {
  w <- .where(ic, year, c("contact_pi_id <> ''", "total_cost > 0"))
  q <- sprintf("WITH f AS (SELECT contact_pi_id, sum(total_cost) funding FROM award_pi WHERE %s GROUP BY 1),
    p AS (SELECT ap.contact_pi_id, count(DISTINCT pl.pmid) pubs FROM award_pi ap
          JOIN publinks pl ON pl.core_project_num = ap.core_project_num WHERE %s GROUP BY 1)
    SELECT f.funding, COALESCE(p.pubs,0) pubs FROM f LEFT JOIN p USING(contact_pi_id)
    WHERE f.funding >= %f", w, w, funding_floor)
  df <- DBI::dbGetQuery(con, q)
  df$pubs_per_million <- df$pubs / (df$funding / 1e6)
  df$quartile <- dplyr::ntile(df$funding, 4)
  df
}

bulk_foa_types <- function(con, ic = NULL, year = NULL) {
  w <- .where(ic, year, c("total_cost > 0"))
  q <- sprintf("WITH f AS (SELECT %s foa_type, count(*) awards, sum(total_cost) funding
      FROM awards WHERE %s GROUP BY 1),
      p AS (SELECT %s foa_type, count(DISTINCT pl.pmid) pubs FROM awards
            JOIN publinks pl ON pl.core_project_num = awards.core_project_num WHERE %s GROUP BY 1)
    SELECT f.foa_type, f.awards, f.funding, COALESCE(p.pubs,0) pubs
    FROM f LEFT JOIN p USING(foa_type) ORDER BY f.funding DESC",
    .FOA_TYPE_SQL, w, .FOA_TYPE_SQL, w)
  df <- DBI::dbGetQuery(con, q)
  df$pubs_per_million <- df$pubs / (df$funding / 1e6)
  df
}

bulk_latency <- function(con, years = NULL, ic = NULL) {
  cl <- c("total_cost > 0", "core_project_num IS NOT NULL")
  if (!is.null(ic)) cl <- c(cl, sprintf("ic = '%s'", .resolve_ic(ic)))
  if (!is.null(years)) cl <- c(cl, sprintf("fiscal_year IN (%s)", paste(years, collapse = ",")))
  w <- paste(cl, collapse = " AND ")
  df <- DBI::dbGetQuery(con, sprintf(
    "WITH g AS (SELECT core_project_num, min(fiscal_year) start_fy FROM awards WHERE %s GROUP BY 1),
      fp AS (SELECT core_project_num, min(pub_year) first_pub FROM publinks WHERE pub_year IS NOT NULL GROUP BY 1)
     SELECT g.start_fy, fp.first_pub FROM g LEFT JOIN fp USING(core_project_num)", w))
  lat <- with(df, (first_pub - start_fy)[!is.na(first_pub) & first_pub >= start_fy])
  list(grants_total = nrow(df), grants_with_output = length(lat),
       pct_censored = 1 - length(lat) / nrow(df),
       median = median(lat), p25 = quantile(lat, .25), p75 = quantile(lat, .75))
}

# Mechanism family from activity_code (mirrors tools/productivity.mechanism_family):
# keeps shared-facility cores and training grants from contaminating a
# research-grant productivity ranking. Compare WITHIN a family, not across.
.MECH_FAMILY_SQL <- "CASE
  WHEN activity_code IS NULL OR activity_code = '' THEN 'other'
  WHEN activity_code LIKE 'DP%' THEN 'research'
  WHEN activity_code LIKE 'Z%' THEN 'intramural'
  WHEN activity_code IN ('U19','U54','UM1','UM2','P2C','UG3','UH3') THEN 'center_core'
  WHEN substr(activity_code,1,1) IN ('P','S','G','C') THEN 'center_core'
  WHEN activity_code IN ('D43','D71','R25','R90','R38','RL5','TL1','KL2') THEN 'training_career'
  WHEN substr(activity_code,1,1) IN ('T','F','K') THEN 'training_career'
  WHEN substr(activity_code,1,1) IN ('R','U') THEN 'research'
  ELSE 'other' END"

# Population-scale productivity split by mechanism family: per-core funding vs
# distinct linked publications, summarized per family (median pubs-per-$1M).
bulk_productivity_by_mechanism <- function(con, ic = NULL, years = NULL, funding_floor = 1e5) {
  cl <- c("total_cost > 0", "core_project_num IS NOT NULL")
  if (!is.null(ic)) cl <- c(cl, sprintf("ic = '%s'", .resolve_ic(ic)))
  if (!is.null(years)) cl <- c(cl, sprintf("fiscal_year IN (%s)", paste(years, collapse = ",")))
  w <- paste(cl, collapse = " AND ")
  q <- sprintf(
    "WITH g AS (SELECT core_project_num, any_value(activity_code) activity_code,
        sum(total_cost) funding FROM awards WHERE %s GROUP BY core_project_num),
      pc AS (SELECT core_project_num, count(DISTINCT pmid) pubs FROM publinks GROUP BY 1)
     SELECT %s AS family, g.funding, COALESCE(pc.pubs,0) pubs
     FROM g LEFT JOIN pc USING(core_project_num) WHERE g.funding >= %f",
    w, gsub("activity_code", "g.activity_code", .MECH_FAMILY_SQL), funding_floor)
  df <- DBI::dbGetQuery(con, q)
  df$pubs_per_million <- df$pubs / (df$funding / 1e6)
  df |>
    dplyr::group_by(family) |>
    dplyr::summarise(
      grants = dplyr::n(),
      funding_M = round(sum(funding) / 1e6),
      total_pubs = sum(pubs),
      median_pubs_per_M = round(median(pubs_per_million), 2),
      mean_pubs_per_M = round(mean(pubs_per_million), 2),
      .groups = "drop") |>
    dplyr::arrange(dplyr::desc(funding_M))
}

# Linkage coverage stratified by a dimension. For every distinct core project
# (optionally scoped to an IC / years), report what fraction has >= 1
# authoritatively-linked publication. Coverage is never uniform — surfacing the
# gradient by IC, mechanism family, or grant age is the audit the design doc asks
# for (don't analyze a portfolio without disclosing where the edges are missing).
bulk_coverage_stratified <- function(con, by = c("ic", "family", "fiscal_year"),
                                     ic = NULL, years = NULL, min_grants = 50) {
  by <- match.arg(by)
  cl <- c("total_cost > 0", "core_project_num IS NOT NULL")
  if (!is.null(ic)) cl <- c(cl, sprintf("ic = '%s'", .resolve_ic(ic)))
  if (!is.null(years)) cl <- c(cl, sprintf("fiscal_year IN (%s)", paste(years, collapse = ",")))
  w <- paste(cl, collapse = " AND ")
  dim_sql <- switch(by,
    ic = "any_value(ic)",
    family = gsub("activity_code", "any_value(activity_code)", .MECH_FAMILY_SQL),
    fiscal_year = "min(fiscal_year)")
  q <- sprintf(
    "WITH g AS (SELECT core_project_num, %s AS strat FROM awards WHERE %s GROUP BY core_project_num),
      pc AS (SELECT DISTINCT core_project_num FROM publinks)
     SELECT g.strat,
            count(*) grants,
            count(pc.core_project_num) linked
     FROM g LEFT JOIN pc USING(core_project_num) GROUP BY g.strat", dim_sql, w)
  df <- DBI::dbGetQuery(con, q)
  df <- df[df$grants >= min_grants, ]
  df$coverage_pct <- round(100 * df$linked / df$grants, 1)
  df[order(-df$coverage_pct), ]
}

bulk_grant_panel <- function(con, ic = NULL, years = NULL, funding_floor = 1e5) {
  cl <- c("total_cost > 0", "core_project_num IS NOT NULL")
  if (!is.null(ic)) cl <- c(cl, sprintf("ic = '%s'", .resolve_ic(ic)))
  if (!is.null(years)) cl <- c(cl, sprintf("fiscal_year IN (%s)", paste(years, collapse = ",")))
  w <- paste(cl, collapse = " AND ")
  DBI::dbGetQuery(con, sprintf(
    "WITH g AS (SELECT core_project_num, any_value(activity_code) activity_code,
        min(fiscal_year) fiscal_year, max(foa_number) foa_number, sum(total_cost) funding
        FROM awards WHERE %s GROUP BY core_project_num),
      pc AS (SELECT core_project_num, count(DISTINCT pmid) pubs FROM publinks GROUP BY 1)
     SELECT g.core_project_num, g.activity_code, g.fiscal_year, %s foa_type,
            g.funding, COALESCE(pc.pubs,0) pubs
     FROM g LEFT JOIN pc USING(core_project_num) WHERE g.funding >= %f",
    w, gsub("foa_number", "g.foa_number", .FOA_TYPE_SQL), funding_floor))
}
