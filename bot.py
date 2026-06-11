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
PAPER_MODE        = os.environ.get("PAPER_MODE", "true").lower() == "true"
ACCOUNT_EQUITY    = float(os.environ.get("ACCOUNT_EQUITY", "100000"))
TELEGRAM_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID  = os.environ.get("TELEGRAM_CHAT_ID", "")

ALPACA_HEADERS  = {
    "APCA-API-KEY-ID":     ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
    "Content-Type":        "application/json",
}

ai_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
OPUS_MODEL   = "claude-opus-4-5"
SONNET_MODEL = "claude-sonnet-4-5"

# ── Watchlist ──────────────────────────────────────────────────────────────────
# Diversified across sectors, liquidity, and headline sensitivity
# Not just big names — includes mid-caps, sector plays, and news-driven movers
WATCHLIST = [
    # Index ETFs — market context + tradeable
    "SPY", "QQQ", "IWM",
    # Mega cap tech — high liquidity, options depth
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN",
    # High-beta / news-driven
    "TSLA", "AMD", "PLTR", "COIN",
    # Financials — rate sensitive, macro-driven
    "JPM", "BAC", "GS",
    # Energy — commodity + geopolitical headline driven
    "XOM", "CVX",
    # Healthcare — FDA headlines, biotech volatility
    "UNH", "LLY",
    # Sector ETFs — rotation signals
    "XLK", "XLF", "XLE", "XLV",
    # Volatility / macro proxy
    "TLT", "GLD",
]

OPTIONS_TICKERS = [
    "SPY", "QQQ", "AAPL", "NVDA", "TSLA",
    "MSFT", "META", "AMD", "PLTR", "IWM",
]

# Headline-sensitive tickers — get extra news weight in scoring
HEADLINE_SENSITIVE = [
    "TSLA", "PLTR", "COIN", "AMD", "META",
    "LLY", "COIN", "GS", "XOM",
]

# Sector map — used for rotation analysis
SECTOR_MAP = {
    "XLK": "tech", "XLF": "financials", "XLE": "energy",
    "XLV": "healthcare", "TLT": "bonds", "GLD": "gold",
    "IWM": "small_cap",
}

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
        self.headline_score = 0.0   # -100 to +100
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
        self.reddit_mentions = {}    # symbol -> mention data
        self.sector_rotation = {}    # sector -> rotation data
        self.unusual_volume  = []    # list of unusual volume symbols

tickers: dict[str, TickerData] = {}
macro = MacroData()

NEG_KEYWORDS = [
    "fraud","sec investigation","bankruptcy","recall","downgrade",
    "guidance cut","earnings miss","layoff","lawsuit","restatement","delisting",
    "data breach","accounting","whistleblower","short seller","going concern",
]

POS_KEYWORDS = [
    "beat expectations","record revenue","upgrade","raised guidance","buyback",
    "dividend increase","fda approval","contract win","partnership","acquisition",
    "ai deal","data center","earnings beat","raised forecast","record profit",
]

def score_headlines(headlines: list, symbol: str) -> dict:
    """
    Score a list of headlines for a ticker.
    Returns sentiment score (-100 to +100), flags, and key headlines.
    Headline-sensitive tickers get amplified scoring.
    """
    if not headlines:
        return {"score": 0, "bullish": 0, "bearish": 0,
                "has_negative": False, "has_positive": False, "key": []}

    bullish = 0
    bearish = 0
    key     = []

    for h in headlines:
        h_low = h.lower()
        pos = sum(1 for kw in POS_KEYWORDS if kw in h_low)
        neg = sum(1 for kw in NEG_KEYWORDS if kw in h_low)
        bullish += pos
        bearish += neg
        if pos > 0 or neg > 0:
            key.append(f"{'+ ' if pos > neg else '- '}{h[:80]}")

    # Amplify for headline-sensitive tickers
    amp = 1.5 if symbol in HEADLINE_SENSITIVE else 1.0
    score = round((bullish - bearish) * 20 * amp, 1)
    score = max(-100, min(100, score))

    return {
        "score":        score,
        "bullish":      bullish,
        "bearish":      bearish,
        "has_negative": bearish > 0,
        "has_positive": bullish > 0,
        "key":          key[:3],
    }


def fetch_reddit_mentions(symbols: list) -> dict:
    """
    Pull Reddit mentions from r/wallstreetbets and r/stocks.
    Returns dict of symbol -> {mentions, bullish, bearish, trending}.
    Free, no auth needed.
    """
    mentions = {s: {"mentions": 0, "bullish": 0, "bearish": 0, "trending": False}
                for s in symbols}
    bull_kw = ["calls","bull","long","buy","moon","breakout","squeeze","beat","upgrade"]
    bear_kw = ["puts","bear","short","sell","crash","drop","miss","downgrade","dump"]

    for sub in ["wallstreetbets", "stocks", "options", "investing"]:
        for sort in ["hot", "new"]:
            try:
                r = requests.get(
                    f"https://www.reddit.com/r/{sub}/{sort}.json?limit=25",
                    headers={"User-Agent": "boticus-sentiment/1.0"},
                    timeout=8
                )
                if r.status_code != 200:
                    continue
                posts = r.json().get("data", {}).get("children", [])
                for post in posts:
                    text = (post["data"].get("title","") + " " +
                            post["data"].get("selftext","")[:200]).lower()
                    for sym in symbols:
                        if re.search(r'\b' + sym.lower() + r'\b', text):
                            mentions[sym]["mentions"] += 1
                            mentions[sym]["bullish"] += sum(1 for k in bull_kw if k in text)
                            mentions[sym]["bearish"] += sum(1 for k in bear_kw if k in text)
            except: pass
        time.sleep(0.3)

    # Flag trending — mentioned 3+ times
    for sym in mentions:
        if mentions[sym]["mentions"] >= 3:
            mentions[sym]["trending"] = True
            log(f"  Reddit trending: {sym} ({mentions[sym]['mentions']} mentions, "
                f"bull:{mentions[sym]['bullish']} bear:{mentions[sym]['bearish']})")

    return mentions


def fetch_unusual_volume_scan() -> list:
    """
    Scan watchlist for unusual volume using yfinance.
    Returns list of symbols with >2x average volume.
    """
    unusual = []
    for sym, t in tickers.items():
        if t.vol_ratio >= 2.0:
            unusual.append({"symbol": sym, "vol_ratio": t.vol_ratio, "price": t.price})
            log(f"  Unusual volume: {sym} {t.vol_ratio:.1f}x avg")
    return unusual


def fetch_sector_rotation() -> dict:
    """
    Detect which sectors are in/out of favor today.
    Uses sector ETF performance to guide signal weighting.
    """
    rotation = {}
    for etf, sector in SECTOR_MAP.items():
        t = tickers.get(etf)
        if t and t.change_pct != 0:
            rotation[sector] = {
                "etf":        etf,
                "change_pct": t.change_pct,
                "trend":      "up" if t.price > t.sma_50 else "down",
                "favored":    t.change_pct > 0.3,
            }

    # Log top movers
    if rotation:
        top = sorted(rotation.items(), key=lambda x: x[1]["change_pct"], reverse=True)
        log(f"  Sector rotation — leading: {top[0][0]} ({top[0][1]['change_pct']:+.1f}%) "
            f"lagging: {top[-1][0]} ({top[-1][1]['change_pct']:+.1f}%)")

    return rotation

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
            # News via Alpaca — enhanced with headline scoring
            headlines = []; has_neg = False; headline_score = 0
            try:
                since = (datetime.now(ET) - timedelta(hours=8)).strftime("%Y-%m-%dT%H:%M:%SZ")
                r = requests.get(
                    f"{ALPACA_DATA}/v1beta1/news",
                    headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
                    params={"symbols": symbol, "start": since, "limit": 15},
                    timeout=8
                )
                if r.ok:
                    articles = r.json().get("news", [])
                    headlines = [a["headline"] for a in articles[:8]]
                    hs = score_headlines(headlines, symbol)
                    has_neg      = hs["has_negative"]
                    headline_score = hs["score"]
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
            t.has_negative_news  = has_neg
            t.headline_score     = headline_score
            t.headlines = headlines
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

    # Reddit sentiment (runs after price data is loaded)
    log("  Scanning Reddit sentiment...")
    try:
        macro.reddit_mentions = fetch_reddit_mentions(WATCHLIST)
    except Exception as e:
        log(f"  Reddit error: {e}", "WARN")

    # Sector rotation
    macro.sector_rotation = fetch_sector_rotation()

    # Unusual volume
    macro.unusual_volume = fetch_unusual_volume_scan()

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

    # Sector rotation summary
    if m.sector_rotation:
        leading = [(s, d) for s, d in m.sector_rotation.items() if d["favored"]]
        lagging = [(s, d) for s, d in m.sector_rotation.items() if not d["favored"]]
        if leading:
            lines.append(f"Sectors leading: {', '.join(s for s,_ in leading[:3])}")
        if lagging:
            lines.append(f"Sectors lagging: {', '.join(s for s,_ in lagging[:3])}")

    # Unusual volume flags
    if m.unusual_volume:
        lines.append(f"Unusual volume: {', '.join(u['symbol'] for u in m.unusual_volume[:5])}")

    if symbol and symbol in tickers:
        t = tickers[symbol]
        lines += [
            f"\n=== {symbol} ===",
            f"Price: ${t.price:.2f} ({t.change_pct:+.2f}%)",
            f"RSI: {t.rsi_14:.1f} | ATR: {t.atr_pct:.2%} | Vol ratio: {t.vol_ratio:.2f}x",
            f"SMA50: ${t.sma_50:.2f} | SMA200: ${t.sma_200:.2f}",
            f"Trend: {'UPTREND' if t.price > t.sma_50 > t.sma_200 else 'DOWNTREND' if t.price < t.sma_50 < t.sma_200 else 'MIXED'}",
            f"Headline score: {t.headline_score:+.0f}/100 ({'bullish' if t.headline_score > 20 else 'bearish' if t.headline_score < -20 else 'neutral'})",
            f"Earnings within 5d: {t.earnings_within_5d}",
        ]
        # Reddit mentions
        reddit = m.reddit_mentions.get(symbol, {})
        if reddit.get("mentions", 0) > 0:
            bias = "bullish" if reddit["bullish"] > reddit["bearish"] else "bearish" if reddit["bearish"] > reddit["bullish"] else "neutral"
            lines.append(f"Reddit: {reddit['mentions']} mentions ({bias}) {'🔥 TRENDING' if reddit.get('trending') else ''}")

        # Sector context
        sym_sector = SECTOR_MAP.get(symbol)
        if sym_sector and sym_sector in m.sector_rotation:
            sr = m.sector_rotation[sym_sector]
            lines.append(f"Sector ({sym_sector}): {sr['change_pct']:+.1f}% today ({'favored' if sr['favored'] else 'out of favor'})")

        if t.headlines:
            lines.append("Headlines:")
            for h in t.headlines[:4]: lines.append(f"  • {h}")

    lines.append("\n=== WATCHLIST ===")
    for sym, tick in tickers.items():
        if sym == symbol: continue
        hl = f" HL:{tick.headline_score:+.0f}" if tick.headline_score != 0 else ""
        lines.append(f"{sym}: ${tick.price:.2f} ({tick.change_pct:+.1f}%) RSI:{tick.rsi_14:.0f}{hl}")
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
    if t.has_negative_news and t.headline_score < -30: return None
    if m.fomc_24h or m.cpi_24h or m.jobs_24h: return None
    if macro.risk_score <= -2: return None
    # Trend — tightened, require >0.5% above SMA50
    if not (t.price > t.sma_50 > t.sma_200): return None
    pct_above_50 = (t.price - t.sma_50) / t.sma_50
    if pct_above_50 < 0.005: return None
    trend_score = min(100, 90 + pct_above_50 * 200)
    # RSI — tightened upper bound to 65
    if not (RISK["rsi_min"] <= t.rsi_14 <= RISK["rsi_max"]): return None
    if t.rsi_14 > 65: return None
    mom_score = max(50, 100 - abs(t.rsi_14 - 52) * 2.5)
    # Volume — heavier penalty
    vol_score = min(100, 55 + (t.vol_ratio - 1) * 25)
    if t.vol_ratio < RISK["volume_min_mult"]: vol_score *= 0.4
    if t.vol_ratio < 0.8: return None
    # ATR
    if t.atr_pct > RISK["atr_pct_max"]: return None
    if t.atr_pct < 0.005: return None
    atr_score = 100 if 0.01 <= t.atr_pct <= 0.025 else 75
    # Macro — tightened
    mac_score = (100 if m.market_regime == "trending_up" else
                 75  if m.market_regime == "ranging" else
                 40  if m.market_regime == "volatile" else 15)
    if m.vix_regime == "fear":      mac_score -= 35
    elif m.vix_regime == "elevated": mac_score -= 20
    if m.yield_curve < -0.5:         mac_score -= 10
    if mac_score < 30: return None
    # Headline + sentiment adjustments
    hl_score = max(0, min(100, 50 + t.headline_score / 2))
    reddit = m.reddit_mentions.get(symbol, {})
    reddit_adj = 8 if (reddit.get("trending") and reddit.get("bullish",0) > reddit.get("bearish",0)) else (
                -15 if (reddit.get("trending") and reddit.get("bearish",0) > reddit.get("bullish",0)) else 0)
    sym_sector = SECTOR_MAP.get(symbol)
    sector_adj = (5 if sym_sector and m.sector_rotation.get(sym_sector, {}).get("favored") else
                 -5 if sym_sector and sym_sector in m.sector_rotation else 0)
    is_unusual  = any(u["symbol"] == symbol for u in m.unusual_volume)
    unusual_adj = 5 if is_unusual else 0
    criteria = (trend_score*0.25 + mom_score*0.20 + vol_score*0.20 +
                atr_score*0.15 + mac_score*0.15 + hl_score*0.05 +
                reddit_adj + sector_adj + unusual_adj)
    if criteria < 60: return None
    atr    = t.atr_14 or t.price * 0.015
    stop   = round(t.price - RISK["stop_loss_atr_mult"]   * atr, 2)
    target = round(t.price + RISK["take_profit_atr_mult"] * atr, 2)
    rr     = round((target - t.price) / (t.price - stop), 2) if t.price > stop else 0
    if rr < 1.5: return None
    notes = [f"Trend↑ {pct_above_50:.1%} above SMA50",
             f"RSI={t.rsi_14:.1f} Vol={t.vol_ratio:.1f}x ATR={t.atr_pct:.2%}"]
    if t.headline_score > 20: notes.append(f"Headlines bullish ({t.headline_score:+.0f})")
    reddit_mentions = reddit.get("mentions", 0)
    if reddit.get("trending"): notes.append(f"Reddit trending ({reddit_mentions} mentions)")
    if is_unusual: notes.append(f"Unusual volume {t.vol_ratio:.1f}x")
    return {
        "symbol": symbol, "type": "stock_long", "direction": "long",
        "entry": t.price, "stop": stop, "target": target, "rr": rr,
        "criteria": round(criteria, 1),
        "scores": {"trend": trend_score, "momentum": mom_score,
                   "volume": vol_score, "atr": atr_score, "macro": mac_score},
        "notes": notes,
        "headline_score": t.headline_score,
        "reddit_trending": reddit.get("trending", False),
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
    "You have pattern memory, market context, headline scores, and Reddit sentiment. USE ALL OF IT.\n"
    "Output ONLY valid JSON — no markdown, no preamble.\n\n"
    "Format: {\"score\":0-100,\"confidence\":\"low|medium|high\","
    "\"recommendation\":\"take|skip|reduce_size\","
    "\"risk_flag\":null or \"caution\" or \"abort\","
    "\"reasoning\":\"2-3 sentences\","
    "\"key_positives\":[\"x\"],\"key_risks\":[\"x\"],"
    "\"suggested_size_adjustment\":1.0,"
    "\"pattern_insight\":\"one sentence\"}\n\n"
    "80-100=take, 65-79=good, 60-64=marginal, 0-59=skip.\n"
    "LONG: uptrend >0.5% above SMA50 + RSI 40-65 + volume + low VIX + RISK-ON futures + positive/neutral headlines.\n"
    "SHORT: downtrend OR overbought RSI>72 + risk-off + elevated VIX + negative headlines supporting move.\n"
    "Headline score >+30 = bullish boost. Score <-30 = bearish signal or long abort.\n"
    "Reddit trending bullish = retail momentum building. Reddit trending bearish = contrarian or confirm short.\n"
    "Sector rotation matters — don't fight the tape. If sector is out of favor, be stricter.\n"
    "Minimum 1.5:1 R/R required. Protect capital first. Never rationalize a bad trade."
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
    mode    = os.environ.get("RUN_MODE", "scan")

    log("=" * 60)
    log(f"TRADING BOT RUN | {now.strftime('%Y-%m-%d %H:%M ET')} | {session} | mode={mode}")
    log(f"Paper mode: {PAPER_MODE} | Telegram: {'yes' if TELEGRAM_TOKEN else 'no'}")
    log("=" * 60)

    # Always fetch data first
    fetch_macro()
    fetch_price_data()

    # ── Dashboard generation ───────────────────────────────────────────────
    if mode == "dashboard":
        generate_dashboard()
        return

    # ── Backtest mode ──────────────────────────────────────────────────────
    if mode == "backtest":
        days = int(os.environ.get("BACKTEST_DAYS", "180"))
        bt   = run_backtest(lookback_days=days)
        if bt:
            run_auto_adjust(backtest=bt)
        return

    # ── Auto-adjust only ───────────────────────────────────────────────────
    if mode == "auto_adjust":
        bt_file = STATE_DIR / "backtest_latest.json"
        bt = json.loads(bt_file.read_text()) if bt_file.exists() else {}
        run_auto_adjust(backtest=bt)
        return

    # ── Post-close review ──────────────────────────────────────────────────
    if mode == "review" or (session == "closed" and now.hour == 16):
        generate_daily_review()
        # Run auto-adjust weekly on Fridays
        if now.weekday() == 4:
            log("Friday — running weekly auto-adjust...")
            bt_file = STATE_DIR / "backtest_latest.json"
            bt = json.loads(bt_file.read_text()) if bt_file.exists() else {}
            result = run_auto_adjust(backtest=bt)
            if result:
                # Weekly summary
                feedback = load_feedback()
                week_trades = [t for t in feedback
                               if t.get("date","") >= (date.today() - timedelta(days=7)).isoformat()]
                wins = [t for t in week_trades if t["result"] == "win"]
                pnl  = sum(t.get("pnl_dollar", 0) for t in week_trades)
                try:
                    resp = ai_client.messages.create(
                        model=SONNET_MODEL, max_tokens=600,
                        system="You are a trading performance coach. Weekly review. Be direct, 3-4 sentences per section.",
                        messages=[{"role": "user", "content":
                            f"Week ending {date.today().isoformat()}\n"
                            f"Trades: {len(week_trades)} | Wins: {len(wins)} | P&L: ${pnl:+.2f}\n"
                            f"Regime: {macro.market_regime} | VIX avg: {macro.vix:.1f}\n"
                            f"Auto-adjust confidence: {result.get('confidence','')}\n"
                            f"Priority change: {result.get('priority_change','')}\n\n"
                            "Sections: WEEK RECAP | WHAT WORKED | WHAT DIDN'T | NEXT WEEK"
                        }]
                    )
                    weekly_review = resp.content[0].text
                    stats = {"total": len(week_trades), "wins": len(wins),
                             "losses": len(week_trades)-len(wins), "pnl": pnl}
                    alert_weekly_summary(weekly_review, stats)
                except Exception as e:
                    log(f"Weekly review error: {e}", "ERROR")
        return

    # ── Status check ───────────────────────────────────────────────────────
    if mode == "status":
        acc = get_account()
        log(f"Account equity: ${acc['equity']:,.2f}")
        positions = get_open_positions()
        log(f"Open positions: {len(positions)}")
        for p in positions:
            log(f"  {p.get('symbol')} {p.get('side')} ×{p.get('qty')} "
                f"@ ${float(p.get('avg_entry_price',0)):.2f} "
                f"P&L: ${float(p.get('unrealized_pl',0)):+.2f}")
        _tg(
            f"📍 *Status Check*\n"
            f"Equity: ${acc['equity']:,.2f}\n"
            f"Open positions: {len(positions)}\n"
            + "\n".join([
                f"  {p.get('symbol')} {p.get('side')} ×{p.get('qty')} "
                f"P&L: ${float(p.get('unrealized_pl',0)):+.2f}"
                for p in positions
            ])
        )
        return

    # ── Main scan mode ─────────────────────────────────────────────────────
    if session not in ("open", "pre_market"):
        log(f"Market {session} — no trading. Exiting.")
        return

    # Startup ping on first run of the day (9:30 ET)
    if now.hour == 9 and now.minute < 40:
        send_startup_ping()

    acc    = get_account()
    equity = acc.get("equity", ACCOUNT_EQUITY)
    log(f"Account equity: ${equity:,.2f} | Buying power: ${acc.get('buying_power',0):,.2f}")

    if check_daily_loss(equity):
        log("Kill switch active — no new trades today", "WARN")
        alert_kill_switch(
            sum(t.get("pnl",0) for t in load_trades()
                if t.get("closed_at","")[:10] == date.today().isoformat()) / equity,
            equity
        )
        return

    open_positions = get_open_positions()
    log(f"Open positions: {len(open_positions)}/{RISK['max_open_positions']}")
    if len(open_positions) >= RISK["max_open_positions"]:
        log("Max positions reached — not scanning for new entries")
        return

    signals = scan_all()
    if not signals:
        log("No signals this run — exiting")
        return

    slots  = RISK["max_open_positions"] - len(open_positions)
    filled = 0
    for sig in signals:
        if filled >= slots:
            log("Position slots filled")
            break
        open_syms = [p.get("symbol") for p in open_positions]
        if sig["symbol"] in open_syms:
            log(f"  {sig['symbol']}: already have open position — skipping")
            continue
        scored = score_signal(sig)
        alert_signal(scored)
        if scored.get("approved"):
            rps    = abs(scored["entry"] - scored["stop"])
            shares = max(1, int(equity * RISK["max_risk_per_trade_pct"] / rps)) if rps else 1
            shares = min(shares, int(equity * RISK["max_position_pct"] / scored["entry"]))
            shares = max(1, int(shares * scored.get("size_adj", 1.0)))
            risk_amt = shares * rps
            if execute_signal(scored, equity):
                alert_trade_open(scored, shares, risk_amt)
                filled += 1
                open_positions.append({"symbol": sig["symbol"]})

    log(f"\nRun complete: {filled} new position(s) opened")


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM ALERTS
# ══════════════════════════════════════════════════════════════════════════════

def _tg(text: str):
    """Send a message to Telegram. Logs response for debugging."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram: skipped — token or chat_id missing", "WARN")
        return
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "Markdown"},
            timeout=8
        )
        log(f"Telegram response: {resp.status_code} — {resp.text[:150]}")
    except Exception as e:
        log(f"Telegram error: {e}", "WARN")

def alert_signal(sig: dict):
    d   = "📈 LONG" if sig["direction"] == "long" else "📉 SHORT"
    ai  = f"\nAI: {sig.get('ai_score',0):.0f}/100 — {sig.get('ai_reasoning','')[:120]}" if sig.get("ai_score") else ""
    hl  = sig.get("headline_score", 0)
    hl_str = f"\nHeadlines: {hl:+.0f} ({'🟢 bullish' if hl > 20 else '🔴 bearish' if hl < -20 else '⚪ neutral'})" if hl != 0 else ""
    reddit_str = "\n🔥 Reddit trending" if sig.get("reddit_trending") else ""
    _tg(
        f"*{d} SIGNAL — {sig['symbol']}*\n"
        f"Entry: ${sig['entry']:.2f}  Stop: ${sig['stop']:.2f}  Target: ${sig['target']:.2f}\n"
        f"R/R: {sig['rr']:.2f}  Criteria: {sig['criteria']:.0f}/100"
        f"{hl_str}{reddit_str}{ai}\n"
        f"VIX: {macro.vix:.1f} ({macro.vix_regime})  Regime: {macro.market_regime}"
    )

def alert_trade_open(sig: dict, shares: int, risk_amt: float):
    d = "📈" if sig["direction"] == "long" else "📉"
    mode = "PAPER" if PAPER_MODE else "LIVE"
    _tg(
        f"{d} *TRADE OPENED [{mode}]* — {sig['symbol']}\n"
        f"{'Bought' if sig['direction']=='long' else 'Shorted'} {shares} shares @ ${sig['entry']:.2f}\n"
        f"Stop: ${sig['stop']:.2f}  |  Target: ${sig['target']:.2f}\n"
        f"Risk: ${risk_amt:.0f}  |  AI score: {sig.get('ai_score',0):.0f}/100\n"
        f"_{sig.get('ai_reasoning','')[:100]}_"
    )

def alert_trade_close(symbol: str, direction: str, entry: float,
                       close: float, shares: int, reason: str):
    pnl     = (close - entry) * shares if direction == "long" else (entry - close) * shares
    pnl_pct = (close - entry) / entry * 100 if direction == "long" else (entry - close) / entry * 100
    e       = "✅" if pnl > 0 else "🔴"
    _tg(
        f"{e} *TRADE CLOSED — {symbol}* ({reason})\n"
        f"Entry: ${entry:.2f}  →  Exit: ${close:.2f}\n"
        f"P&L: {pnl_pct:+.1f}%  (${pnl:+.2f})\n"
        f"Shares: {shares}"
    )

def alert_kill_switch(loss_pct: float, equity: float):
    _tg(
        f"🚨 *KILL SWITCH TRIGGERED*\n"
        f"Daily loss: {loss_pct:.2%} of account\n"
        f"Equity: ${equity:,.2f}\n"
        f"All trading halted until tomorrow."
    )

def alert_daily_summary(review: str, stats: dict):
    wins = stats.get("wins", 0)
    total = stats.get("total", 0)
    pnl = stats.get("pnl", 0.0)
    wr = wins / total * 100 if total else 0
    _tg(
        f"📊 *Daily Summary — {date.today().isoformat()}*\n"
        f"Trades: {total}  |  W:{wins} L:{total-wins}  |  WR: {wr:.0f}%\n"
        f"P&L: ${pnl:+.2f}\n\n"
        f"{review[:600]}"
    )

def alert_weekly_summary(review: str, stats: dict):
    _tg(
        f"📅 *Weekly Review — Week of {date.today().isoformat()}*\n"
        f"Trades: {stats.get('total',0)}  |  "
        f"W:{stats.get('wins',0)} L:{stats.get('losses',0)}  |  "
        f"P&L: ${stats.get('pnl',0):+.2f}\n\n"
        f"{review[:800]}"
    )

def send_startup_ping():
    mode = "PAPER" if PAPER_MODE else "🚨 LIVE"
    _tg(
        f"🤖 *Boticus started [{mode}]*\n"
        f"Session: {get_market_session()}  |  "
        f"VIX: {macro.vix:.1f}  |  Regime: {macro.market_regime}\n"
        f"Watchlist: {', '.join(WATCHLIST)}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# BACKTESTING MODULE
# Runs criteria against historical data to validate signal quality
# ══════════════════════════════════════════════════════════════════════════════

def run_backtest(symbols: list = None, lookback_days: int = 180,
                 notify: bool = True) -> dict:
    """
    Backtest the long + short signal criteria against historical price data.
    For each day in the lookback window, simulates what signals would have fired
    and tracks hypothetical outcomes.

    Returns a dict with win rates, avg R/R, best/worst setups, and regime breakdown.
    """
    symbols = symbols or WATCHLIST
    log(f"Running backtest: {len(symbols)} symbols, {lookback_days} days lookback")

    results   = []
    all_trades = []

    for symbol in symbols:
        try:
            ticker = yf.Ticker(symbol)
            hist   = ticker.history(period=f"{lookback_days + 210}d")
            if hist.empty or len(hist) < 220:
                log(f"  {symbol}: insufficient history", "WARN")
                continue

            closes  = hist["Close"].values
            highs   = hist["High"].values
            lows    = hist["Low"].values
            volumes = hist["Volume"].values
            dates   = hist.index

            log(f"  Backtesting {symbol} ({len(hist)} days)...")
            sym_trades = []

            # Walk forward from day 210 onwards (need 200 bars for SMA200)
            for i in range(210, len(closes) - 5):
                window_c = closes[:i+1]
                window_h = highs[:i+1]
                window_l = lows[:i+1]
                window_v = volumes[:i+1]

                price     = float(closes[i])
                sma_50    = float(np.mean(window_c[-50:]))
                sma_200   = float(np.mean(window_c[-200:]))
                rsi       = calc_rsi(window_c)
                atr       = calc_atr(window_h, window_l, window_c)
                atr_pct   = atr / price if price else 0
                vol_ratio = float(window_v[-1]) / float(np.mean(window_v[-20:])) if np.mean(window_v[-20:]) > 0 else 0

                # ── Long signal check ──────────────────────────────────────
                long_signal = (
                    price > sma_50 > sma_200 and
                    RISK["rsi_min"] <= rsi <= RISK["rsi_max"] and
                    atr_pct <= RISK["atr_pct_max"]
                )
                # ── Short signal check ─────────────────────────────────────
                short_signal = (
                    (price < sma_50 < sma_200 and rsi < 55) or
                    (rsi > 72 and price > sma_50)
                ) and atr_pct <= RISK["atr_pct_max"]

                for direction, fired in [("long", long_signal), ("short", short_signal)]:
                    if not fired:
                        continue

                    entry  = price
                    stop   = round(entry - RISK["stop_loss_atr_mult"]   * atr, 4) if direction == "long" else round(entry + RISK["stop_loss_atr_mult"]   * atr, 4)
                    target = round(entry + RISK["take_profit_atr_mult"] * atr, 4) if direction == "long" else round(entry - RISK["take_profit_atr_mult"] * atr, 4)

                    # Simulate outcome: walk forward up to 10 days
                    outcome = "timeout"
                    exit_price = float(closes[min(i+10, len(closes)-1)])
                    exit_day   = 10

                    for j in range(i+1, min(i+11, len(closes))):
                        c = float(closes[j])
                        if direction == "long":
                            if c <= stop:   outcome = "stop";   exit_price = stop;   exit_day = j-i; break
                            if c >= target: outcome = "target"; exit_price = target; exit_day = j-i; break
                        else:
                            if c >= stop:   outcome = "stop";   exit_price = stop;   exit_day = j-i; break
                            if c <= target: outcome = "target"; exit_price = target; exit_day = j-i; break

                    pnl_pct = ((exit_price - entry) / entry * 100) if direction == "long" else ((entry - exit_price) / entry * 100)

                    # Simple regime at signal date
                    regime = ("trending_up"   if price > sma_50 > sma_200 else
                              "trending_down" if price < sma_50 < sma_200 else "ranging")

                    sym_trades.append({
                        "symbol":    symbol,
                        "date":      str(dates[i].date()),
                        "direction": direction,
                        "entry":     round(entry, 2),
                        "stop":      round(stop, 2),
                        "target":    round(target, 2),
                        "exit":      round(exit_price, 2),
                        "exit_day":  exit_day,
                        "outcome":   outcome,
                        "pnl_pct":   round(pnl_pct, 2),
                        "rsi":       round(rsi, 1),
                        "atr_pct":   round(atr_pct, 4),
                        "vol_ratio": round(vol_ratio, 2),
                        "regime":    regime,
                    })

            all_trades.extend(sym_trades)
            wins   = [t for t in sym_trades if t["outcome"] == "target"]
            losses = [t for t in sym_trades if t["outcome"] == "stop"]
            log(f"    {symbol}: {len(sym_trades)} signals | "
                f"W:{len(wins)} L:{len(losses)} | "
                f"WR:{len(wins)/len(sym_trades)*100:.0f}%" if sym_trades else f"    {symbol}: 0 signals")

        except Exception as e:
            log(f"  Backtest error {symbol}: {e}", "ERROR")

    if not all_trades:
        log("Backtest: no trades generated", "WARN")
        return {}

    # ── Aggregate results ──────────────────────────────────────────────────
    wins    = [t for t in all_trades if t["outcome"] == "target"]
    losses  = [t for t in all_trades if t["outcome"] == "stop"]
    timeout = [t for t in all_trades if t["outcome"] == "timeout"]
    total   = len(all_trades)
    wr      = len(wins) / total * 100
    avg_win  = sum(t["pnl_pct"] for t in wins)   / len(wins)   if wins   else 0
    avg_loss = sum(t["pnl_pct"] for t in losses)  / len(losses) if losses else 0
    expectancy = (wr/100 * avg_win) + ((1-wr/100) * avg_loss)

    # Regime breakdown
    regime_stats = {}
    for t in all_trades:
        r = t["regime"]
        if r not in regime_stats: regime_stats[r] = {"wins": 0, "losses": 0, "total": 0}
        regime_stats[r]["total"] += 1
        if t["outcome"] == "target": regime_stats[r]["wins"] += 1
        if t["outcome"] == "stop":   regime_stats[r]["losses"] += 1

    # RSI sweet spot analysis
    rsi_buckets = {"40-50": [], "50-60": [], "60-72": []}
    for t in all_trades:
        rsi = t["rsi"]
        if   40 <= rsi < 50: rsi_buckets["40-50"].append(t)
        elif 50 <= rsi < 60: rsi_buckets["50-60"].append(t)
        elif 60 <= rsi < 72: rsi_buckets["60-72"].append(t)

    rsi_wr = {}
    for bucket, trades in rsi_buckets.items():
        if trades:
            w = sum(1 for t in trades if t["outcome"] == "target")
            rsi_wr[bucket] = round(w / len(trades) * 100, 1)

    # Volume sweet spot
    vol_buckets = {"<1.1x": [], "1.1-2x": [], ">2x": []}
    for t in all_trades:
        v = t["vol_ratio"]
        if   v < 1.1: vol_buckets["<1.1x"].append(t)
        elif v < 2.0: vol_buckets["1.1-2x"].append(t)
        else:         vol_buckets[">2x"].append(t)

    vol_wr = {}
    for bucket, trades in vol_buckets.items():
        if trades:
            w = sum(1 for t in trades if t["outcome"] == "target")
            vol_wr[bucket] = round(w / len(trades) * 100, 1)

    # Direction split
    long_trades  = [t for t in all_trades if t["direction"] == "long"]
    short_trades = [t for t in all_trades if t["direction"] == "short"]
    long_wr  = sum(1 for t in long_trades  if t["outcome"]=="target") / len(long_trades)  * 100 if long_trades  else 0
    short_wr = sum(1 for t in short_trades if t["outcome"]=="target") / len(short_trades) * 100 if short_trades else 0

    summary = {
        "period_days":    lookback_days,
        "symbols":        symbols,
        "total_signals":  total,
        "wins":           len(wins),
        "losses":         len(losses),
        "timeouts":       len(timeout),
        "win_rate":       round(wr, 1),
        "avg_win_pct":    round(avg_win, 2),
        "avg_loss_pct":   round(avg_loss, 2),
        "expectancy_pct": round(expectancy, 2),
        "long_signals":   len(long_trades),
        "long_win_rate":  round(long_wr, 1),
        "short_signals":  len(short_trades),
        "short_win_rate": round(short_wr, 1),
        "regime_stats":   regime_stats,
        "rsi_win_rates":  rsi_wr,
        "volume_win_rates": vol_wr,
        "raw_trades":     all_trades,
    }

    # ── Print results ──────────────────────────────────────────────────────
    log("=" * 60)
    log(f"BACKTEST RESULTS — {lookback_days} days | {total} signals")
    log("=" * 60)
    log(f"Win rate:    {wr:.1f}%  ({len(wins)}W / {len(losses)}L / {len(timeout)} timeout)")
    log(f"Avg win:     {avg_win:+.2f}%   Avg loss:  {avg_loss:+.2f}%")
    log(f"Expectancy:  {expectancy:+.2f}% per trade")
    log(f"Long:  {len(long_trades)} signals | {long_wr:.1f}% WR")
    log(f"Short: {len(short_trades)} signals | {short_wr:.1f}% WR")
    log("\nBy regime:")
    for r, s in regime_stats.items():
        rwr = s["wins"]/s["total"]*100 if s["total"] else 0
        log(f"  {r:15} {rwr:.1f}% WR ({s['total']} trades)")
    log("\nRSI buckets:")
    for bucket, wr_val in rsi_wr.items():
        log(f"  RSI {bucket}: {wr_val:.1f}% WR")
    log("\nVolume buckets:")
    for bucket, wr_val in vol_wr.items():
        log(f"  Vol {bucket}: {wr_val:.1f}% WR")
    log("=" * 60)

    # Save backtest results
    bt_file = STATE_DIR / "backtest_latest.json"
    bt_file.write_text(json.dumps(
        {k: v for k, v in summary.items() if k != "raw_trades"},
        indent=2, default=str
    ))
    log(f"Backtest saved to {bt_file}")

    # Telegram notification
    if notify and TELEGRAM_TOKEN:
        _tg(
            f"📊 *Backtest Complete — {lookback_days} days*\n"
            f"Signals: {total}  |  Win rate: {wr:.1f}%\n"
            f"Avg win: {avg_win:+.2f}%  |  Avg loss: {avg_loss:+.2f}%\n"
            f"Expectancy: {expectancy:+.2f}% per trade\n"
            f"Long WR: {long_wr:.1f}%  |  Short WR: {short_wr:.1f}%\n"
            f"Best regime: {max(regime_stats, key=lambda r: regime_stats[r]['wins']/regime_stats[r]['total'] if regime_stats[r]['total'] else 0)}"
        )

    return summary


# ══════════════════════════════════════════════════════════════════════════════
# AUTO-ADJUST — AI reviews backtest + live results and suggests criteria changes
# ══════════════════════════════════════════════════════════════════════════════

def run_auto_adjust(backtest: dict = None, notify: bool = True) -> dict:
    """
    Uses Sonnet to review backtest results + live trade feedback,
    then suggests specific criteria adjustments.
    Returns a dict of suggested changes — does NOT apply them automatically.
    Shows you what to change and why, you approve.
    """
    feedback = load_feedback()
    bt       = backtest or {}

    log("Running auto-adjust analysis...")

    # Build context for AI
    live_summary = ""
    if feedback:
        wins   = [t for t in feedback if t["result"] == "win"]
        losses = [t for t in feedback if t["result"] == "loss"]
        total  = len(feedback)
        wr     = len(wins) / total * 100 if total else 0

        # Find patterns in wins vs losses
        win_rsi  = sum(t.get("criteria_score", 0) for t in wins)   / len(wins)   if wins   else 0
        loss_rsi = sum(t.get("criteria_score", 0) for t in losses)  / len(losses) if losses else 0

        regime_perf = {}
        for t in feedback:
            r = t.get("regime", "unknown")
            if r not in regime_perf: regime_perf[r] = {"w": 0, "l": 0}
            regime_perf[r]["w" if t["result"]=="win" else "l"] += 1

        regime_str = json.dumps({
            r: f"{s['w']/(s['w']+s['l'])*100:.0f}%"
            for r, s in regime_perf.items() if s['w']+s['l'] > 0
        })
        live_summary = (
            f"LIVE TRADE HISTORY ({total} trades):\n"
            f"Win rate: {wr:.1f}% | Wins: {len(wins)} | Losses: {len(losses)}\n"
            f"Win avg criteria score: {win_rsi:.1f} vs loss: {loss_rsi:.1f}\n"
            f"By regime: {regime_str}\n"
            f"Recent 5: {' '.join(['W' if t['result']=='win' else 'L' for t in feedback[-5:]])}"
        )

    if bt:
        regime_bt_str = json.dumps({
            r: f"{s['wins']/s['total']*100:.0f}%"
            for r, s in bt.get("regime_stats", {}).items() if s.get("total", 0) > 0
        })
        bt_summary = (
            f"BACKTEST RESULTS ({bt.get('period_days',0)} days, {bt.get('total_signals',0)} signals):\n"
            f"Win rate: {bt.get('win_rate',0):.1f}%\n"
            f"Avg win: {bt.get('avg_win_pct',0):+.2f}% | Avg loss: {bt.get('avg_loss_pct',0):+.2f}%\n"
            f"Expectancy: {bt.get('expectancy_pct',0):+.2f}% per trade\n"
            f"Long WR: {bt.get('long_win_rate',0):.1f}% | Short WR: {bt.get('short_win_rate',0):.1f}%\n"
            f"RSI performance: {json.dumps(bt.get('rsi_win_rates',{}))}\n"
            f"Volume performance: {json.dumps(bt.get('volume_win_rates',{}))}\n"
            f"By regime: {regime_bt_str}"
        )

    if not live_summary and not bt_summary:
        log("Auto-adjust: insufficient data — need backtest or live trades first", "WARN")
        return {}

    current_config = (
        f"CURRENT CRITERIA:\n"
        f"RSI range: {RISK['rsi_min']}-{RISK['rsi_max']}\n"
        f"Volume min: {RISK['volume_min_mult']}x\n"
        f"ATR max: {RISK['atr_pct_max']:.1%}\n"
        f"Stop ATR mult: {RISK['stop_loss_atr_mult']}\n"
        f"Target ATR mult: {RISK['take_profit_atr_mult']}\n"
        f"Max risk per trade: {RISK['max_risk_per_trade_pct']:.1%}\n"
        f"Max daily loss: {RISK['max_daily_loss_pct']:.1%}"
    )

    prompt = (
        f"{live_summary}\n\n"
        f"{bt_summary}\n\n"
        f"{current_config}\n\n"
        "Based on this data, suggest specific adjustments to improve performance.\n"
        "Output ONLY valid JSON — no markdown, no preamble.\n\n"
        "Format:\n"
        '{"adjustments": ['
        '{"param": "rsi_min", "current": 40, "suggested": 45, "reason": "RSI 40-45 showing 38% WR vs 58% for 45-65"},'
        '...'
        '], '
        '"summary": "2-3 sentence overall assessment", '
        '"confidence": "low|medium|high", '
        '"priority_change": "the single most impactful change to make first"}'
    )

    try:
        resp = ai_client.messages.create(
            model=SONNET_MODEL, max_tokens=800,
            system=(
                "You are a quantitative trading strategy optimizer. "
                "Analyze performance data and suggest specific, data-driven parameter adjustments. "
                "Only suggest changes where the data clearly supports it. "
                "Be conservative — don't over-optimize. Output only valid JSON."
            ),
            messages=[{"role": "user", "content": prompt}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        result = json.loads(raw.strip())

        adjustments = result.get("adjustments", [])
        summary     = result.get("summary", "")
        priority    = result.get("priority_change", "")
        confidence  = result.get("confidence", "medium")

        log("=" * 60)
        log(f"AUTO-ADJUST RECOMMENDATIONS (confidence: {confidence})")
        log("=" * 60)
        log(f"Summary: {summary}")
        log(f"Priority change: {priority}")
        log("\nSuggested adjustments:")
        for adj in adjustments:
            log(f"  {adj['param']:30} {adj['current']} → {adj['suggested']}")
            log(f"    Reason: {adj['reason']}")
        log("=" * 60)
        log("NOTE: These are suggestions only. Edit RISK dict in bot.py to apply.")

        # Save recommendations
        adj_file = STATE_DIR / "auto_adjust_latest.json"
        adj_file.write_text(json.dumps(result, indent=2))

        # Telegram alert with recommendations
        if notify and TELEGRAM_TOKEN and adjustments:
            adj_lines = "\n".join([
                f"• `{a['param']}`: {a['current']} → *{a['suggested']}*"
                for a in adjustments[:5]
            ])
            _tg(
                f"🔧 *Auto-Adjust Recommendations* (confidence: {confidence})\n\n"
                f"{adj_lines}\n\n"
                f"Priority: {priority}\n\n"
                f"_{summary}_\n\n"
                f"Edit RISK dict in bot.py to apply."
            )

        return result

    except json.JSONDecodeError as e:
        log(f"Auto-adjust parse error: {e}", "ERROR")
    except Exception as e:
        log(f"Auto-adjust error: {e}", "ERROR")
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# STREAMLIT DASHBOARD — generates dashboard.py for local or Streamlit Cloud use
# ══════════════════════════════════════════════════════════════════════════════

DASHBOARD_CODE = '''
import streamlit as st
import json, os
from pathlib import Path
from datetime import datetime, date

st.set_page_config(page_title="Boticus", page_icon="🤖", layout="wide")
st.title("🤖 Boticus — Trading Bot Dashboard")

STATE_DIR = Path(os.environ.get("STATE_DIR", "bot_state"))

def load(filename):
    p = STATE_DIR / filename
    if p.exists():
        try: return json.loads(p.read_text())
        except: pass
    return None

# ── Header metrics ────────────────────────────────────────────────────────────
trades   = load("trades.json") or []
feedback = load("feedback.json") or []
bt       = load("backtest_latest.json")
adj      = load("auto_adjust_latest.json")

open_t   = [t for t in trades if t.get("status") == "open"]
closed_t = [t for t in trades if t.get("status") == "closed"]
today_t  = [t for t in trades if t.get("opened_at","")[:10] == date.today().isoformat()]
wins     = [t for t in feedback if t.get("result") == "win"]
total_pnl = sum(t.get("pnl_dollar", 0) for t in feedback)
wr       = len(wins) / len(feedback) * 100 if feedback else 0

col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Open Positions", len(open_t))
col2.metric("Total Trades", len(feedback))
col3.metric("Win Rate", f"{wr:.1f}%")
col4.metric("Total P&L", f"${total_pnl:+,.2f}")
col5.metric("Today's Trades", len(today_t))

st.divider()

# ── Open positions ────────────────────────────────────────────────────────────
st.subheader("Open Positions")
if open_t:
    for t in open_t:
        with st.container():
            c1, c2, c3, c4, c5 = st.columns([2,1,1,1,2])
            c1.write(f"**{t['symbol']}** {t['direction'].upper()}")
            c2.write(f"Entry: ${t.get('entry_price',0):.2f}")
            c3.write(f"Stop: ${t.get('stop_loss',0):.2f}")
            c4.write(f"Target: ${t.get('take_profit',0):.2f}")
            c5.write(f"AI: {t.get('ai_score','—')}/100 | Risk: ${t.get('risk_amount',0):.0f}")
else:
    st.info("No open positions")

st.divider()

# ── Recent closed trades ──────────────────────────────────────────────────────
st.subheader("Recent Closed Trades")
if feedback:
    recent = sorted(feedback, key=lambda x: x.get("date",""), reverse=True)[:20]
    for t in recent:
        pnl = t.get("pnl_pct", 0)
        col = "🟢" if pnl > 0 else "🔴"
        st.write(
            f"{col} **{t['symbol']}** {t['direction']} | "
            f"{t.get('date','')} | "
            f"P&L: {pnl:+.1f}% | "
            f"Reason: {t.get('close_reason','—')} | "
            f"Regime: {t.get('regime','—')} | "
            f"AI score: {t.get('ai_score','—')}"
        )
else:
    st.info("No closed trades yet — keep the bot running")

# ── P&L chart ─────────────────────────────────────────────────────────────────
if feedback:
    st.subheader("Cumulative P&L")
    import pandas as pd
    df = pd.DataFrame(feedback)
    if "date" in df.columns and "pnl_dollar" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        df["cumulative_pnl"] = df["pnl_dollar"].cumsum()
        st.line_chart(df.set_index("date")["cumulative_pnl"])

# ── Backtest results ──────────────────────────────────────────────────────────
if bt:
    st.divider()
    st.subheader("Latest Backtest Results")
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("Signals", bt.get("total_signals", 0))
    b2.metric("Win Rate", f"{bt.get('win_rate',0):.1f}%")
    b3.metric("Expectancy", f"{bt.get('expectancy_pct',0):+.2f}%")
    b4.metric("Period", f"{bt.get('period_days',0)} days")
    col_l, col_r = st.columns(2)
    col_l.metric("Long Win Rate",  f"{bt.get('long_win_rate',0):.1f}%")
    col_r.metric("Short Win Rate", f"{bt.get('short_win_rate',0):.1f}%")
    if bt.get("regime_stats"):
        st.write("**By regime:**")
        for regime, stats in bt["regime_stats"].items():
            total_r = stats["wins"] + stats["losses"]
            if total_r:
                rwr = stats["wins"] / total_r * 100
                st.write(f"  {regime}: {rwr:.1f}% WR ({total_r} trades)")

# ── Auto-adjust recommendations ───────────────────────────────────────────────
if adj:
    st.divider()
    st.subheader("Auto-Adjust Recommendations")
    st.info(adj.get("summary",""))
    st.write(f"**Priority change:** {adj.get('priority_change','')}")
    st.write(f"**Confidence:** {adj.get('confidence','')}")
    for a in adj.get("adjustments", []):
        st.write(f"• `{a['param']}`: {a['current']} → **{a['suggested']}** — {a['reason']}")

# ── Log viewer ────────────────────────────────────────────────────────────────
st.divider()
st.subheader("Latest Log")
log_file = STATE_DIR / "bot.log"
if log_file.exists():
    lines = log_file.read_text().strip().split("\\n")
    st.code("\\n".join(lines[-50:]), language="text")
else:
    st.info("No log file yet")

st.caption(f"Last refreshed: {datetime.now().strftime('%H:%M:%S')} ET | Auto-refreshes every 60s")
st.markdown(
    "<script>setTimeout(()=>window.location.reload(), 60000)</script>",
    unsafe_allow_html=True
)
'''

def generate_dashboard():
    """Write the Streamlit dashboard file to the state directory."""
    dash_file = STATE_DIR / "dashboard.py"
    dash_file.write_text(DASHBOARD_CODE)
    log(f"Dashboard written to {dash_file}")
    log("Run with: streamlit run bot_state/dashboard.py")
    _tg(
        "📊 *Dashboard generated*\n"
        "Run locally: `streamlit run bot_state/dashboard.py`\n"
        "Or deploy to Streamlit Cloud for free — share the repo."
    )
    return str(dash_file)


if __name__ == "__main__":
    main()
