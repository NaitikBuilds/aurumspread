# Decision Log (append-only)
| Date | Decision | Reason | Decided by |
|---|---|---|---|
| 2026-09 | Contracts keyed by (symbol, expiry_date) | PS requirement; avoids roll jumps | PRD |
| 2026-09 | Repo name aurumspread, product name AurumEdge | user choice | Naitik |
| 2026-09-29 | Config loaders are frozen pydantic models with `extra="forbid"`; `verify:true` fields are required keys but nullable, exposed via `unverified_fields()` | typos fail loudly; results can be flagged until humans fill MCX facts (PRD 16) | T01 |
| 2026-09-29 | Endpoint request shape lives in `config/data_source.yaml` (`url_template` null until verified); fetch refuses to run while unset | URL/headers are not in the problem statement (DATA_CONTRACT open questions) | T02 |
| 2026-09-29 | Fetch rejects any response whose `Date` set != {requested}; payload kept under `data/raw/rejected/` for audit, never under the requested date | rule 40-data-layer; keeps holiday substitutions out of the history | T02 |
| 2026-09-29 | Raw files named `data/raw/bhavcopy_YYYY-MM-DD.csv`; processed parquet = `prices.parquet`, `contracts.parquet`, `data_quality_log.parquet` | ingest derives the expected trade date from the file name | T03 |
| 2026-09-29 | Rows failing price sanity (rule 3) or expiry < trade date (rule 7) are dropped and logged; thin (rule 4) and jumps (rule 5) are flagged, kept | follows DATA_CONTRACT outcomes; check on real data whether no-trade days print Open/High/Low = 0 with a positive Close | T03 |
| 2026-09-29 | `dte_days` and `exit_buffer_days_before_expiry` are calendar days; live iff `dte_days > buffer` | carry is quoted per calendar day (PRD 6.2); buffer vs tender period still `verify` | T05 |
| 2026-09-29 | Carry = slope of a family's own curve (`front_pair` default); single-expiry family borrows the cross-family median; without any estimate the spread is NaN, never the naive gap | PRD 6.2 step 3; acceptance test 7 | T06 |
| 2026-09-29 | Pairing picks the (a, b) contracts with the smallest DTE gap (ties -> front), capped at `alignment.max_dte_gap_days`; leg B is shifted along its curve to leg A's DTE | PRD 6.2 step 2 | T06 |
