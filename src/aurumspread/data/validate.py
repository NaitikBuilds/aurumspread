"""Row validation for parsed Bhavcopy frames (docs/DATA_CONTRACT.md "Validation").

Rule 1 (returned date == requested date) is enforced at fetch time; it is
re-checked here defensively. Rules 2-7 are implemented as pure functions that
take a frame and a :class:`DQLog` and return a new frame; nothing is mutated.

Outcome per rule:
* 2 symbol outside universe   -> row dropped silently (one summary DQ event per day)
* 3 inconsistent prices       -> row dropped, DQ ``bad_price``
* 4 missing/zero volume or OI -> row kept, ``thin=True``, DQ ``thin``
* 5 day-over-day jump         -> row kept, ``jump_flag=True``, DQ ``price_jump``
* 6 duplicate (symbol, expiry) on a day -> :class:`DuplicateKeyError`
* 7 expiry before trade date  -> row dropped, DQ ``expired_contract``
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

import numpy as np
import pandas as pd

from aurumspread.data.dq import DQLog

KEY_COLUMNS = ("symbol", "expiry_date")


class DuplicateKeyError(ValueError):
    """Two rows share (trade_date, symbol, expiry_date); the raw file is corrupt."""


def _key(row: pd.Series) -> str:
    return f"{row['symbol']}/{row['expiry_date'].isoformat()}"


def check_unique_keys(frame: pd.DataFrame) -> None:
    """Rule 6: raise if any (trade_date, symbol, expiry_date) appears twice."""
    dupes = frame.duplicated(subset=["trade_date", *KEY_COLUMNS], keep=False)
    if dupes.any():
        examples = (
            frame.loc[dupes, ["trade_date", *KEY_COLUMNS]]
            .drop_duplicates()
            .head(5)
            .to_dict("records")
        )
        raise DuplicateKeyError(f"duplicate contract keys: {examples}")


def validate_day(
    frame: pd.DataFrame,
    universe: Iterable[str],
    dq_log: DQLog,
    *,
    expected_trade_date: date | None = None,
) -> pd.DataFrame:
    """Apply rules 1-4, 6, 7 to one day's parsed frame and return the clean rows.

    The returned frame has an extra boolean ``thin`` column and is sorted by
    (symbol, expiry_date). Rule 5 needs history and lives in :func:`flag_price_jumps`.
    """
    universe_set = set(universe)
    out = frame.copy()

    # Rule 1 (defensive): every row must carry the requested trade date.
    if expected_trade_date is not None:
        wrong = out["trade_date"] != expected_trade_date
        if wrong.any():
            dq_log.add(
                expected_trade_date,
                "validate",
                "date_mismatch",
                f"{int(wrong.sum())} rows carry a different trade_date; dropped",
            )
            out = out[~wrong]

    # Rule 2: universe filter, ignored (not errored).
    outside = ~out["symbol"].isin(universe_set)
    if outside.any():
        day = expected_trade_date or (out["trade_date"].iloc[0] if len(out) else None)
        ignored = sorted(out.loc[outside, "symbol"].unique().tolist())
        dq_log.add(day, "validate", "symbols_ignored", f"outside universe: {ignored}")
        out = out[~outside]

    # Rule 6 before anything else that could hide a duplicate.
    check_unique_keys(out)

    # Rule 3: price sanity.
    bad_price = (
        (out["close_inr"] <= 0)
        | (out["high_inr"] < out["low_inr"])
        | (out["close_inr"] < out["low_inr"])
        | (out["close_inr"] > out["high_inr"])
    )
    for _, row in out[bad_price].iterrows():
        dq_log.add(
            row["trade_date"],
            "validate",
            "bad_price",
            f"open={row['open_inr']} high={row['high_inr']} low={row['low_inr']} "
            f"close={row['close_inr']}",
            key=_key(row),
        )
    out = out[~bad_price]

    # Rule 7: expiry must not precede the trade date.
    expired = out["expiry_date"] < out["trade_date"]
    for _, row in out[expired].iterrows():
        dq_log.add(
            row["trade_date"],
            "validate",
            "expired_contract",
            f"expiry {row['expiry_date']} before trade date {row['trade_date']}",
            key=_key(row),
        )
    out = out[~expired]

    # Rule 4: thin flag, row kept.
    volume = out["volume"]
    oi = out["open_interest"]
    thin = volume.isna() | (volume <= 0) | oi.isna() | (oi <= 0)
    out = out.assign(thin=thin.to_numpy(dtype=bool))
    for _, row in out[out["thin"]].iterrows():
        dq_log.add(
            row["trade_date"],
            "validate",
            "thin",
            f"volume={row['volume']} open_interest={row['open_interest']}",
            key=_key(row),
        )

    return out.sort_values(list(KEY_COLUMNS), kind="stable").reset_index(drop=True)


def flag_price_jumps(
    frame: pd.DataFrame,
    dq_log: DQLog,
    *,
    max_jump_pct: float,
    price_col: str = "close_inr",
) -> pd.DataFrame:
    """Rule 5: flag |day-over-day change| > ``max_jump_pct`` per contract; never drop.

    Percent changes are computed within each (symbol, expiry_date) so a
    per-symbol constant like normalization does not alter the result.
    Adds a boolean ``jump_flag`` column.
    """
    if frame.empty:
        return frame.assign(jump_flag=np.zeros(0, dtype=bool))
    ordered = frame.sort_values(["symbol", "expiry_date", "trade_date"], kind="stable")
    prev = ordered.groupby(list(KEY_COLUMNS), sort=False)[price_col].shift(1)
    pct = (ordered[price_col] / prev - 1.0) * 100.0
    jump = pct.abs() > max_jump_pct
    jump = jump.fillna(False).astype(bool)
    for _, row in ordered[jump].assign(_pct=pct[jump]).iterrows():
        dq_log.add(
            row["trade_date"],
            "validate",
            "price_jump",
            f"{price_col} moved {row['_pct']:+.2f}% vs previous trading day "
            f"(limit {max_jump_pct}%); kept for review",
            key=_key(row),
        )
    out = frame.copy()
    out["jump_flag"] = jump.reindex(frame.index).to_numpy(dtype=bool)
    return out
