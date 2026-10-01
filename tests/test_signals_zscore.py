"""T07: causal z-score and percentile. Values below are hand arithmetic, not market data."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from aurumspread.signals.spread import build_spread_signals
from aurumspread.signals.zscore import PERCENTILE_COL, SIGMA_COL, SPREAD_MEAN_COL, ZSCORE_COL

FIXTURE = Path(__file__).parent / "fixtures" / "spread_series.csv"
WINDOW = 3
PAIR = ("GOLDM", date(2026, 2, 5), "GOLDTEN", date(2026, 1, 30))
# Window [10, 12, 11]: mean 11, sample std 1, z(11) = 0, percentile 2/3.
# Window [12, 11, 13]: mean 12, sample std 1, z(13) = 1, percentile 1.
# Window [11, 13, 9]: mean 11, sample std 2, z(9) = -1, percentile 1/3.
_EXPECTED = {
    date(2026, 1, 7): (11.0, 1.0, 0.0, 2.0 / 3.0),
    date(2026, 1, 8): (12.0, 1.0, 1.0, 1.0),
    date(2026, 1, 9): (11.0, 2.0, -1.0, 1.0 / 3.0),
}


def _load_fixture() -> pd.DataFrame:
    frame = pd.read_csv(FIXTURE)
    for column in ("trade_date", "expiry_a", "expiry_b"):
        frame[column] = pd.to_datetime(frame[column]).dt.date
    frame["spread_inr_per_g"] = frame["spread_inr_per_g"].astype("float64")
    return frame


def _pair(frame: pd.DataFrame) -> pd.DataFrame:
    mask = (
        (frame["symbol_a"] == PAIR[0])
        & (frame["expiry_a"] == PAIR[1])
        & (frame["symbol_b"] == PAIR[2])
        & (frame["expiry_b"] == PAIR[3])
    )
    return frame.loc[mask].reset_index(drop=True)


def test_hand_computed_zscore_and_percentile() -> None:
    signals = build_spread_signals(_load_fixture(), WINDOW)
    got = _pair(signals).set_index("trade_date")
    assert got.loc[date(2026, 1, 5), ZSCORE_COL] != got.loc[date(2026, 1, 5), ZSCORE_COL]
    assert got.loc[date(2026, 1, 6), ZSCORE_COL] != got.loc[date(2026, 1, 6), ZSCORE_COL]
    for trade_date, (mean, sigma, zscore, percentile) in _EXPECTED.items():
        assert got.loc[trade_date, SPREAD_MEAN_COL] == pytest.approx(mean)
        assert got.loc[trade_date, SIGMA_COL] == pytest.approx(sigma)
        assert got.loc[trade_date, ZSCORE_COL] == pytest.approx(zscore)
        assert got.loc[trade_date, PERCENTILE_COL] == pytest.approx(percentile)


def test_future_mutation_does_not_change_past_signals() -> None:
    original = _load_fixture()
    baseline = _pair(build_spread_signals(original, WINDOW))
    mutated = original.copy()
    future = mutated["trade_date"] > date(2026, 1, 7)
    mutated.loc[future, "spread_inr_per_g"] = mutated.loc[future, "spread_inr_per_g"] + 1000.0
    # Also reorder future rows. Past z-scores must still match.
    head = mutated.loc[~future]
    tail = mutated.loc[future].iloc[::-1]
    shuffled = pd.concat([head, tail], ignore_index=True)
    again = _pair(build_spread_signals(shuffled, WINDOW))
    past = baseline["trade_date"] <= date(2026, 1, 7)
    for column in (SPREAD_MEAN_COL, SIGMA_COL, ZSCORE_COL, PERCENTILE_COL):
        pd.testing.assert_series_equal(
            again.loc[past, column].reset_index(drop=True),
            baseline.loc[past, column].reset_index(drop=True),
            check_names=False,
        )


def test_dropping_future_rows_does_not_change_past_signals() -> None:
    original = _load_fixture()
    baseline = _pair(build_spread_signals(original, WINDOW))
    trimmed = original.loc[original["trade_date"] <= date(2026, 1, 7)].copy()
    again = _pair(build_spread_signals(trimmed, WINDOW))
    expected = baseline.loc[baseline["trade_date"] <= date(2026, 1, 7), ZSCORE_COL]
    pd.testing.assert_series_equal(
        again[ZSCORE_COL].reset_index(drop=True),
        expected.reset_index(drop=True),
        check_names=False,
    )


def test_pairs_do_not_share_a_window() -> None:
    signals = build_spread_signals(_load_fixture(), WINDOW)
    other = signals[
        (signals["expiry_a"] == date(2026, 3, 5)) & (signals["trade_date"] == date(2026, 1, 7))
    ].iloc[0]
    # Three identical 1s: sample sigma is 0, so z is undefined; percentile is 1.
    assert other[SIGMA_COL] == pytest.approx(0.0)
    assert np.isnan(other[ZSCORE_COL])
    assert other[PERCENTILE_COL] == pytest.approx(1.0)
    spike = signals[
        (signals["expiry_a"] == date(2026, 3, 5)) & (signals["trade_date"] == date(2026, 1, 9))
    ].iloc[0]
    # Window [1, 1, 100]: mean 34, z = 66 / sqrt(3267).
    assert spike[SPREAD_MEAN_COL] == pytest.approx(34.0)
    assert spike[ZSCORE_COL] == pytest.approx(66.0 / (3267.0**0.5))


def test_nan_inside_window_blocks_that_row_only() -> None:
    frame = _load_fixture()
    frame.loc[frame["trade_date"] == date(2026, 1, 6), "spread_inr_per_g"] = np.nan
    got = _pair(build_spread_signals(frame, WINDOW)).set_index("trade_date")
    assert np.isnan(got.loc[date(2026, 1, 7), ZSCORE_COL])
    # 8 Jan window is [NaN, 11, 13] and stays NaN; 9 Jan window is [11, 13, 9], complete.
    assert np.isnan(got.loc[date(2026, 1, 8), ZSCORE_COL])
    assert got.loc[date(2026, 1, 9), ZSCORE_COL] == pytest.approx(-1.0)


def test_duplicate_trade_date_raises() -> None:
    frame = _pair(_load_fixture())
    doubled = pd.concat([frame, frame.iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="duplicate trade_date"):
        build_spread_signals(doubled, WINDOW)


def test_empty_frame_keeps_signal_columns() -> None:
    empty = _load_fixture().iloc[0:0]
    got = build_spread_signals(empty, WINDOW)
    assert list(got.columns[-4:]) == [SPREAD_MEAN_COL, SIGMA_COL, ZSCORE_COL, PERCENTILE_COL]
    assert got.empty


def test_input_frame_is_not_mutated() -> None:
    frame = _load_fixture()
    before = frame.copy()
    build_spread_signals(frame, WINDOW)
    pd.testing.assert_frame_equal(frame, before)
