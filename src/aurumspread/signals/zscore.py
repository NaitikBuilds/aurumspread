"""Causal rolling z-score and percentile (PRD 6.3, BACKTEST_PROTOCOL rules 1–3).

Row ``i`` of a time-ordered series uses only indices ``[i - window + 1, i]``.
The observation at t is included: the signal is formed at that close. Fills are
not computed here. An incomplete window, any NaN inside it, or a zero sample
standard deviation leaves ``zscore`` as NaN. Nothing in this module reads a
later index.
"""

from __future__ import annotations

import numpy as np

SPREAD_MEAN_COL = "spread_mean_inr_per_g"
SIGMA_COL = "spread_sigma_inr_per_g"
ZSCORE_COL = "zscore"
PERCENTILE_COL = "percentile_rank"
SIGNAL_COLUMNS = (SPREAD_MEAN_COL, SIGMA_COL, ZSCORE_COL, PERCENTILE_COL)


def rolling_signal_arrays(spread_inr_per_g: np.ndarray, window: int) -> dict[str, np.ndarray]:
    """Rolling mean, sample sigma, z-score, and percentile rank of one series.

    ``spread_inr_per_g`` must already be sorted by trade date, oldest first.
    ``window`` is the number of sessions (``zscore_window_days`` in config).
    Sigma uses ``ddof=1``. Percentile is the fraction of the window that is
    ``<=`` the current value, in ``(0, 1]``.

    Returns float64 arrays of the same length, named by :data:`SIGNAL_COLUMNS`.
    """
    if isinstance(window, bool) or not isinstance(window, int):
        raise TypeError(f"window must be an int, got {window!r}")
    if window < 2:
        raise ValueError(f"window must be >= 2, got {window}")
    values = np.asarray(spread_inr_per_g, dtype="float64")
    if values.ndim != 1:
        raise ValueError(f"spread must be 1-d, got shape {values.shape}")
    n = values.shape[0]
    mean = np.full(n, np.nan, dtype="float64")
    sigma = np.full(n, np.nan, dtype="float64")
    zscore = np.full(n, np.nan, dtype="float64")
    percentile = np.full(n, np.nan, dtype="float64")
    for i in range(window - 1, n):
        # Closed window ending at i. The slice stops at i+1 and never past it.
        window_values = values[i - window + 1 : i + 1]
        if np.isnan(window_values).any():
            continue
        mu = float(window_values.mean())
        sig = float(np.std(window_values, ddof=1))
        mean[i] = mu
        sigma[i] = sig
        if sig > 0.0:
            zscore[i] = (float(values[i]) - mu) / sig
        percentile[i] = float(np.count_nonzero(window_values <= values[i]) / window)
    return {
        SPREAD_MEAN_COL: mean,
        SIGMA_COL: sigma,
        ZSCORE_COL: zscore,
        PERCENTILE_COL: percentile,
    }
