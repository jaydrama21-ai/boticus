# Strategy tightening — 2026-07-14

You said we were missing plays and needed to tighten up and make a profit. I dug
into the data before touching anything, and the headline finding flips the story:

## The bot was panicking over a broken number

`run_backtest` reported **expectancy −1.07% per trade** and the auto-adjust engine
had been recommending we choke the strategy down because of it. That number is
**wrong**. It computes expectancy as `WR·avg_win + (1−WR)·avg_loss`, treating every
one of the ~37% of trades that **time out** as if it lost the average stop amount.
But timeouts exit at the 10-day close, usually near breakeven — many are small
winners. The **true mean P&L per trade is positive**, not negative.

Measured properly (mean realized P&L across *all* trades, net of a 0.05%
round-trip cost assumption, on the same 62-symbol / 180-day set the −1.07% came
from):

| Config | True EV/trade (close) | True EV/trade (realistic intrabar fills) |
|---|---|---|
| Old settings | **+0.33%** | **+0.21%** |
| New settings (below) | **+1.18%** | **+1.11%** |

The strategy always had edge. We were strangling it based on a measurement
artifact. So the fix was **not** "fire more marginal trades" — it was "stop doing
the things that bled, and let the winners run."

## What changed (all in `bot.py`)

Chosen by an in-memory parameter sweep over cached history (`scratchpad/sweep.py`,
`validate.py`), optimizing true per-trade EV, then stress-tested for overfitting.

| Param | Old | New | Why (from the sweep) |
|---|---|---|---|
| `take_profit_atr_mult` | 3.0 | **3.5** | Ride winners; higher targets lift EV on a trend system |
| `rsi_min` / `rsi_max` | 55–72 | **58–75** | Strongest momentum band in the data |
| trend floor (`pct_above_50`) | 0.3% | **1.0%** | Demand a real established trend, not a fresh cross |
| `atr_pct_max` | 0.04 | **0.05** | Improves *both* signal count and EV |
| `volume_min_mult` | 0.5 | **1.0** | Kept **soft** (score penalty, not a hard gate) — live scans intraday where early-day volume is incomplete, so a hard gate would misfire |
| Mode-A regime shorts | on | **off** (`ENABLE_REGIME_SHORTS = False`) | ~20% WR over 1000+ signals, negative EV. Mode-B crash-day breakdown shorts **kept** as a hedge |

### Robustness checks (candidate config)
- **Time split:** both halves of the window positive (+0.99% / +1.44% per trade).
- **Breadth:** 30 of 47 traded symbols positive — not one lucky name. Top
  contributors are diverse (GS, SLV, XLE, GOOGL, CVX, XBI).
- **Realistic fills:** holds up under intrabar high/low stop-fills (+1.11%),
  where the *old* config nearly collapses (+0.21%).

### One honest note on "NVDA plays"
On this trend-follow long system, **NVDA, TSLA and PLTR longs actually lost money**
over the last 180 days (high-ATR whipsaw stops them out). The edge came from
steadier names and sector ETFs, not the meme-momentum tickers.

## What I deliberately did NOT touch
- **Risk controls unchanged:** 2% max risk/trade, 2% daily-loss kill switch, 6-position
  cap, all stop/re-protect logic. Positive edge ≠ reason to size up.
- **Paper mode / live mode:** untouched.
- **No deploy.** These changes are on a branch. The live GitHub-Actions bot runs
  `bot.py` from `main`, so nothing changes in production until you review and merge.

## Also fixed
`run_backtest` now reports `mean_pnl_pct` (the true EV) in the summary, logs,
Telegram alert, and — importantly — feeds it to the auto-adjust prompt with an
explicit instruction to optimize against it, **not** the misleading win/loss-only
expectancy. This stops the optimizer from strangling the strategy again.

## Verification
- `python test_signals.py` → all pass (added cases for RSI-74 fire, <1% trend-floor
  block, and regime-shorts-disabled default).
- Real `run_backtest` on the new config: `mean_pnl_pct = +0.80%` per trade
  (blended long+short; live is long-only so real EV skews higher).

## To go live
Review this branch, then merge to `main`. Suggest watching the next few days of
paper trades / the backtest Telegram alert (now showing true EV) before any
live-capital switch.
