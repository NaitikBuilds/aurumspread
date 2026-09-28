# Data Contract (Bhavcopy)
Fields provided: Symbol, Date, ExpiryDate, Open, High, Low, Close, Volume, OpenInterest.

## Parsing rules
| Field | Raw | Parsed |
|---|---|---|
| Request date | DD/MM/YYYY | date |
| Date (response) | MM/DD/YYYY | `trade_date` (date) |
| ExpiryDate | e.g. 04SEP2026 | `expiry_date` (date) |
| Symbol | may be space-padded | stripped upper-case `symbol` |
Contract key = (`symbol`, `expiry_date`). Unique per `trade_date`.

Parsed columns (`data/parse.py`): `symbol, trade_date, expiry_date, open_inr, high_inr, low_inr, close_inr, volume, open_interest, source, source_row`.
Prices are INR per quote unit of the contract (GOLDM: per 10 g). `source`/`source_row` = raw file and 1-based line, for traceability.
Validation adds `thin` and `jump_flag`. The header must match the nine fields above exactly; anything else raises `BhavcopyFormatError`.
Endpoint/request settings and quality thresholds: `config/data_source.yaml`.

## Validation (each failure is logged to `data_quality_log`)
1. returned `trade_date` == requested date, else discard (holiday/future/malformed).
2. Symbol in configured universe; other symbols/options rows ignored, not errored.
3. Close > 0, High >= Low, Low <= Close <= High. 
4. Volume/OI missing or zero -> flag `thin`, keep row.
5. Day-over-day normalized price jump > 5% (configurable) -> flag for review, do not auto-drop.
6. Duplicate keys -> error.
7. `expiry_date` >= `trade_date`.

DQ event codes (`data_quality_log.parquet`): `date_mismatch`, `empty_response` (fetch); `symbols_ignored`, `bad_price`, `expired_contract`, `thin`, `price_jump` (validate). Duplicate keys raise `DuplicateKeyError`.

## Open questions (human must resolve from a real file; Cursor must not guess)
- Is `Close` the official settlement price? If a separate settlement column exists, which is used?
- Are Volume/OI in lots or units? (affects gram-based liquidity math)
- First actual trade date of GOLDTEN in the data.
- Does data include evening-session-only days/holidays inconsistencies?
- Is the endpoint rate-limited / does it need headers or cookies? Record the working request shape here.
