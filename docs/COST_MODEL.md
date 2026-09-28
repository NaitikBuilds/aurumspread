# Cost Model
Fills are NOT settlement prices. For every leg:
`fill = settle_{t+1} +/- slippage` (buy pays up, sell receives less).
1. Slippage: ticks_per_side x tick_size (config/costs.yaml); doubled on thin days; bigger orders (participation > threshold) rejected or scaled down.
2. Fees per leg per side: brokerage + exchange charge + CTT (sell side) + SEBI + stamp + GST on fees. Values in `costs.yaml` only; all currently unverified.
3. Round-trip cost per gram is computed for every candidate trade BEFORE entry and fed into LAMS.
4. Multi-leg trades pay costs on every leg, entry and exit (typically 4 legs round trip minimum).
5. Sensitivity: results reported at 0.5x, 1x, 2x, 3x costs. State the break-even cost multiple.
6. P&L is computed from prices of the contracts actually held, in INR, using real lot sizes.
7. Financing/margin: report return on margin as well as INR P&L (margins in config, verified from MCX/broker).
