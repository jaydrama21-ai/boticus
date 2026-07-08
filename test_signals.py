#!/usr/bin/env python3
"""
test_signals.py — offline unit test for scan_long signal logic.

Verifies the LONG signal path in bot.py end-to-end WITHOUT any network calls.
All data is mocked; the only functions that touch the network
(get_1h_confirmation, calculate_vwap) are monkeypatched. is_opex_friday is
pinned to False so the test is deterministic regardless of the calendar.

Run:  python test_signals.py
Exit code 0 = all cases passed, 1 = a case failed.
"""

import bot

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"

_failures = 0


def check(label, condition, detail=""):
    global _failures
    tag = PASS if condition else FAIL
    if not condition:
        _failures += 1
    line = f"  [{tag}] {label}"
    if detail:
        line += f"  — {detail}"
    print(line)


def make_ticker(symbol="TEST", rsi_14=62):
    """Build a fully-mocked TickerData with a clean bullish setup."""
    t = bot.TickerData(symbol)
    t.price       = 100.0
    t.sma_50      = 97.0
    t.sma_200     = 92.0
    t.rsi_14      = rsi_14
    t.vol_ratio   = 1.4
    t.atr_14      = 1.8
    t.atr_pct     = 0.018
    t.headline_score      = 10.0
    t.earnings_within_5d  = False
    t.has_negative_news   = False
    t.adx_14      = 25.0
    return t


def setup_macro():
    """Reset global macro to a calm, trending-up, no-event state."""
    m = bot.macro
    m.market_regime = "trending_up"
    m.vix           = 16.0
    m.vix_regime    = "normal"
    m.risk_score    = 1
    m.fomc_24h      = False
    m.cpi_24h       = False
    m.jobs_24h      = False
    m.yield_curve   = 0.0
    m.reddit_mentions = {}
    m.unusual_volume  = []
    return m


def install_offline_patches():
    """Neutralize every network / date dependency reachable from scan_long."""
    bot.get_1h_confirmation = lambda symbol, direction: (True, "mocked")
    bot.calculate_vwap      = lambda symbol: None      # -> "VWAP unavailable", 0 adj
    bot.is_opex_friday      = lambda: False
    # Ensure the optional signal dicts are empty so they contribute 0.
    bot.EARNINGS_BEATS.clear()
    bot.INSIDER_SIGNALS.clear()
    bot.UNUSUAL_OPTIONS.clear()
    bot.CONGRESS_TRADES.clear()


def run():
    install_offline_patches()
    setup_macro()

    stop_mult   = bot.RISK["stop_loss_atr_mult"]
    tp_mult     = bot.RISK["take_profit_atr_mult"]

    # ── Case 1: healthy bullish setup — expect a LONG signal ──────────────────
    print("Case 1: bullish setup (RSI 62) — expect signal FIRES")
    t = make_ticker("TEST", rsi_14=62)
    bot.tickers = {"TEST": t}

    sig = bot.scan_long("TEST")

    check("scan_long returns a signal (not None)", sig is not None,
          f"got {type(sig).__name__ if sig is None else 'dict'}")

    if sig is not None:
        exp_stop   = round(t.price - stop_mult * t.atr_14, 2)
        exp_target = round(t.price + tp_mult   * t.atr_14, 2)
        exp_rr     = round((exp_target - t.price) / (t.price - exp_stop), 2)

        check("direction == long", sig["direction"] == "long", sig["direction"])
        check("entry == 100.0", sig["entry"] == 100.0, sig["entry"])
        check(f"stop == {exp_stop} (100 - {stop_mult}*1.8)",
              sig["stop"] == exp_stop, sig["stop"])
        check(f"target == {exp_target} (100 + {tp_mult}*1.8)",
              sig["target"] == exp_target, sig["target"])
        check(f"rr == {exp_rr}", sig["rr"] == exp_rr, sig["rr"])
    print()

    # ── Case 2: same setup but RSI 45 — below rsi_min gate — must NOT fire ─────
    print("Case 2: weak momentum (RSI 45) — expect signal does NOT fire")
    t2 = make_ticker("TEST2", rsi_14=45)
    bot.tickers = {"TEST2": t2}

    sig2 = bot.scan_long("TEST2")
    check(f"RSI 45 < rsi_min ({bot.RISK['rsi_min']}) so scan_long returns None",
          sig2 is None, "signal fired unexpectedly" if sig2 else "None")
    print()

    print("=" * 60)
    if _failures == 0:
        print(f"{PASS}: all assertions passed")
        return 0
    print(f"{FAIL}: {_failures} assertion(s) failed")
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(run())
