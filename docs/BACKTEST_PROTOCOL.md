# Backtest Protocol (frozen before results are viewed)
## Timeline
`warmup | TRAIN (design + calibrate) | embargo | TEST (touch once)`. GOLDTEN pairs use their own shorter window; report per-pair sample size.
## Rules
1. Day t: features from data <= t. Signal generated at close t, filled at settle t+1.
2. Rolling stats use window ending at t. Thresholds set on TRAIN only, then frozen.
3. Expanding or rolling re-estimation allowed only with data <= t. No full-sample normalization.
4. Universe per day: contracts with trade_date < expiry - exit_buffer, passing liquidity filter.
5. Pair alignment: see PRD 6.2 (calendar + carry adjustment). Never compare an unadjusted GOLDM near-expiry price to a far-dated GOLDTEN price.
6. Positions: gram-matched via lots; report residual grams. Sized by thinnest leg and participation cap.
7. Exits: |z| <= exit_z, stop_z, max_hold, or forced exit at exit buffer.
## Validation battery (all reported, pass or fail)
- Net Sharpe/Sortino, max drawdown, hit rate, avg trade net INR, turnover, trade count.
- Bootstrap (block) 95% CI on mean daily net P&L; permutation/placebo test (shuffled entry dates, random pairs).
- Baselines: always-flat, random-entry, naive spread threshold without liquidity/carry adjustments.
- Regression of daily strategy P&L on gold return: beta, alpha t-stat.
- Cost sensitivity + break-even cost multiple. Parameter-stability grid (neighbors of chosen params must not flip sign).
- Multiple-testing note: count of pairs/params tried, adjust (e.g. Bonferroni/Deflated Sharpe).
## Verdict logic
Edge claimed only if net alpha CI excludes zero on TEST at 1x costs AND survives 2x costs at a reduced level AND beats placebo. Otherwise verdict = "no persistent edge after costs".
## Tests
Future-shuffle invariance, determinism, cost monotonicity, attribution identity (components sum to total).
