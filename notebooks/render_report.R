#!/usr/bin/env Rscript
# Render a "State of an NIH Portfolio" report for any IC + condition + window.
#
#   Rscript notebooks/render_report.R NIDDK diabetes 2008 2017
#   Rscript notebooks/render_report.R NCI cancer 2008 2017
#   Rscript notebooks/render_report.R NIGMS none 2008 2017   # cross-cutting IC, no condition
#
# Conditions must be in the crosswalk — see `list_conditions()` in R/conditions.R.
# Pass `none` (or omit) for a cross-cutting IC with no single home condition.
# Run from the project root.
a <- commandArgs(trailingOnly = TRUE)
ic   <- if (length(a) >= 1) a[1] else "NIDDK"
cond <- if (length(a) >= 2) a[2] else "none"
ys   <- if (length(a) >= 3) as.integer(a[3]) else 2008L
ye   <- if (length(a) >= 4) as.integer(a[4]) else 2017L

slug <- if (tolower(cond) %in% c("none", "-", "na", "")) "structural" else gsub("[^A-Za-z0-9]+", "_", cond)
out <- sprintf("portfolio_%s_%s.html", toupper(ic), slug)
rmarkdown::render("notebooks/portfolio_report.Rmd",
  output_file = out,
  params = list(ic = ic, condition = cond, year_start = ys, year_end = ye))
cat("wrote notebooks/", out, "\n", sep = "")
