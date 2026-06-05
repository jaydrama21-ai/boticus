"""
bot.py — Trading Bot
Single-file deployment version for GitHub Actions.
Runs once per trigger, does its job, exits cleanly.
Alpaca bracket orders handle stop/target monitoring 24/7 server-side.
"""

import os, sys, json, time, re, math, requests
import numpy as np
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import yfinance as yf
import anthropic

ET = ZoneInfo("America/New_York")

# ── Keys from environment ──────────────────────────────────────────────────────
ALPACA_KEY      = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET   = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE     = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
ALPACA_DATA     = "https://data.alpaca.markets"
FRED_KEY        = os.environ.get("FRED_API_KEY", "")
ANTHROPIC_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
PAPER_MODE      = os.environ.get("PAPER_MODE", "true").lower() == "true"
ACCOUNT_EQUITY  = float(os.environ.get("ACCOUNT_EQUITY", "100000"))

ALPACA_HEADERS  = {
    "APCA-API-KEY-ID":     ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
    "Content-Type":        "application/json",
}

ai_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
OPUS_MODEL   = "claude-opus-4-5"
SONNET_MODEL = "claude-sonnet-4-5"

# ── Watchlist ──────────────────────────────────────────────────────────────────
WATCHLIST       = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "AMD", "TSLA"]
OPTIONS_TICKERS = ["SPY", "QQQ", "AAPL", "NVDA", "TSLA"]

# ── Risk config ────────────────────────────────────────────────────────────────
RISK = {
    "stop_loss_atr_mult":    1.5,
    "take_profit_atr_mult":  2.5,
    "max_position_pct":      0.05,
    "max_risk_per_trade_pct":0.02,
    "max_daily_loss_pct":    0.02,
    "max_open_positions":    6,
    "rsi_min":               40,
    "rsi_max":               72,
    "volume_min_mult":       1.1,
    "atr_pct_max":           0.04,
}

# ── State files (persisted via GitHub Actions cache) ──────────────────────────
STATE_DIR   = Path(os.environ.get("STATE_DIR", "/tmp/bot_state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
TRADES_FILE   = STATE_DIR / "trades.json"
FEEDBACK_FILE = STATE_DIR / "feedback.json"
LOG_FILE      = STATE_DIR / "bot.log"


# ══════════════════════════════════════════════════════════════════════════════
# LOGGING
# ══════════════════════════════════════════════════════════════════════════════

def log(msg, level="INFO"):
    ts  = datetime.now(ET).strftime("%H:%M:%S")
    out = f"[{ts}] {level:5} {msg}"
    print(out)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(out + "\n")
    except: pass


# ══════════════════════════════════════════════════════════════════════════════
# STATE PERSISTENCE
# Trades and feedback persist between GitHub Actions runs via cache
# ══════════════════════════════════════════════════════════════════════════════

def load_trades() -> list:
    try:
        if TRADES_FILE.exists():
            return json.loads(TRADES_FILE.read_text())
    except: pass
    return []

def save_trades(trades: list):
    try:
        TRADES_FILE.write_text(json.dumps(trades, indent=2, default=str))
    except Exception as e:
        log(f"Trade save error: {e}", "ERROR")

def load_feedback() -> list:
    try:
        if FEEDBACK_FILE.exists():
            return json.loads(FEEDBACK_FILE.read_text())
    except: pass
    return []

def save_feedback(feedback: list):
    try:
        FEEDBACK_FILE.write_text(json.dumps(feedback, indent=2, default=str))
    except Exception as e:
        log(f"Feedback save error: {e}", "ERROR")

def log_trade_outcome(trade: dict):
    feedback = load_feedback()
    feedback.append({
        "date":           trade.get("opened_at", "")[:10],
        "symbol":         trade["symbol"],
        "direction":      trade["direction"],
        "entry":          trade["entry_price"],
        "exit":           trade.get("closed_price", 0),
        "pnl_pct":        trade.get("pnl_pct", 0),
        "pnl_dollar":     trade.get("pnl", 0),
        "result":         "win" if trade.get("pnl", 0) > 0 else "loss",
        "close_reason":   trade.get("close_reason", ""),
        "criteria_score": trade.get("criteria", 0),
        "ai_score":       trade.get("ai_score", 0),
        "vix":            trade.get("vix", 0),
        "regime":         trade.get("regime", ""),
    })
    save_feedback(feedback)
    log(f"Trade outcome logged: {trade['symbol']} {trade.get('close_reason','')} {trade.get('pnl_pct',0):+.1f}%")


# ══════════════════════════════════════════════════════════════════════════════
# PATTERN MEMORY
# ══════════════════════════════════════════════════════════════════════════════

def build_pattern_memory() -> str:
    feedback = load_feedback()
    if len(feedback) < 3:
        return f"Limited history ({len(feedback)} trades) — early stage system."
    wins   = [t for t in feedback if t["result"] == "win"]
    losses = [t for t in feedback if t["result"] == "loss"]
    total  = len(feedback)
    wr     = len(wins) / total * 100
    aw     = sum(t["pnl_pct"] for t in wins)   / len(wins)   if wins   else 0
    al     = sum(t["pnl_pct"] for t in losses)  / len(losses) if losses else 0
    wc     = sum(t["criteria_score"] for t in wins)   / len(wins)   if wins   else 0
    lc     = sum(t["criteria_score"] for t in losses)  / len(losses) if losses else 0
    regime_stats = {}
    for t in feedback:
        r = t.get("regime", "unknown")
        if r not in regime_stats: regime_stats[r] = {"w": 0, "l": 0}
        regime_stats[r]["w" if t["result"] == "win" else "l"] += 1
    rlines = []
    for r, s in regime_stats.items():
        tot = s["w"] + s["l"]
        rlines.append(f"  {r}: {s['w']/tot*100:.0f}% WR ({tot} trades)")
    hv    = [t for t in feedback if t.get("vix", 0) > 25]
    lv    = [t for t in feedback if t.get("vix", 0) <= 25]
    hv_wr = sum(1 for t in hv if t["result"]=="win")/len(hv)*100 if hv else 0
    lv_wr = sum(1 for t in lv if t["result"]=="win")/len(lv)*100 if lv else 0
    streak = " ".join(["W" if t["result"]=="win" else "L" for t in feedback[-5:]])
    stopped = len([t for t in feedback if "STOP"   in t.get("close_reason", "")])
    targets = len([t for t in feedback if "TARGET" in t.get("close_reason", "")])
    adj = []
    if wr < 40 and total >= 10: adj.append("Win rate <40% — be stricter")
    if wr > 65 and total >= 10: adj.append("Strong win rate — criteria calibrated")
    if lc > wc and total >= 5:  adj.append("Losses have higher criteria scores — recalibrate")
    if hv_wr < 30 and len(hv) >= 3: adj.append("Poor high-VIX performance — avoid VIX>25")
    if stopped > targets * 2:   adj.append("Stops hit 2x targets — adjust levels")
    if not adj: adj.append("No significant patterns yet")
    return "\n".join([
        f"=== PATTERN MEMORY ({total} trades) ===",
        f"Win rate: {wr:.0f}% | Avg win: {aw:+.1f}% | Avg loss: {al:+.1f}%",
        f"Recent 5: {streak} | Stops: {stopped} | Targets: {targets}",
        f"Win criteria avg: {wc:.0f} vs loss: {lc:.0f}",
        "By regime:", *rlines,
        f"VIX<=25: {lv_wr:.0f}% WR | VIX>25: {hv_wr:.0f}% WR",
        f"LEARNED: {' | '.join(adj)}",
    ])


# ══════════════════════════════════════════════════════════════════════════════
# TECHNICAL INDICATORS
# ══════════════════════════════════════════════════════════════════════════════

def calc_rsi(closes, period=14):
    if len(closes) < period + 1: return 50.0
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    ag, al = np.mean(gains[:period]), np.mean(losses[:period])
    for i in range(period, len(deltas)):
        ag = (ag * (period-1) + gains[i])  / period
        al = (al * (period-1) + losses[i]) / period
    return round(100 - (100 / (1 + ag/al)), 1) if al else 100.0

def calc_atr(highs, lows, closes, period=14):
    if len(closes) < period + 1: return 0.0
    tr  = [max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
           for i in range(1, len(closes))]
    atr = np.mean(tr[:period])
    for i in range(period, len(tr)): atr = (atr*(period-1) + tr[i]) / period
    return round(float(atr), 4)


# ══════════════════════════════════════════════════════════════════════════════
# MARKET DATA
# ══════════════════════════════════════════════════════════════════════════════

class TickerData:
    def __init__(self, symbol):
        self.symbol = symbol
        self.price = self.prev_close = self.change_pct = 0.0
        self.volume = self.avg_vol = self.vol_ratio = 0.0
        self.sma_50 = self.sma_200 = self.rsi_14 = 0.0
        self.atr_14 = self.atr_pct = 0.0
        self.iv_rank = self.implied_move = 0.0
        self.earnings_within_5d = False
        self.earnings_date = None
        self.has_negative_news = False
        self.headlines = []

class MacroData:
    def __init__(self):
        self.fed_funds = self.cpi_yoy = self.unemployment = 0.0
        self.yield_curve = self.vix = 20.0
        self.vix_regime = "normal"
        self.market_regime = "unknown"
        self.fomc_24h = self.cpi_24h = self.jobs_24h = False
        self.upcoming_events = []
        self.futures_sentiment = "NEUTRAL"
        self.risk_score = 0

tickers: dict[str, TickerData] = {}
macro = MacroData()

NEG_KEYWORDS = [
    "fraud","sec investigation","bankruptcy","recall","downgrade",
    "guidance cut","earnings miss","layoff","lawsuit","restatement","delisting",
]

def get_market_session() -> str:
    now = datetime.now(ET)
    h   = now.hour + now.minute / 60
    if now.weekday() >= 5: return "closed"
    if 4 <= h < 9.5:  return "pre_market"
    if 9.5 <= h < 16: return "open"
    if 16 <= h < 20:  return "after_hours"
    return "closed"

def fetch_price_data():
    log(f"Fetching price data for {WATCHLIST}...")
    for symbol in WATCHLIST:
        try:
            tick  = yf.Ticker(symbol)
            hist  = tick.history(period="1y")
            if hist.empty: continue
            closes  = hist["Close"].values
            highs   = hist["High"].values
            lows    = hist["Low"].values
            volumes = hist["Volume"].values
            price      = float(closes[-1])
            prev_close = float(closes[-2]) if len(closes) > 1 else price
            change_pct = (price - prev_close) / prev_close * 100
            volume     = float(volumes[-1])
            avg_vol    = float(np.mean(volumes[-20:]))
            vol_ratio  = volume / avg_vol if avg_vol else 0
            sma_50     = float(np.mean(closes[-50:]))  if len(closes) >= 50  else 0
            sma_200    = float(np.mean(closes[-200:])) if len(closes) >= 200 else 0
            rsi        = calc_rsi(closes)
            atr        = calc_atr(highs, lows, closes)
            atr_pct    = atr / price if price else 0
            # Earnings
            earnings_5d = False
            earnings_dt = None
            try:
                cal = tick.calendar
                if cal is not None and not cal.empty and "Earnings Date" in cal.index:
                    ed = cal.loc["Earnings Date"]
                    if hasattr(ed, "iloc"): ed = ed.iloc[0]
                    if hasattr(ed, "date"): ed = ed.date()
                    days = (ed - date.today()).days
                    earnings_5d = 0 <= days <= 5
                    earnings_dt = str(ed)
            except: pass
            # News via Alpaca
            headlines = []; has_neg = False
            try:
                since = (datetime.now(ET) - timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ")
                r = requests.get(
                    f"{ALPACA_DATA}/v1beta1/news",
                    headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
                    params={"symbols": symbol, "start": since, "limit": 10},
                    timeout=8
                )
                if r.ok:
                    articles = r.json().get("news", [])
                    headlines = [a["headline"] for a in articles[:5]]
                    has_neg   = any(kw in h.lower() for h in headlines for kw in NEG_KEYWORDS)
            except: pass
            t = TickerData(symbol)
            t.price = round(price, 2);  t.prev_close = round(prev_close, 2)
            t.change_pct = round(change_pct, 2)
            t.volume = volume; t.avg_vol = avg_vol
            t.vol_ratio = round(vol_ratio, 2)
            t.sma_50  = round(sma_50, 2);  t.sma_200 = round(sma_200, 2)
            t.rsi_14  = rsi; t.atr_14 = atr; t.atr_pct = round(atr_pct, 4)
            t.iv_rank = 50.0; t.implied_move = round(atr_pct * 2, 3)
            t.earnings_within_5d = earnings_5d; t.earnings_date = earnings_dt
            t.has_negative_news  = has_neg; t.headlines = headlines
            tickers[symbol] = t
            trend = "↑" if price > sma_50 > sma_200 else "↓" if price < sma_50 else "→"
            earn  = " ⚠️EARN" if earnings_5d else ""
            neg   = " 🔴NEG"  if has_neg   else ""
            log(f"  {symbol:6} ${price:7.2f} ({change_pct:+.1f}%)  "
                f"RSI:{rsi:5.1f}  ATR:{atr_pct:.2%}  Vol:{vol_ratio:.1f}x  {trend}{earn}{neg}")
        except Exception as e:
            log(f"  {symbol}: {e}", "WARN")

def fetch_macro():
    log("Fetching macro data...")
    def fred(series_id, limit=14):
        try:
            r = requests.get(
                "https://api.stlouisfed.org/fred/series/observations",
                params={"series_id": series_id, "api_key": FRED_KEY,
                        "file_type": "json", "sort_order": "desc",
                        "limit": limit, "observation_start": "2020-01-01"},
                timeout=8)
            return [o for o in r.json().get("observations", []) if o.get("value", ".") != "."]
        except: return []
    def latest(sid):
        obs = fred(sid, 5)
        return float(obs[0]["value"]) if obs else 0.0
    macro.fed_funds    = latest("FEDFUNDS")
    macro.unemployment = latest("UNRATE")
    macro.yield_curve  = latest("T10Y2Y")
    cpi_obs = fred("CPIAUCSL", 14)
    macro.cpi_yoy = round((float(cpi_obs[0]["value"]) - float(cpi_obs[12]["value"])) /
                           float(cpi_obs[12]["value"]) * 100, 2) if len(cpi_obs) >= 13 else 0.0
    try:
        vix_h  = yf.Ticker("^VIX").history(period="2d")
        macro.vix = float(vix_h["Close"].iloc[-1]) if not vix_h.empty else 20.0
    except: macro.vix = 20.0
    macro.vix_regime = ("low" if macro.vix < 15 else "normal" if macro.vix < 25
                        else "elevated" if macro.vix < 35 else "fear")
    # Regime
    spy = tickers.get("SPY")
    if spy and spy.price > spy.sma_50 > spy.sma_200 and 50 <= spy.rsi_14 <= 70 and macro.vix < 25:
        macro.market_regime = "trending_up"
    elif spy and spy.price < spy.sma_50 < spy.sma_200:
        macro.market_regime = "trending_down"
    elif macro.vix > 30: macro.market_regime = "volatile"
    else: macro.market_regime = "ranging"
    # Events
    today   = date.today().isoformat()
    cutoff  = (date.today() + timedelta(days=1)).isoformat()
    FOMC = ["2026-06-17","2026-07-29","2026-09-16","2026-11-04","2026-12-16"]
    CPI  = ["2026-06-11","2026-07-14","2026-08-12","2026-09-11","2026-10-14"]
    JOBS = ["2026-06-05","2026-07-10","2026-08-07","2026-09-04","2026-10-02"]
    macro.fomc_24h = any(today <= d <= cutoff for d in FOMC)
    macro.cpi_24h  = any(today <= d <= cutoff for d in CPI)
    macro.jobs_24h = any(today <= d <= cutoff for d in JOBS)
    # Futures sentiment
    try:
        es_h = yf.Ticker("ES=F").history(period="2d")
        nq_h = yf.Ticker("NQ=F").history(period="2d")
        gc_h = yf.Ticker("GC=F").history(period="2d")
        es_c = (float(es_h["Close"].iloc[-1])-float(es_h["Close"].iloc[-2]))/float(es_h["Close"].iloc[-2])*100 if len(es_h)>=2 else 0
        nq_c = (float(nq_h["Close"].iloc[-1])-float(nq_h["Close"].iloc[-2]))/float(nq_h["Close"].iloc[-2])*100 if len(nq_h)>=2 else 0
        gc_c = (float(gc_h["Close"].iloc[-1])-float(gc_h["Close"].iloc[-2]))/float(gc_h["Close"].iloc[-2])*100 if len(gc_h)>=2 else 0
        avg  = (es_c + nq_c) / 2
        macro.risk_score = (2 if avg > 0.5 else -2 if avg < -0.5 else 1 if avg > 0.2 else -1 if avg < -0.2 else 0)
        if gc_c > 0.3: macro.risk_score -= 1
        if gc_c < -0.3: macro.risk_score += 1
        macro.futures_sentiment = (
            "RISK-ON"  if macro.risk_score >= 2 else
            "RISK-OFF" if macro.risk_score <= -2 else
            "MILDLY-RISK-ON"  if macro.risk_score == 1 else
            "MILDLY-RISK-OFF" if macro.risk_score == -1 else "NEUTRAL"
        )
        log(f"  Futures: ES={es_c:+.2f}% NQ={nq_c:+.2f}% Gold={gc_c:+.2f}% → {macro.futures_sentiment}")
    except Exception as e:
        log(f"  Futures: {e}", "WARN")
    log(f"  Fed:{macro.fed_funds:.2f}% CPI:{macro.cpi_yoy:.1f}% "
        f"VIX:{macro.vix:.1f}({macro.vix_regime}) Regime:{macro.market_regime}")
    if macro.fomc_24h or macro.cpi_24h or macro.jobs_24h:
        events = [e for e,f in [("FOMC",macro.fomc_24h),("CPI",macro.cpi_24h),("Jobs",macro.jobs_24h)] if f]
        log(f"  ⚠️  HIGH-IMPACT EVENT TODAY: {events}", "WARN")

def build_context(symbol=None) -> str:
    m = macro
    lines = [
        "=== MARKET CONTEXT ===",
        f"VIX: {m.vix:.1f} ({m.vix_regime}) | Regime: {m.market_regime}",
        f"Futures: {m.futures_sentiment} (risk score: {m.risk_score:+d})",
        f"Fed: {m.fed_funds:.2f}% | CPI: {m.cpi_yoy:.1f}% | Unemployment: {m.unemployment:.1f}%",
        f"Yield curve: {m.yield_curve:+.2f}% ({'INVERTED' if m.yield_curve < 0 else 'normal'})",
        f"Session: {get_market_session()}",
    ]
    if m.fomc_24h or m.cpi_24h or m.jobs_24h:
        lines.append("⚠️ HIGH-IMPACT EVENT IN 24H")
    if symbol and symbol in tickers:
        t = tickers[symbol]
        lines += [
            f"\n=== {symbol} ===",
            f"Price: ${t.price:.2f} ({t.change_pct:+.2f}%)",
            f"RSI: {t.rsi_14:.1f} | ATR: {t.atr_pct:.2%} | Vol ratio: {t.vol_ratio:.2f}x",
            f"SMA50: ${t.sma_50:.2f} | SMA200: ${t.sma_200:.2f}",
            f"Trend: {'UPTREND' if t.price > t.sma_50 > t.sma_200 else 'DOWNTREND' if t.price < t.sma_50 < t.sma_200 else 'MIXED'}",
            f"Earnings within 5d: {t.earnings_within_5d}",
        ]
        if t.headlines:
            lines.append("Headlines:")
            for h in t.headlines[:3]: lines.append(f"  • {h}")
    lines.append("\n=== WATCHLIST ===")
    for sym, tick in tickers.items():
        if sym == symbol: continue
        lines.append(f"{sym}: ${tick.price:.2f} ({tick.change_pct:+.1f}%) RSI:{tick.rsi_14:.0f}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
# SIGNAL ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def scan_long(symbol) -> dict | None:
    t = tickers.get(symbol)
    if not t or t.price == 0: return None
    m = macro
    # Hard gates
    if t.earnings_within_5d: return None
    if t.has_negative_news:  return None
    if m.fomc_24h or m.cpi_24h or m.jobs_24h: return None
    if macro.risk_score <= -2: return None  # RISK-OFF — no longs
    # Trend
    if not (t.price > t.sma_50 > t.sma_200): return None
    trend_score = min(100, 90 + (t.price - t.sma_50) / t.sma_50 * 200)
    # RSI
    if not (RISK["rsi_min"] <= t.rsi_14 <= RISK["rsi_max"]): return None
    mom_score = 100 - abs(t.rsi_14 - 52) * 2.5
    # Volume
    vol_score = min(100, 55 + (t.vol_ratio - 1) * 25)
    if t.vol_ratio < RISK["volume_min_mult"]: vol_score *= 0.5
    # ATR
    if t.atr_pct > RISK["atr_pct_max"]: return None
    atr_score = 100 if 0.01 <= t.atr_pct <= 0.03 else 80
    # Macro
    mac_score = (100 if m.market_regime == "trending_up" else
                 80  if m.market_regime == "ranging" else
                 50  if m.market_regime == "volatile" else 20)
    if m.vix_regime == "fear":     mac_score -= 30
    elif m.vix_regime == "elevated": mac_score -= 15
    if m.yield_curve < -0.5:       mac_score -= 10
    criteria = (trend_score*0.25 + mom_score*0.20 + vol_score*0.20 +
                atr_score*0.15 + mac_score*0.20)
    if criteria < 55: return None
    atr    = t.atr_14 or t.price * 0.015
    stop   = round(t.price - RISK["stop_loss_atr_mult"]   * atr, 2)
    target = round(t.price + RISK["take_profit_atr_mult"] * atr, 2)
    rr     = round((target - t.price) / (t.price - stop), 2) if t.price > stop else 0
    return {
        "symbol": symbol, "type": "stock_long", "direction": "long",
        "entry": t.price, "stop": stop, "target": target, "rr": rr,
        "criteria": round(criteria, 1),
        "scores": {"trend": trend_score, "momentum": mom_score,
                   "volume": vol_score, "atr": atr_score, "macro": mac_score},
        "notes": [f"Trend↑ RSI={t.rsi_14:.1f} Vol={t.vol_ratio:.1f}x ATR={t.atr_pct:.2%}"],
    }

def scan_short(symbol) -> dict | None:
    t = tickers.get(symbol)
    if not t or t.price == 0: return None
    m = macro
    if t.earnings_within_5d: return None
    if m.fomc_24h or m.cpi_24h or m.jobs_24h: return None
    if macro.risk_score >= 2: return None  # RISK-ON — no shorts
    confirmed_down = t.price < t.sma_50 < t.sma_200
    overbought_rev = t.rsi_14 > 72 and t.price > t.sma_50
    if not confirmed_down and not overbought_rev: return None
    if confirmed_down:
        trend_score = min(100, 90 + (t.sma_50 - t.price) / t.sma_50 * 200)
        rsi_ok      = t.rsi_14 < 55
        mom_score   = max(0, 100 - t.rsi_14 * 1.2)
        stype       = "stock_short_downtrend"
    else:
        trend_score = 70; rsi_ok = t.rsi_14 > 68
        mom_score   = min(100, (t.rsi_14 - 65) * 5)
        stype       = "stock_short_reversal"
    if not rsi_ok: return None
    vol_score = min(100, 55 + (t.vol_ratio - 1) * 25)
    if t.vol_ratio < 1.3: vol_score *= 0.6
    if t.atr_pct > RISK["atr_pct_max"]: return None
    atr_score = 100 if 0.01 <= t.atr_pct <= 0.03 else 80
    mac_score = (100 if m.market_regime == "trending_down" else
                 90  if m.market_regime == "volatile" else
                 70  if m.market_regime == "ranging" else 30)
    if m.vix_regime in ("fear", "elevated"): mac_score = min(100, mac_score + 10)
    criteria = (trend_score*0.25 + mom_score*0.20 + vol_score*0.20 +
                atr_score*0.15 + mac_score*0.20)
    if criteria < 55: return None
    atr    = t.atr_14 or t.price * 0.015
    stop   = round(t.price + RISK["stop_loss_atr_mult"]   * atr, 2)
    target = round(t.price - RISK["take_profit_atr_mult"] * atr, 2)
    rr     = round((t.price - target) / (stop - t.price), 2) if stop > t.price else 0
    return {
        "symbol": symbol, "type": stype, "direction": "short",
        "entry": t.price, "stop": stop, "target": target, "rr": rr,
        "criteria": round(criteria, 1),
        "scores": {"trend": trend_score, "momentum": mom_score,
                   "volume": vol_score, "atr": atr_score, "macro": mac_score},
        "notes": [f"Short RSI={t.rsi_14:.1f} Vol={t.vol_ratio:.1f}x ATR={t.atr_pct:.2%}"],
    }

def scan_all() -> list:
    session = get_market_session()
    log(f"Scanning {len(WATCHLIST)} tickers | {session} | Regime:{macro.market_regime} | VIX:{macro.vix:.1f}")
    signals = []
    for sym in WATCHLIST:
        for fn in (scan_long, scan_short):
            s = fn(sym)
            if s:
                signals.append(s)
                log(f"  {'↑' if s['direction']=='long' else '↓'} SIGNAL: "
                    f"{s['symbol']} [{s['criteria']:.0f}] entry:${s['entry']:.2f} "
                    f"stop:${s['stop']:.2f} target:${s['target']:.2f} R/R:{s['rr']:.2f}")
    signals.sort(key=lambda x: x["criteria"], reverse=True)
    log(f"Scan complete: {len(signals)} signal(s)")
    return signals


# ══════════════════════════════════════════════════════════════════════════════
# AI BRAIN
# ══════════════════════════════════════════════════════════════════════════════

SYSTEM_PROMPT = (
    "You are a disciplined trading signal analyst. Trade BOTH directions.\n"
    "You have pattern memory and full market context. USE BOTH.\n"
    "Output ONLY valid JSON — no markdown, no preamble.\n\n"
    "Format: {\"score\":0-100,\"confidence\":\"low|medium|high\","
    "\"recommendation\":\"take|skip|reduce_size\","
    "\"risk_flag\":null or \"caution\" or \"abort\","
    "\"reasoning\":\"2-3 sentences\","
    "\"key_positives\":[\"x\"],\"key_risks\":[\"x\"],"
    "\"suggested_size_adjustment\":1.0,"
    "\"pattern_insight\":\"one sentence\"}\n\n"
    "80-100=take, 65-79=good, 55-64=marginal, 0-54=skip.\n"
    "LONG: needs uptrend + RSI 40-65 + volume + low VIX + RISK-ON or NEUTRAL futures.\n"
    "SHORT: needs downtrend OR overbought + risk-off + elevated VIX + RISK-OFF futures.\n"
    "Protect capital first. Never rationalize a bad trade."
)

def score_signal(sig: dict) -> dict:
    log(f"AI scoring {sig['symbol']} ({sig['direction'].upper()})...")
    memory  = build_pattern_memory()
    context = build_context(sig["symbol"])
    s       = sig["scores"]
    prompt  = (
        f"PATTERN MEMORY:\n{memory}\n\n"
        f"MARKET CONTEXT:\n{context}\n\n"
        f"SIGNAL: {sig['symbol']} {sig['direction'].upper()} {sig['type']}\n"
        f"Entry:${sig['entry']:.2f} Stop:${sig['stop']:.2f} "
        f"Target:${sig['target']:.2f} R/R:{sig['rr']:.2f}\n"
        f"Criteria: Trend={s['trend']:.0f} Mom={s['momentum']:.0f} "
        f"Vol={s['volume']:.0f} ATR={s['atr']:.0f} Macro={s['macro']:.0f} "
        f"TOTAL={sig['criteria']:.0f}\n"
        f"Notes: {' | '.join(sig['notes'][:3])}"
    )
    try:
        resp   = ai_client.messages.create(
            model=OPUS_MODEL, max_tokens=700,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        result = json.loads(raw.strip())
        sig["ai_score"]      = float(result.get("score", 50))
        sig["ai_rec"]        = result.get("recommendation", "skip")
        sig["ai_conf"]       = result.get("confidence", "medium")
        sig["ai_flag"]       = result.get("risk_flag")
        sig["ai_reasoning"]  = result.get("reasoning", "")
        sig["ai_pattern"]    = result.get("pattern_insight", "")
        sig["size_adj"]      = result.get("suggested_size_adjustment", 1.0)
        approved = (sig["ai_flag"] != "abort" and
                    sig["ai_rec"] != "skip" and
                    sig["ai_score"] >= 55)
        sig["approved"] = approved
        e  = "✅ APPROVED" if approved else "❌ REJECTED"
        de = "LONG" if sig["direction"] == "long" else "SHORT"
        log(f"  {e} {de}: score={sig['ai_score']:.0f} {sig['ai_rec']} "
            f"conf={sig['ai_conf']} size={sig['size_adj']}x")
        log(f"  {sig['ai_reasoning']}")
        if sig["ai_pattern"]: log(f"  Pattern: {sig['ai_pattern']}")
        if result.get("key_risks"): log(f"  Risks: {result['key_risks']}")
        if sig["ai_flag"]: log(f"  FLAG: {sig['ai_flag']}", "WARN")
        log(f"  Tokens: {resp.usage.input_tokens}in / {resp.usage.output_tokens}out")
    except json.JSONDecodeError as e:
        log(f"  JSON parse error: {e}", "ERROR")
        sig["approved"] = False; sig["ai_rec"] = "skip"
    except Exception as e:
        log(f"  AI error: {e}", "ERROR")
        sig["approved"] = False; sig["ai_rec"] = "skip"
    return sig


# ══════════════════════════════════════════════════════════════════════════════
# EXECUTION
# ══════════════════════════════════════════════════════════════════════════════

def get_account() -> dict:
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/account",
                         headers=ALPACA_HEADERS, timeout=8)
        if r.ok:
            a = r.json()
            return {"equity": float(a.get("equity", 0)),
                    "buying_power": float(a.get("buying_power", 0)),
                    "status": a.get("status")}
    except Exception as e:
        log(f"Account fetch: {e}", "WARN")
    return {"equity": ACCOUNT_EQUITY, "buying_power": ACCOUNT_EQUITY}

def get_open_positions() -> list:
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/positions",
                         headers=ALPACA_HEADERS, timeout=8)
        if r.ok: return r.json()
    except: pass
    return []

def execute_signal(sig: dict, equity: float) -> bool:
    if not sig.get("approved"): return False
    rps    = abs(sig["entry"] - sig["stop"])
    if rps <= 0: return False
    shares = max(1, int(equity * RISK["max_risk_per_trade_pct"] / rps))
    shares = min(shares, int(equity * RISK["max_position_pct"] / sig["entry"]))
    shares = max(1, int(shares * sig.get("size_adj", 1.0)))
    side   = "buy" if sig["direction"] == "long" else "sell"
    order  = {
        "symbol":        sig["symbol"],
        "qty":           str(shares),
        "side":          side,
        "type":          "market",
        "time_in_force": "day",
        "order_class":   "bracket",
        "stop_loss":     {"stop_price":  str(round(sig["stop"], 2))},
        "take_profit":   {"limit_price": str(round(sig["target"], 2))},
    }
    try:
        r = requests.post(f"{ALPACA_BASE}/v2/orders",
                          headers=ALPACA_HEADERS, json=order, timeout=10)
        if r.status_code in (200, 201):
            data     = r.json()
            order_id = data.get("id", "unknown")
            log(f"  ✅ ORDER SENT: {sig['symbol']} {side.upper()} ×{shares} "
                f"@ ${sig['entry']:.2f} | stop:${sig['stop']:.2f} "
                f"target:${sig['target']:.2f} | id:{order_id}")
            # Save to trades file
            trades = load_trades()
            trades.append({
                "order_id":    order_id,
                "symbol":      sig["symbol"],
                "direction":   sig["direction"],
                "signal_type": sig["type"],
                "entry_price": sig["entry"],
                "stop_loss":   sig["stop"],
                "take_profit": sig["target"],
                "shares":      shares,
                "risk_amount": round(shares * rps, 2),
                "criteria":    sig["criteria"],
                "ai_score":    sig.get("ai_score"),
                "ai_rec":      sig.get("ai_rec"),
                "vix":         macro.vix,
                "regime":      macro.market_regime,
                "futures":     macro.futures_sentiment,
                "status":      "open",
                "opened_at":   datetime.now(ET).isoformat(),
                "paper_mode":  PAPER_MODE,
            })
            save_trades(trades)
            return True
        else:
            log(f"  ❌ Order failed {r.status_code}: {r.text[:150]}", "ERROR")
    except Exception as e:
        log(f"  ❌ Execution error: {e}", "ERROR")
    return False

def check_daily_loss(equity: float) -> bool:
    """Returns True if kill switch should activate."""
    trades = load_trades()
    today  = date.today().isoformat()
    today_closed = [t for t in trades
                    if t.get("status") == "closed"
                    and t.get("closed_at", "")[:10] == today]
    if not today_closed: return False
    day_pnl     = sum(t.get("pnl", 0) for t in today_closed)
    loss_pct    = day_pnl / equity if equity else 0
    if loss_pct <= -RISK["max_daily_loss_pct"]:
        log(f"KILL SWITCH: {loss_pct:.2%} daily loss (${day_pnl:.2f})", "WARN")
        return True
    return False


# ══════════════════════════════════════════════════════════════════════════════
# DAILY REVIEW (runs post-close)
# ══════════════════════════════════════════════════════════════════════════════

def generate_daily_review():
    log("Generating daily review...")
    trades   = load_trades()
    today    = date.today().isoformat()
    today_t  = [t for t in trades if t.get("opened_at", "")[:10] == today]
    closed   = [t for t in today_t if t.get("status") == "closed"]
    wins     = [t for t in closed if t.get("pnl", 0) > 0]
    total_pl = sum(t.get("pnl", 0) for t in closed)
    trade_lines = "\n".join([
        f"  {t['symbol']} {t['direction']} | entry=${t['entry_price']:.2f} "
        f"| P&L:{t.get('pnl_pct',0):+.1f}% (${t.get('pnl',0):+.2f}) "
        f"| AI:{t.get('ai_score','N/A')} | {t.get('close_reason','open')}"
        for t in today_t
    ]) or "  No trades today"
    msg = (
        f"Date: {today}\n"
        f"Regime: {macro.market_regime} | VIX: {macro.vix:.1f} | "
        f"Futures: {macro.futures_sentiment}\n\n"
        f"TRADES TODAY:\n{trade_lines}\n\n"
        f"Stats: {len(today_t)} total | {len(wins)}W/{len(closed)-len(wins)}L | "
        f"P&L: ${total_pl:+.2f}\n\n"
        f"Fed: {macro.fed_funds:.2f}% | CPI: {macro.cpi_yoy:.1f}% | "
        f"Curve: {macro.yield_curve:+.2f}%\n\n"
        "Provide:\n"
        "1. PERFORMANCE: What worked/didn't and why.\n"
        "2. SIGNALS: Were criteria appropriate?\n"
        "3. TOMORROW: One specific thing to watch.\n"
        "4. REGIME: How did conditions affect the strategy?"
    )
    try:
        resp = ai_client.messages.create(
            model=SONNET_MODEL, max_tokens=800,
            system="You are a trading performance coach. Be direct. No fluff. 2-3 sentences per section.",
            messages=[{"role": "user", "content": msg}]
        )
        review = resp.content[0].text
        log("=" * 60)
        log("DAILY REVIEW")
        log("=" * 60)
        print(review)
        log("=" * 60)
        # Save review
        review_file = STATE_DIR / f"review_{today}.txt"
        review_file.write_text(review)
    except Exception as e:
        log(f"Review error: {e}", "ERROR")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN — runs once per GitHub Actions trigger
# ══════════════════════════════════════════════════════════════════════════════

def main():
    now     = datetime.now(ET)
    session = get_market_session()
    mode    = os.environ.get("RUN_MODE", "scan")  # scan | review | status

    log("=" * 60)
    log(f"TRADING BOT RUN | {now.strftime('%Y-%m-%d %H:%M ET')} | {session} | mode={mode}")
    log(f"Paper mode: {PAPER_MODE}")
    log("=" * 60)

    # Always fetch data first
    fetch_macro()
    fetch_price_data()

    # Post-close review
    if mode == "review" or (session == "closed" and now.hour == 16):
        generate_daily_review()
        return

    # Status check — just print state
    if mode == "status":
        acc = get_account()
        log(f"Account equity: ${acc['equity']:,.2f}")
        positions = get_open_positions()
        log(f"Open positions: {len(positions)}")
        for p in positions:
            log(f"  {p.get('symbol')} {p.get('side')} ×{p.get('qty')} "
                f"@ ${float(p.get('avg_entry_price',0)):.2f} "
                f"P&L: ${float(p.get('unrealized_pl',0)):+.2f}")
        return

    # Main scan mode
    if session not in ("open", "pre_market"):
        log(f"Market {session} — no trading. Exiting.")
        return

    # Get account state
    acc    = get_account()
    equity = acc.get("equity", ACCOUNT_EQUITY)
    log(f"Account equity: ${equity:,.2f} | Buying power: ${acc.get('buying_power',0):,.2f}")

    # Kill switch
    if check_daily_loss(equity):
        log("Kill switch active — no new trades today", "WARN")
        return

    # Position count check
    open_positions = get_open_positions()
    log(f"Open positions: {len(open_positions)}/{RISK['max_open_positions']}")
    if len(open_positions) >= RISK["max_open_positions"]:
        log("Max positions reached — not scanning for new entries")
        return

    # Scan for signals
    signals = scan_all()
    if not signals:
        log("No signals this run — exiting")
        return

    # Score and execute
    slots   = RISK["max_open_positions"] - len(open_positions)
    filled  = 0
    for sig in signals:
        if filled >= slots:
            log("Position slots filled")
            break
        # Skip if already have a position in this symbol
        open_syms = [p.get("symbol") for p in open_positions]
        if sig["symbol"] in open_syms:
            log(f"  {sig['symbol']}: already have open position — skipping")
            continue
        scored = score_signal(sig)
        if scored.get("approved"):
            if execute_signal(scored, equity):
                filled += 1
                open_positions.append({"symbol": sig["symbol"]})  # Update local count

    log(f"\nRun complete: {filled} new position(s) opened")
    log(f"Total open: {len(open_positions) + filled - len([p for p in open_positions])}")


if __name__ == "__main__":
    main()
