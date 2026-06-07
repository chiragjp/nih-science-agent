# Source all native-R connectors. Run from the project root.
for (f in c("cache.R", "reporter.R", "icite.R", "clinicaltrials.R", "analysis.R",
            "cdc.R", "fda.R", "conditions.R", "duckdb_store.R", "report.R"))
  source(file.path("R", f))
