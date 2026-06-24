def scan_short(symbol) -> dict | None:
    """
    Short signal scanner — two modes:

    MODE A — REGIME SHORT (original)
        Fires when macro regime is trending_down or volatile.
        RSI < 55 in downtrend, or RSI > 72 overbought reversal.
        Strict: requires risk_score <= -1, VIX not low.

    MODE B — INTRADAY BREAKDOWN SHORT (new)
        Fires even inside a bull market when there's a confirmed intraday
        selloff (SPY down ≥1.5% on the day) with risk-off futures and
        elevated VIX. Catches days like today where a macro catalyst
        (KOSPI crash, chip selloff, Fed shock) drives sharp intraday moves
        the longer-term regime detection completely misses.

        This is where the alpha is — regime-following bots sit out all the
        best short setups. Breakdown mode catches them.
    """
    t = tickers.get(symbol)
    if not t or t.price == 0: return None
    m = macro

    # ── Hard gates — always apply regardless of mode ──────────────────────
    if t.earnings_within_5d:              return None
    if m.fomc_24h or m.cpi_24h or m.jobs_24h: return None
    # Never short a stock with extreme positive news momentum
    if t.headline_score > 60:             return None

    # ── Determine which mode applies ──────────────────────────────────────
    spy = tickers.get("SPY")

    # Intraday breakdown conditions
    spy_down_hard   = spy and spy.change_pct <= -1.5   # SPY red > 1.5%
    spy_down_extreme = spy and spy.change_pct <= -2.5  # SPY red > 2.5%
    vix_not_low     = m.vix_regime in ("normal", "elevated", "fear")
    futures_bearish = m.risk_score <= -1
    vix_elevated    = m.vix_regime in ("elevated", "fear")

    intraday_breakdown = spy_down_hard and vix_not_low and futures_bearish

    # Existing regime-based conditions
    regime_allows_shorts = m.market_regime in ("trending_down", "volatile")

    # If neither mode is active, bail
    if not regime_allows_shorts and not intraday_breakdown:
        return None

    # ── MODE B — INTRADAY BREAKDOWN ───────────────────────────────────────
    if intraday_breakdown and not regime_allows_shorts:

        # The stock itself must be participating in the selloff
        # (don't short defensive stocks holding up well)
        if t.change_pct > -0.3:
            return None  # Stock not breaking down — skip

        # Structural requirements for breakdown short
        confirmed_down   = t.price < t.sma_50                   # Below 50-day
        overbought_break = t.rsi_14 >= 60 and t.change_pct < -1 # Selling from strength
        high_beta_break  = t.change_pct <= -2.0                  # Hard mover

        if not (confirmed_down or overbought_break or high_beta_break):
            return None

        # Volume — breakdown moves need real volume
        if t.vol_ratio < 1.2:
            return None

        # ATR gate — still need manageable volatility
        if t.atr_pct > RISK["atr_pct_max"] * 1.5:  # More lenient on breakdown days
            return None

        # Score the breakdown
        # Trend: how far below the 50-day?
        pct_below_50  = (t.sma_50 - t.price) / t.sma_50 if t.price < t.sma_50 else 0
        trend_score   = min(100, 65 + pct_below_50 * 300) if confirmed_down else 55

        # Momentum: bigger daily drop = stronger signal
        mom_score     = min(100, 50 + abs(t.change_pct) * 10)

        # Volume: more conviction on high volume
        vol_score     = min(100, 55 + (t.vol_ratio - 1.2) * 30)

        # ATR: prefer moderate volatility
        atr_score     = 100 if 0.01 <= t.atr_pct <= 0.035 else 75

        # Macro: how bad is the macro backdrop today?
        mac_score = (
            100 if spy_down_extreme and vix_elevated    else  # Panic selloff
            85  if spy_down_extreme                     else  # Hard breakdown
            75  if spy_down_hard    and vix_elevated    else  # Elevated VIX
            65                                                 # Base breakdown
        )
        if m.risk_score <= -2: mac_score = min(100, mac_score + 10)

        # Headline boost — negative news on the stock amplifies the signal
        hl_boost = max(0, -t.headline_score / 5)  # Score -60 → +12 boost

        criteria = (
            trend_score * 0.25 +
            mom_score   * 0.25 +
            vol_score   * 0.20 +
            atr_score   * 0.15 +
            mac_score   * 0.15 +
            hl_boost
        )

        # Breakdown shorts have a lower bar — the macro context is the primary
        # signal, individual stock setup is secondary
        if criteria < 52:
            return None

        # Multi-timeframe: 1H should confirm the break
        mtf_ok, mtf_reason = get_1h_confirmation(symbol, "short")
        if not mtf_ok:
            log(f"  {symbol} BREAKDOWN SHORT rejected: {mtf_reason}")
            return None

        atr    = t.atr_14 or t.price * 0.015
        stop   = round(t.price + RISK["stop_loss_atr_mult"] * atr, 2)
        target = round(t.price - RISK["take_profit_atr_mult"] * atr, 2)
        rr     = round((t.price - target) / (stop - t.price), 2) if stop > t.price else 0
        if rr < 1.5: return None

        notes = [
            f"Breakdown short: SPY {spy.change_pct:+.1f}% | VIX {m.vix:.1f} ({m.vix_regime})",
            f"Stock: {t.change_pct:+.1f}% today | RSI {t.rsi_14:.0f} | Vol {t.vol_ratio:.1f}x",
            mtf_reason,
        ]
        if t.headline_score < -20:
            notes.append(f"Negative news confirmed ({t.headline_score:+.0f})")

        return {
            "symbol":    symbol,
            "type":      "stock_short_breakdown",
            "direction": "short",
            "entry":     t.price,
            "stop":      stop,
            "target":    target,
            "rr":        rr,
            "criteria":  round(criteria, 1),
            "scores":    {
                "trend":    trend_score,
                "momentum": mom_score,
                "volume":   vol_score,
                "atr":      atr_score,
                "macro":    mac_score,
            },
            "notes":           notes,
            "headline_score":  t.headline_score,
            "reddit_trending": m.reddit_mentions.get(symbol, {}).get("trending", False),
        }

    # ── MODE A — REGIME SHORT (original logic, unchanged) ─────────────────
    # Only reaches here if regime_allows_shorts is True

    if m.market_regime == "unknown":       return None
    if macro.risk_score > -1:              return None
    if m.vix_regime == "low":              return None
    if macro.risk_score >= 2:              return None

    confirmed_down = t.price < t.sma_50 < t.sma_200
    overbought_rev = t.rsi_14 > 72 and t.price > t.sma_50

    if not confirmed_down and not overbought_rev:
        return None

    if confirmed_down:
        trend_score = min(100, 90 + (t.sma_50 - t.price) / t.sma_50 * 200)
        rsi_ok      = t.rsi_14 < 55
        mom_score   = max(0, 100 - t.rsi_14 * 1.2)
        stype       = "stock_short_downtrend"
    else:
        trend_score = 70
        rsi_ok      = t.rsi_14 > 68
        mom_score   = min(100, (t.rsi_14 - 65) * 5)
        stype       = "stock_short_reversal"

    if not rsi_ok: return None

    vol_score = min(100, 55 + (t.vol_ratio - 1) * 25)
    if t.vol_ratio < 1.3: vol_score *= 0.6
    if t.atr_pct > RISK["atr_pct_max"]: return None
    atr_score = 100 if 0.01 <= t.atr_pct <= 0.03 else 80

    mac_score = (
        100 if m.market_regime == "trending_down" else
        90  if m.market_regime == "volatile"      else
        70  if m.market_regime == "ranging"       else 30
    )
    if m.vix_regime in ("fear", "elevated"): mac_score = min(100, mac_score + 10)

    criteria = (
        trend_score * 0.25 +
        mom_score   * 0.20 +
        vol_score   * 0.20 +
        atr_score   * 0.15 +
        mac_score   * 0.20
    )
    if criteria < 55: return None

    mtf_ok, mtf_reason = get_1h_confirmation(symbol, "short")
    if not mtf_ok:
        log(f"  {symbol} SHORT rejected: {mtf_reason}")
        return None

    atr    = t.atr_14 or t.price * 0.015
    stop   = round(t.price + RISK["stop_loss_atr_mult"]   * atr, 2)
    target = round(t.price - RISK["take_profit_atr_mult"] * atr, 2)
    rr     = round((t.price - target) / (stop - t.price), 2) if stop > t.price else 0

    return {
        "symbol":    symbol,
        "type":      stype,
        "direction": "short",
        "entry":     t.price,
        "stop":      stop,
        "target":    target,
        "rr":        rr,
        "criteria":  round(criteria, 1),
        "scores":    {
            "trend":    trend_score,
            "momentum": mom_score,
            "volume":   vol_score,
            "atr":      atr_score,
            "macro":    mac_score,
        },
        "notes": [
            f"Short RSI={t.rsi_14:.1f} Vol={t.vol_ratio:.1f}x ATR={t.atr_pct:.2%}",
            mtf_reason,
        ],
        "headline_score":  t.headline_score,
        "reddit_trending": m.reddit_mentions.get(symbol, {}).get("trending", False),
    }
