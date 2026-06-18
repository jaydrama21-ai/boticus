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
GITHUB_TOKEN      = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO       = os.environ.get("GITHUB_REPOSITORY", "")
DASHBOARD_URL     = os.environ.get("DASHBOARD_URL", "")

ALPACA_HEADERS  = {
    "APCA-API-KEY-ID":     ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
    "Content-Type":        "application/json",
}

ai_client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
OPUS_MODEL   = "claude-opus-4-5"
SONNET_MODEL = "claude-sonnet-4-5"


# ── Watchlist ──────────────────────────────────────────────────────────────────
# CORE watchlist — always monitored, high liquidity, options depth
# These never get removed — they're the foundation
CORE_WATCHLIST = [
    # US Index ETFs — broad market coverage
    "SPY", "QQQ", "IWM", "DIA", "MDY", "VXX",
    # International indexes
    "EEM", "EFA", "FXI", "EWJ", "IEUR",
    # Mega cap tech
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN",
    # High-beta / news-driven
    "TSLA", "AMD", "PLTR", "COIN",
    # Financials
    "JPM", "BAC", "GS", "MS", "C",
    # Energy
    "XOM", "CVX", "MRO",
    # Healthcare / biotech
    "UNH", "LLY", "ABBV",
    # Sector ETFs — rotation signals
    "XLK", "XLF", "XLE", "XLV", "XBI", "XRT", "XLU", "XLI", "XLP",
    # Macro proxies
    "TLT", "GLD", "SLV", "USO", "UUP",
    # Small cap / mid cap
    "IJR", "MDY",
]

# DYNAMIC candidate universe — scanned daily, best movers get added temporarily
DYNAMIC_UNIVERSE = [
    # Biotech / pharma — FDA catalyst plays
    "MRNA", "BNTX", "REGN", "BIIB", "GILD", "VRTX", "SRPT",
    "ALNY", "INCY", "BMRN", "RGEN", "EXEL", "IONS", "RARE",
    "ACAD", "SAGE", "BEAM", "EDIT", "NTLA", "CRSP",
    # Meme / WSB retail favorites
    "GME", "AMC", "SOFI", "RIVN", "LCID", "HOOD",
    "RBLX", "SNAP", "UBER", "LYFT", "DKNG", "PENN",
    "CLOV", "WISH", "WKHS", "NKLA", "GOEV",
    # Small cap momentum (under $10B market cap)
    "SMCI", "CELH", "HIMS", "JOBY", "ACHR", "LUNR",
    "SOUN", "BBAI", "KTOS", "RKLB", "ASTS", "SATL",
    "GEVO", "PLUG", "FCEL", "BLNK", "CHPT",
    # Mid cap growth / SaaS
    "CRWD", "PANW", "DDOG", "SNOW", "NET", "MDB",
    "BILL", "ZS", "OKTA", "HUBS", "GTLB", "BRZE",
    "MNDY", "CFLT", "ESTC", "PATH", "AI", "BBAI",
    # Semiconductors
    "ARM", "ASML", "TSM", "AVGO", "QCOM", "MRVL",
    "SWKS", "QRVO", "MPWR", "WOLF", "ON", "AMAT",
    "KLAC", "LRCX", "COHR", "AMBA", "ALGM",
    # International ADRs / growth
    "SHOP", "MELI", "SE", "GRAB", "BABA", "JD", "PDD",
    "NU", "STNE", "PAGS", "TCEHY", "NTES",
    # Commodity / inflation plays
    "FCX", "NEM", "AEM", "WPM", "GOLD", "MP", "LAC",
    "AA", "CLF", "X", "NUE", "STLD",
    # Defense / aerospace / space
    "LMT", "RTX", "NOC", "BA", "GD", "HII", "LDOS",
    "RKLB", "ASTS", "MAXR", "SPIR",
    # Real estate / REIT (rate sensitive)
    "VNQ", "XLRE", "AMT", "CCI", "EQIX", "PLD",
    # Consumer discretionary
    "TGT", "WMT", "COST", "HD", "LOW", "NKE", "LULU",
    "ABNB", "BKNG", "MAR", "HLT",
    # Leveraged ETFs — for high-conviction regime plays
    "TQQQ", "SQQQ", "SPXL", "SPXS", "SOXL", "SOXS",
    "UVXY", "SVXY", "TNA", "TZA",
    # Sector plays
    "XHB", "ITB", "KRE", "ARKK", "ARKG", "ARKW",
    "ICLN", "TAN", "FAN", "JETS", "PBW",
]

# Sector map
SECTOR_MAP = {
    "XLK": "tech",      "XLF": "financials", "XLE": "energy",
    "XLV": "healthcare","TLT": "bonds",       "GLD": "gold",
    "IWM": "small_cap", "XBI": "biotech",     "XRT": "retail",
    "KRE": "regional_banks", "ARKK": "innovation",
    "SLV": "silver",    "USO": "oil",         "XLU": "utilities",
    "XLI": "industrials","XLP": "consumer_staples",
    "EEM": "emerging_markets", "FXI": "china",
    "VNQ": "real_estate","UUP": "dollar",
    "IJR": "small_cap",  "MDY": "mid_cap",
}

# Headline-sensitive tickers — 1.5x news score amplification
HEADLINE_SENSITIVE = [
    "TSLA", "PLTR", "COIN", "AMD", "META", "GME", "AMC",
    "LLY", "MRNA", "BNTX", "GS", "XOM", "RIVN", "HOOD",
    "SMCI", "NVDA", "BABA", "ARM", "SOFI", "RBLX", "SNAP",
    "CRWD", "PANW", "AI", "SOUN", "ASTS", "RKLB",
]

OPTIONS_TICKERS = [
    "SPY", "QQQ", "AAPL", "NVDA", "TSLA",
    "MSFT", "META", "AMD", "PLTR", "IWM",
    "XBI", "GLD", "TLT", "EEM", "MDY",
]

# Active watchlist — starts as core, gets dynamic tickers added/removed each day
# Stored in STATE_DIR so it persists between runs
_ACTIVE_WATCHLIST = None

def load_active_watchlist() -> list:
    """Load the current active watchlist from state file."""
    global _ACTIVE_WATCHLIST
    wl_file = STATE_DIR / "watchlist.json"
    if wl_file.exists():
        try:
            data = json.loads(wl_file.read_text())
            _ACTIVE_WATCHLIST = data.get("active", list(CORE_WATCHLIST))
            return _ACTIVE_WATCHLIST
        except: pass
    _ACTIVE_WATCHLIST = list(CORE_WATCHLIST)
    return _ACTIVE_WATCHLIST

def save_active_watchlist(wl: list):
    """Persist the active watchlist."""
    global _ACTIVE_WATCHLIST
    _ACTIVE_WATCHLIST = wl
    wl_file = STATE_DIR / "watchlist.json"
    wl_file.write_text(json.dumps({
        "active":    wl,
        "core":      CORE_WATCHLIST,
        "updated_at": datetime.now(ET).isoformat(),
    }, indent=2))

def get_watchlist() -> list:
    """Returns current active watchlist."""
    if _ACTIVE_WATCHLIST is None:
        return load_active_watchlist()
    return _ACTIVE_WATCHLIST

def update_dynamic_watchlist():
    """
    Daily watchlist refresh — runs at market open.
    1. Scans DYNAMIC_UNIVERSE for hot candidates (volume, momentum, news)
    2. Adds best movers to active watchlist (up to MAX_DYNAMIC slots)
    3. Removes underperforming dynamic tickers (low volume, no signals, stale)
    4. Core tickers are never removed
    5. Sends Telegram summary of changes
    """
    import yfinance as yf
    MAX_DYNAMIC   = 15   # Max dynamic tickers at any time
    MIN_VOLUME    = 500_000  # Minimum avg daily volume to qualify
    log("Updating dynamic watchlist...")

    current_wl  = get_watchlist()
    core_set    = set(CORE_WATCHLIST)
    dynamic_now = [t for t in current_wl if t not in core_set]

    # ── Score dynamic universe candidates ────────────────────────────────
    candidates = []
    for symbol in DYNAMIC_UNIVERSE:
        if symbol in core_set:
            continue
        try:
            ticker = yf.Ticker(symbol)
            hist   = ticker.history(period="10d")
            if hist.empty or len(hist) < 5:
                continue

            closes  = hist["Close"].values
            volumes = hist["Volume"].values
            avg_vol = float(np.mean(volumes[-10:]))

            if avg_vol < MIN_VOLUME:
                continue

            # Score: momentum (5d return) + volume surge + RSI momentum
            ret_5d    = (closes[-1] - closes[-5]) / closes[-5] * 100
            vol_ratio = float(volumes[-1]) / avg_vol if avg_vol else 0
            rsi       = calc_rsi(closes)

            # Scoring formula
            score = 0
            score += min(40, abs(ret_5d) * 4)    # Big move = high score
            score += min(30, vol_ratio * 15)       # Volume surge
            score += 20 if 50 <= rsi <= 70 else 10 if 40 <= rsi <= 75 else 0
            score += 10 if symbol in HEADLINE_SENSITIVE else 0

            candidates.append({
                "symbol":    symbol,
                "score":     round(score, 1),
                "ret_5d":    round(ret_5d, 2),
                "vol_ratio": round(vol_ratio, 2),
                "rsi":       round(rsi, 1),
                "avg_vol":   int(avg_vol),
            })
        except: pass

    # Sort by score
    candidates.sort(key=lambda x: x["score"], reverse=True)
    top_candidates = [c["symbol"] for c in candidates[:MAX_DYNAMIC]]

    # ── Score existing dynamic tickers for removal ────────────────────────
    to_remove = []
    wl_file   = STATE_DIR / "watchlist.json"
    # Load signal history to see which dynamic tickers have been useful
    trades    = load_trades()
    traded_syms = {t.get("symbol") for t in trades}

    for sym in dynamic_now:
        try:
            ticker = yf.Ticker(sym)
            hist   = ticker.history(period="5d")
            if hist.empty:
                to_remove.append((sym, "no price data"))
                continue

            closes  = hist["Close"].values
            volumes = hist["Volume"].values
            avg_vol = float(np.mean(volumes))

            # Remove if: volume dried up, flat movement, or not in top candidates
            ret_3d    = (closes[-1] - closes[-3]) / closes[-3] * 100 if len(closes) >= 3 else 0
            vol_ratio = float(volumes[-1]) / avg_vol if avg_vol else 0

            if avg_vol < MIN_VOLUME:
                to_remove.append((sym, f"volume too low (avg {avg_vol:,.0f})"))
            elif abs(ret_3d) < 0.5 and vol_ratio < 0.8 and sym not in traded_syms:
                to_remove.append((sym, f"stale: {ret_3d:+.1f}% 3d, {vol_ratio:.1f}x vol"))
        except:
            to_remove.append((sym, "data error"))

    # ── Build new watchlist ───────────────────────────────────────────────
    # Start with core
    new_wl = list(CORE_WATCHLIST)

    # Keep dynamic tickers not being removed
    kept = [s for s in dynamic_now if s not in [r[0] for r in to_remove]]
    new_wl.extend(kept)

    # Add new candidates (avoid duplicates)
    added = []
    for sym in top_candidates:
        if sym not in new_wl and len([s for s in new_wl if s not in core_set]) < MAX_DYNAMIC:
            new_wl.append(sym)
            added.append(sym)

    # Deduplicate while preserving order
    seen = set()
    new_wl = [x for x in new_wl if not (x in seen or seen.add(x))]

    save_active_watchlist(new_wl)

    # ── Summary ───────────────────────────────────────────────────────────
    dynamic_after = [t for t in new_wl if t not in core_set]
    log(f"Watchlist updated: {len(new_wl)} total "
        f"({len(CORE_WATCHLIST)} core + {len(dynamic_after)} dynamic)")

    if added or to_remove:
        removed_syms = [r[0] for r in to_remove]
        log(f"  Added: {added}")
        log(f"  Removed: {removed_syms}")

        top3 = candidates[:3]
        top_str = "\n".join([
            f"  {c['symbol']}: {c['ret_5d']:+.1f}% 5d | {c['vol_ratio']:.1f}x vol | RSI {c['rsi']:.0f}"
            for c in top3
        ])

        _tg(
            f"📋 *Watchlist Updated*\n"
            f"Total: {len(new_wl)} tickers "
            f"({len(CORE_WATCHLIST)} core + {len(dynamic_after)} dynamic)\n\n"
            + (f"*Added ({len(added)}):* {', '.join(added)}\n" if added else "") +
            (f"*Removed ({len(removed_syms)}):* {', '.join(removed_syms)}\n" if removed_syms else "") +
            f"\n*Top movers in universe:*\n{top_str}"
        )
    else:
        log("  No watchlist changes today")

    return new_wl


# ── Use WATCHLIST as alias for get_watchlist() ────────────────────────────────
# Existing code references WATCHLIST — this makes it dynamic
WATCHLIST = property(get_watchlist) if False else CORE_WATCHLIST  # bootstrap

# ── Risk config ────────────────────────────────────────────────────────────────
# Auto-adjusted (high confidence) 2026-06-18:
# RSI 52→60, volume 1.3→1.5, take_profit 2.0→2.5, ranging excluded
# Recent regime: STABLE (+0.2% delta) — long WR improving (35% recent vs 32.7% historical)
RISK = {
    "stop_loss_atr_mult":    1.5,
    "take_profit_atr_mult":  2.5,   # was 2.0 — let winners run longer
    "max_position_pct":      0.05,
    "max_risk_per_trade_pct":0.02,
    "max_daily_loss_pct":    0.02,
    "max_open_positions":    6,
    "rsi_min":               60,    # was 52 — filter weak 40-60 RSI zone
    "rsi_max":               72,
    "volume_min_mult":       1.5,   # was 1.3 — require stronger conviction
    "atr_pct_max":           0.04,
    "dead_money_hours":      4,
    "max_hold_hours":        6,
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
        self.headline_score = 0.0
        self.headlines = []
        self.macro_triggers = []   # High-impact macro events detected in headlines
        self.macro_alert = False   # True if any high-impact macro trigger found

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
        self.reddit_mentions = {}    # symbol -> mention data (now StockTwits)
        self.sector_rotation = {}    # sector -> rotation data
        self.unusual_volume  = []    # list of unusual volume symbols
        self.fear_greed      = {"score": 50, "rating": "neutral", "change": 0}  # CNN F&G

tickers: dict[str, TickerData] = {}
macro = MacroData()

NEG_KEYWORDS = [
    # ── Company-specific negatives ─────────────────────────────────────────
    "fraud","sec investigation","bankruptcy","recall","downgrade",
    "guidance cut","earnings miss","layoff","lawsuit","restatement","delisting",
    "data breach","accounting","whistleblower","short seller","going concern",
    "class action","criminal charges","doj investigation","ftc investigation",
    "product recall","safety recall","plant shutdown","factory fire",

    # ── Macro / Fed negatives ──────────────────────────────────────────────
    "rate hike","hawkish","tightening","inflation surge","cpi higher",
    "recession fears","yield curve inverts","stagflation","credit crunch",
    "bank failure","systemic risk","liquidity crisis","margin calls",
    "fed raises","rate increase","quantitative tightening",

    # ── Geopolitical negatives ─────────────────────────────────────────────
    "war escalates","military strike","missile attack","nuclear threat",
    "sanctions imposed","trade war","tariffs increased","embargo",
    "invasion","terrorist attack","cyber attack","infrastructure attack",
    "oil supply cut","opec cut","energy crisis","supply chain disruption",
    "iran attack","north korea","russia escalates","china threatens",
    "taiwan strait","south china sea","houthi attack","strait of hormuz",

    # ── Trump / Political negatives ────────────────────────────────────────
    "trump tariffs","trump imposes","trump threatens","trump sanctions",
    "trump fires","trump bans","trump executive order","trump withdraws",
    "government shutdown","debt ceiling","default risk","credit downgrade",
    "impeachment","indictment","investigation launched","subpoena",
    "election disputed","political crisis","congress blocks",

    # ── Market structure negatives ─────────────────────────────────────────
    "flash crash","circuit breaker","trading halted","market selloff",
    "margin call","forced liquidation","deleveraging","fund collapse",
    "contagion","bank run","credit downgrade","sovereign default",
]

POS_KEYWORDS = [
    # ── Company-specific positives ─────────────────────────────────────────
    "beat expectations","record revenue","upgrade","raised guidance","buyback",
    "dividend increase","fda approval","contract win","partnership","acquisition",
    "ai deal","data center","earnings beat","raised forecast","record profit",
    "market share gain","new product launch","clinical trial success",
    "patent approved","ipo debut","strategic alliance","merger approved",
    "cost cutting","margin expansion","share repurchase","special dividend",

    # ── Macro / Fed positives ──────────────────────────────────────────────
    "rate cut","dovish","easing","inflation cools","cpi lower",
    "soft landing","gdp beat","jobs strong","fed pauses","rate hold",
    "quantitative easing","stimulus","fed pivot","lower rates",
    "inflation falls","deflation","yield drops","bonds rally",

    # ── Geopolitical positives ─────────────────────────────────────────────
    "ceasefire","peace deal","trade deal","tariffs reduced","sanctions lifted",
    "diplomatic breakthrough","nato unity","alliances strengthened",
    "oil supply increase","opec increases","energy prices fall",
    "supply chain normalizes","trade agreement signed","wto ruling",

    # ── Trump / Political positives ────────────────────────────────────────
    "trump deal","trump signs","trump approves","trump lifts","trump reduces",
    "deregulation","tax cuts","infrastructure bill","trade surplus",
    "trump tariff pause","tariff exemption","trade truce","china deal",
    "bipartisan deal","budget passed","debt ceiling raised","stimulus approved",

    # ── Market structure positives ─────────────────────────────────────────
    "short squeeze","gamma squeeze","strong earnings season","buyback program",
    "institutional buying","insider buying","record inflows","etf creation",
    "index addition","s&p 500 addition","fund inflows","short covering",
]

# ── Macro event keywords — these trigger immediate regime reassessment ─────────
MACRO_TRIGGERS = {
    # Trump actions — immediate market movers
    "trump":        {"impact": "high",   "direction": "mixed",  "note": "Trump statement — check context"},
    "tariff":       {"impact": "high",   "direction": "bearish","note": "Tariffs = inflation + trade war risk"},
    "trade war":    {"impact": "high",   "direction": "bearish","note": "Trade war = risk off"},
    "trump tweet":  {"impact": "high",   "direction": "mixed",  "note": "Trump social post — volatile"},
    "truth social": {"impact": "medium", "direction": "mixed",  "note": "Trump platform statement"},

    # Fed / rates
    "federal reserve":   {"impact": "high",   "direction": "mixed",  "note": "Fed statement — major mover"},
    "jerome powell":     {"impact": "high",   "direction": "mixed",  "note": "Fed chair speaking"},
    "fomc":              {"impact": "high",   "direction": "mixed",  "note": "Fed meeting — sit out"},
    "interest rate":     {"impact": "high",   "direction": "mixed",  "note": "Rate decision incoming"},
    "rate cut":          {"impact": "high",   "direction": "bullish","note": "Rate cuts = risk on"},
    "rate hike":         {"impact": "high",   "direction": "bearish","note": "Rate hikes = risk off"},
    "inflation":         {"impact": "medium", "direction": "bearish","note": "Inflation = hawkish risk"},
    "cpi":               {"impact": "high",   "direction": "mixed",  "note": "CPI print — major mover"},

    # Geopolitical — Middle East
    "iran":              {"impact": "high",   "direction": "bearish","note": "Iran = oil risk + geopolitical"},
    "houthi":            {"impact": "medium", "direction": "bearish","note": "Red Sea = supply chain risk"},
    "israel":            {"impact": "medium", "direction": "bearish","note": "Middle East tension"},
    "oil":               {"impact": "high",   "direction": "mixed",  "note": "Oil price = inflation signal"},
    "opec":              {"impact": "high",   "direction": "mixed",  "note": "OPEC decision = energy sector"},
    "strait of hormuz":  {"impact": "high",   "direction": "bearish","note": "Chokepoint risk = oil spike"},

    # Russia / Ukraine
    "russia":            {"impact": "high",   "direction": "bearish","note": "Russia = energy + risk off"},
    "ukraine":           {"impact": "medium", "direction": "bearish","note": "War escalation risk"},
    "nato":              {"impact": "medium", "direction": "mixed",  "note": "NATO statement = geopolitical"},
    "putin":             {"impact": "high",   "direction": "bearish","note": "Putin statement = risk off"},

    # China / Taiwan
    "china":             {"impact": "high",   "direction": "mixed",  "note": "China = trade + tech risk"},
    "taiwan":            {"impact": "high",   "direction": "bearish","note": "Taiwan = chip supply chain"},
    "xi jinping":        {"impact": "high",   "direction": "bearish","note": "Xi statement = China policy"},
    "south china sea":   {"impact": "high",   "direction": "bearish","note": "Military tension"},
    "semiconductor":     {"impact": "high",   "direction": "mixed",  "note": "Chip supply = tech sector"},
    "nvidia ban":        {"impact": "high",   "direction": "bearish","note": "Export controls = tech hit"},

    # North Korea
    "north korea":       {"impact": "high",   "direction": "bearish","note": "NK = risk off spike"},
    "missile test":      {"impact": "high",   "direction": "bearish","note": "Military provocation"},

    # Market events
    "flash crash":       {"impact": "high",   "direction": "bearish","note": "Emergency — reduce exposure"},
    "circuit breaker":   {"impact": "high",   "direction": "bearish","note": "Market halt"},
    "bank failure":      {"impact": "high",   "direction": "bearish","note": "Systemic risk"},
    "default":           {"impact": "high",   "direction": "bearish","note": "Sovereign/corporate default"},

    # Crypto — affects COIN, general sentiment
    "bitcoin":           {"impact": "medium", "direction": "mixed",  "note": "Crypto sentiment signal"},
    "crypto crash":      {"impact": "high",   "direction": "bearish","note": "Risk off signal"},
    "sec crypto":        {"impact": "medium", "direction": "bearish","note": "Regulatory risk"},

    # AI / Tech
    "openai":            {"impact": "medium", "direction": "bullish","note": "AI news = tech sector boost"},
    "ai breakthrough":   {"impact": "medium", "direction": "bullish","note": "AI = growth narrative"},
    "chatgpt":           {"impact": "low",    "direction": "bullish","note": "AI sentiment"},
    "deepseek":          {"impact": "medium", "direction": "bearish","note": "AI competition risk for NVDA"},
}


def score_headlines(headlines: list, symbol: str) -> dict:
    """
    Score headlines using three layers:
    1. Company-specific positive/negative keywords
    2. Macro trigger detection (Trump, Fed, geopolitical)
    3. Amplification for headline-sensitive tickers

    Returns score (-100 to +100), flags, macro triggers found, and key headlines.
    """
    if not headlines:
        return {"score": 0, "bullish": 0, "bearish": 0,
                "has_negative": False, "has_positive": False,
                "key": [], "macro_triggers": [], "macro_alert": False}

    bullish       = 0
    bearish       = 0
    key           = []
    macro_found   = []
    macro_alert   = False

    for h in headlines:
        h_low = h.lower()

        # Layer 1: Company keywords
        pos = sum(1 for kw in POS_KEYWORDS if kw in h_low)
        neg = sum(1 for kw in NEG_KEYWORDS if kw in h_low)
        bullish += pos
        bearish += neg
        if pos > 0 or neg > 0:
            key.append(f"{'+ ' if pos > neg else '- '}{h[:80]}")

        # Layer 2: Macro trigger detection
        for trigger, meta in MACRO_TRIGGERS.items():
            if trigger in h_low:
                macro_found.append({
                    "trigger":   trigger,
                    "impact":    meta["impact"],
                    "direction": meta["direction"],
                    "note":      meta["note"],
                    "headline":  h[:100],
                })
                if meta["impact"] == "high":
                    macro_alert = True
                # Apply directional scoring for macro triggers
                if meta["direction"] == "bullish":
                    bullish += 2 if meta["impact"] == "high" else 1
                elif meta["direction"] == "bearish":
                    bearish += 2 if meta["impact"] == "high" else 1
                # Mixed impact = add to both (uncertainty)
                elif meta["direction"] == "mixed":
                    bullish += 1
                    bearish += 1

    # Layer 3: Amplify for headline-sensitive tickers
    amp = 1.5 if symbol in HEADLINE_SENSITIVE else 1.0
    score = round((bullish - bearish) * 20 * amp, 1)
    score = max(-100, min(100, score))

    # Log macro alerts
    if macro_found:
        high_impact = [m for m in macro_found if m["impact"] == "high"]
        if high_impact:
            log(f"  🚨 MACRO TRIGGER on {symbol}: "
                f"{', '.join(m['trigger'] for m in high_impact[:3])}")

    return {
        "score":          score,
        "bullish":        bullish,
        "bearish":        bearish,
        "has_negative":   bearish > 0,
        "has_positive":   bullish > 0,
        "key":            key[:3],
        "macro_triggers": macro_found[:5],
        "macro_alert":    macro_alert,
    }


# ══════════════════════════════════════════════════════════════════════════════
# REDDIT RESEARCH SCRAPER
# Uses Reddit API (OAuth) to pull top research posts from target subreddits
# Extracts trading insights using Sonnet and stores as research_digest.json
# Runs weekly — bot reads digest on every signal score
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# REDDIT RESEARCH SCRAPER — no credentials needed
# Uses Reddit's public JSON endpoints (old.reddit.com)
# Pulls top research posts weekly, extracts insights with Sonnet
# ══════════════════════════════════════════════════════════════════════════════

REDDIT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

# Fallback user agents to rotate if blocked
REDDIT_UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36",
]

RESEARCH_SUBREDDITS = []  # Kept for reference, no longer used

# ══════════════════════════════════════════════════════════════════════════════
# STOCKTWITS SENTIMENT — ticker-specific retail sentiment, free, no auth
# Replaces Reddit which blocks GitHub Actions IPs
# ══════════════════════════════════════════════════════════════════════════════

def fetch_stocktwits_sentiment(symbols: list) -> dict:
    """
    Pull real-time retail sentiment from StockTwits for each ticker.
    Free public API — no auth, no IP blocking.
    Returns dict of symbol -> {bullish_pct, bearish_pct, message_count, trending}
    """
    results = {}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; boticus/1.0)"}

    for sym in symbols[:20]:  # Limit to avoid rate limiting
        try:
            r = requests.get(
                f"https://api.stocktwits.com/api/2/streams/symbol/{sym}.json",
                headers=headers,
                timeout=8
            )
            if not r.ok:
                continue

            data     = r.json()
            messages = data.get("messages", [])
            symbol_d = data.get("symbol", {})

            if not messages:
                continue

            # Count sentiment from message entities
            bullish = sum(1 for m in messages
                         if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish")
            bearish = sum(1 for m in messages
                         if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish")
            total   = len(messages)
            bull_pct = bullish / total * 100 if total else 50
            bear_pct = bearish / total * 100 if total else 50

            # Watchlist count = how many StockTwits users watching
            watchers  = symbol_d.get("watchlist_count", 0)
            trending  = total >= 10 and (bull_pct >= 70 or bear_pct >= 70)

            results[sym] = {
                "bullish_pct":  round(bull_pct, 1),
                "bearish_pct":  round(bear_pct, 1),
                "message_count": total,
                "watchers":     watchers,
                "trending":     trending,
                "sentiment":    "bullish" if bull_pct > 60 else "bearish" if bear_pct > 60 else "neutral",
            }

            if trending or bull_pct >= 70 or bear_pct >= 70:
                log(f"  StockTwits {sym}: {bull_pct:.0f}% bull / {bear_pct:.0f}% bear "
                    f"({total} msgs) {'🔥 TRENDING' if trending else ''}")

        except Exception as e:
            pass  # Silent — not critical
        time.sleep(0.3)

    return results


def fetch_fear_greed() -> dict:
    """
    Pull CNN Fear & Greed Index — free, no auth, works from any IP.
    Returns current score (0=extreme fear, 100=extreme greed) and rating.
    Useful as market-wide sentiment overlay for signal scoring.
    """
    try:
        r = requests.get(
            "https://production.dataviz.cnn.io/index/fearandgreed/graphdata",
            headers={"User-Agent": "Mozilla/5.0 (compatible; boticus/1.0)",
                     "Referer": "https://edition.cnn.com/"},
            timeout=10
        )
        if r.ok:
            data  = r.json()
            score = data.get("fear_and_greed", {}).get("score", 50)
            rating = data.get("fear_and_greed", {}).get("rating", "neutral")
            prev  = data.get("fear_and_greed_historical", {}).get("previous_close", {}).get("score", score)
            change = score - prev

            result = {
                "score":   round(float(score), 1),
                "rating":  rating,  # extreme_fear, fear, neutral, greed, extreme_greed
                "change":  round(float(change), 1),
                "bullish": score >= 60,
                "bearish": score <= 40,
            }
            log(f"  Fear & Greed: {score:.0f} ({rating}) {change:+.1f} from yesterday")
            return result
    except Exception as e:
        log(f"  Fear & Greed fetch error: {e}", "WARN")
    return {"score": 50, "rating": "neutral", "change": 0, "bullish": False, "bearish": False}


def fetch_stocktwits_trending() -> list:
    """
    Pull StockTwits trending tickers — what retail is talking about right now.
    Returns list of trending symbols to potentially add to watchlist.
    """
    try:
        r = requests.get(
            "https://api.stocktwits.com/api/2/trending/symbols.json",
            headers={"User-Agent": "Mozilla/5.0 (compatible; boticus/1.0)"},
            timeout=8
        )
        if r.ok:
            symbols = r.json().get("symbols", [])
            tickers = [s["symbol"] for s in symbols[:15] if s.get("symbol")]
            log(f"  StockTwits trending: {', '.join(tickers[:10])}")
            return tickers
    except Exception as e:
        log(f"  StockTwits trending error: {e}", "WARN")
    return []


# ══════════════════════════════════════════════════════════════════════════════
# RESEARCH DIGEST — weekly intelligence summary
# StockTwits + Fear & Greed + Fed Speeches + Earnings Transcripts
# ══════════════════════════════════════════════════════════════════════════════

def fetch_fed_speeches(max_speeches: int = 3) -> list:
    """Pull latest Fed speeches from federalreserve.gov RSS. Free, no auth."""
    speeches = []
    try:
        from xml.etree import ElementTree as ET
        from html.parser import HTMLParser

        class TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.text = []; self.skip = False
                self.skip_tags = {"script","style","nav","header","footer"}
            def handle_starttag(self, tag, attrs):
                if tag in self.skip_tags: self.skip = True
            def handle_endtag(self, tag):
                if tag in self.skip_tags: self.skip = False
            def handle_data(self, data):
                if not self.skip and data.strip(): self.text.append(data.strip())

        # Fed speeches RSS
        for feed_url, label in [
            ("https://www.federalreserve.gov/feeds/speeches.xml", "speech"),
            ("https://www.federalreserve.gov/feeds/press_monetary.xml", "FOMC"),
        ]:
            r = requests.get(feed_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; boticus/1.0)"},
                timeout=10)
            if not r.ok: continue

            root  = ET.fromstring(r.content)
            items = root.findall(".//item")[:3]
            for item in items:
                title = (item.findtext("title") or "").strip()
                link  = (item.findtext("link") or "").strip()
                if not link or not title: continue

                # Fetch full text
                sr = requests.get(link,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; boticus/1.0)"},
                    timeout=12)
                if not sr.ok: continue

                parser = TextExtractor()
                parser.feed(sr.text)
                full_text = " ".join(parser.text)[:4000]

                if len(full_text) < 300: continue

                speaker = "Fed"
                for name in ["Powell","Jefferson","Waller","Cook","Kugler","Barr","Bowman","Logan"]:
                    if name in title: speaker = name; break
                if label == "FOMC": speaker = "FOMC"

                speeches.append({
                    "title":   title[:200],
                    "speaker": speaker,
                    "url":     link,
                    "text":    full_text,
                })
                log(f"  Fed {label}: {speaker} — {title[:60]}")
                if len(speeches) >= max_speeches: break
            if len(speeches) >= max_speeches: break

    except Exception as e:
        log(f"  Fed speeches error: {e}", "WARN")
    return speeches


def fetch_earnings_transcripts(symbols: list, days_back: int = 14) -> list:
    """
    Pull earnings call highlights from Motley Fool free pages.
    Only fetches tickers that reported in the last days_back days.
    """
    import re as _re
    from datetime import date as _date

    transcripts = []
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

    PRIORITY = ["NVDA","AAPL","MSFT","GOOGL","META","AMZN","TSLA","AMD",
                "JPM","GS","MS","BAC","LLY","UNH","XOM","CVX"]
    ordered = [s for s in PRIORITY if s in symbols] + \
              [s for s in symbols if s not in PRIORITY]

    for sym in ordered[:10]:
        try:
            url = f"https://www.fool.com/earnings/call-transcripts/{sym.lower()}/"
            r   = requests.get(url, headers=headers, timeout=10)
            if not r.ok: continue

            links = _re.findall(
                r'href="(/earnings/call-transcripts/\d{4}/\d{2}/\d{2}/[^"]+)"',
                r.text
            )
            if not links: continue

            # Check date
            date_match = _re.search(r'/(\d{4})/(\d{2})/(\d{2})/', links[0])
            if date_match:
                t_date = _date(int(date_match.group(1)),
                               int(date_match.group(2)),
                               int(date_match.group(3)))
                if (_date.today() - t_date).days > days_back:
                    continue

            # Fetch transcript
            tr = requests.get(f"https://www.fool.com{links[0]}", headers=headers, timeout=12)
            if not tr.ok: continue

            # Extract text
            clean = _re.sub(r'<[^>]+>', ' ', tr.text)
            clean = _re.sub(r'\s+', ' ', clean).strip()[:3000]
            if len(clean) < 300: continue

            # Find guidance statements
            guidance = _re.findall(
                r'(?:guidance|expect|outlook|revenue|margin|EPS|Q\d)[^.]{20,200}\.',
                clean[:4000], _re.IGNORECASE
            )

            transcripts.append({
                "symbol":     sym,
                "url":        f"https://www.fool.com{links[0]}",
                "highlights": guidance[:5],
                "text":       clean,
            })
            log(f"  Earnings: {sym} transcript found")
            time.sleep(0.5)

        except Exception: pass

    log(f"Earnings transcripts: {len(transcripts)} found")
    return transcripts


def summarize_fed_and_earnings(fed: list, earnings: list) -> dict:
    """Feed Fed speeches + earnings to Sonnet for market-moving insights."""
    if not fed and not earnings:
        return {}

    sections = []
    if fed:
        sections.append("=== FED SPEECHES ===")
        for s in fed:
            sections.append(f"{s['speaker']} — {s['title']}\n{s['text'][:1500]}")
    if earnings:
        sections.append("\n=== EARNINGS CALLS ===")
        for e in earnings:
            highlights = " | ".join(str(h)[:120] for h in e["highlights"][:3])
            sections.append(f"{e['symbol']}: {highlights}\n{e['text'][:800]}")

    combined = "\n".join(sections)[:6000]

    try:
        resp = ai_client.messages.create(
            model=SONNET_MODEL, max_tokens=1000,
            system="Extract actionable trading signals from Fed speeches and earnings calls. Output only valid JSON.",
            messages=[{"role": "user", "content":
                f"Extract 3-5 trading insights from these Fed and earnings sources:\n\n{combined}\n\n"
                f'Output JSON: {{"insights": [{{"finding": "specific finding", "tickers": ["AAPL"], "impact": "bullish/bearish/neutral", "confidence": "high/medium/low", "source": "Fed/Earnings", "actionable": "how to apply"}}], "summary": "2-3 sentences on key market implications"}}'
            }]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"): raw = raw.split("```")[1]; raw = raw[4:] if raw.startswith("json") else raw
        result = json.loads(raw.strip())
        log(f"Fed/Earnings insights: {len(result.get('insights',[]))}")
        return result
    except Exception as e:
        log(f"Fed/Earnings summarize error: {e}", "WARN")
        return {}


def run_research_digest() -> dict:
    """
    Weekly intelligence digest — runs Sundays.
    Pulls: StockTwits sentiment + Fear & Greed + Fed speeches + Earnings transcripts
    Extracts insights with Sonnet → feeds AI brain on every signal score
    """
    log("Running weekly research digest (StockTwits + Fear & Greed + Fed + Earnings)...")

    # 1. Market mood
    fg = fetch_fear_greed()

    # 2. StockTwits trending
    trending = fetch_stocktwits_trending()

    # 3. StockTwits sentiment on core watchlist
    core_syms = CORE_WATCHLIST[:20]
    sentiment = fetch_stocktwits_sentiment(core_syms)

    bull_syms = [s for s, v in sentiment.items() if v.get("bullish_pct", 50) >= 65]
    bear_syms = [s for s, v in sentiment.items() if v.get("bearish_pct", 50) >= 65]

    # 4. Fed speeches + FOMC statements
    log("Fetching Fed speeches...")
    fed_speeches = fetch_fed_speeches(max_speeches=3)

    # 5. Recent earnings transcripts for watchlist
    log("Fetching earnings transcripts...")
    earnings = fetch_earnings_transcripts(get_watchlist(), days_back=14)

    # 6. Sentiment context for Sonnet
    sentiment_context = (
        f"Fear & Greed Index: {fg['score']:.0f}/100 ({fg['rating']}) — "
        f"{'markets greedy, overextension risk' if fg['score'] >= 70 else 'markets fearful, opportunity' if fg['score'] <= 30 else 'neutral sentiment'}\n"
        f"Change from yesterday: {fg['change']:+.1f} points\n\n"
        f"StockTwits trending: {', '.join(trending[:10])}\n"
        f"Heavily bullish (65%+ bull): {', '.join(bull_syms) or 'none'}\n"
        f"Heavily bearish (65%+ bear): {', '.join(bear_syms) or 'none'}\n"
        + "\n".join([
            f"  {s}: {v['bullish_pct']:.0f}% bull / {v['bearish_pct']:.0f}% bear ({v['message_count']} msgs)"
            for s, v in sorted(sentiment.items(), key=lambda x: x[1].get("message_count",0), reverse=True)[:8]
        ])
    )

    # 7. Sentiment insights from Sonnet
    sentiment_insights = []
    sentiment_summary  = ""
    try:
        resp = ai_client.messages.create(
            model=SONNET_MODEL, max_tokens=800,
            system="Extract actionable trading insights from retail sentiment data. Output only valid JSON.",
            messages=[{"role": "user", "content":
                f"Extract 3-5 actionable trading insights from this retail sentiment data:\n\n{sentiment_context}\n\n"
                f'Output JSON: {{"insights": [{{"finding": "...", "tickers": [], "edge": "...", "confidence": "high/medium/low", "source": "StockTwits/F&G", "actionable": "..."}}], "summary": "2-3 sentences"}}'
            }]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"): raw = raw.split("```")[1]; raw = raw[4:] if raw.startswith("json") else raw
        result = json.loads(raw.strip())
        sentiment_insights = result.get("insights", [])
        sentiment_summary  = result.get("summary", "")
    except Exception as e:
        log(f"Sentiment insights error: {e}", "WARN")

    # 8. Fed + Earnings insights from Sonnet
    fed_earnings_result = summarize_fed_and_earnings(fed_speeches, earnings)
    fed_insights = fed_earnings_result.get("insights", [])
    fed_summary  = fed_earnings_result.get("summary", "")

    # Combine all insights
    all_insights = sentiment_insights + fed_insights

    # Save digest
    digest = {
        "updated_at":      datetime.now(ET).isoformat(),
        "source":          "StockTwits + CNN Fear & Greed + Fed + Earnings",
        "fear_greed":      fg,
        "trending":        trending[:15],
        "sentiment":       sentiment,
        "bull_tickers":    bull_syms,
        "bear_tickers":    bear_syms,
        "fed_speeches":    [{"title": s["title"], "speaker": s["speaker"], "url": s["url"]} for s in fed_speeches],
        "earnings":        [{"symbol": e["symbol"], "url": e["url"]} for e in earnings],
        "insights":        all_insights,
        "summary":         f"{sentiment_summary} {fed_summary}".strip(),
    }
    digest_file = STATE_DIR / "research_digest.json"
    digest_file.write_text(json.dumps(digest, indent=2))
    log(f"Research digest saved: {len(all_insights)} total insights ({len(sentiment_insights)} sentiment, {len(fed_insights)} Fed/earnings)")

    # Telegram summary
    fg_emoji = "🟢" if fg["score"] >= 60 else "🔴" if fg["score"] <= 40 else "🟡"
    findings_txt = "\n".join([f"• {i['finding'][:100]} ({i.get('source','')})" for i in all_insights[:5]])
    fed_str = f"\nFed: {', '.join(s['speaker'] for s in fed_speeches[:2])}" if fed_speeches else ""
    earn_str = f"\nEarnings: {', '.join(e['symbol'] for e in earnings[:5])}" if earnings else ""
    _tg(
        f"📚 *Weekly Research Digest*\n\n"
        f"{fg_emoji} *Fear & Greed: {fg['score']:.0f}/100* ({fg['rating'].replace('_',' ').title()}) "
        f"{fg['change']:+.1f} from yesterday\n"
        f"*Retail trending:* {', '.join(trending[:8])}"
        f"{fed_str}{earn_str}\n\n"
        f"*Bullish crowd:* {', '.join(bull_syms[:5]) or 'none'}\n"
        f"*Bearish crowd:* {', '.join(bear_syms[:5]) or 'none'}\n\n"
        f"*Key insights:*\n{findings_txt}"
    )
    return digest



def load_research_digest() -> str:
    """Load digest as AI brain context string."""
    digest_file = STATE_DIR / "research_digest.json"
    if not digest_file.exists():
        return ""
    try:
        digest  = json.loads(digest_file.read_text())
        updated = digest.get("updated_at", "")[:10]
        fg      = digest.get("fear_greed", {})
        items   = digest.get("insights", [])
        bull    = digest.get("bull_tickers", [])
        bear    = digest.get("bear_tickers", [])
        trend   = digest.get("trending", [])

        lines = [f"=== RETAIL SENTIMENT ({updated}) ==="]
        lines.append(f"Fear & Greed: {fg.get('score',50):.0f}/100 ({fg.get('rating','neutral')}) — "
                     f"{'risk-on bias' if fg.get('score',50) >= 60 else 'risk-off bias' if fg.get('score',50) <= 40 else 'neutral'}")
        if trend:
            lines.append(f"Retail trending: {', '.join(trend[:8])}")
        if bull:
            lines.append(f"Heavy retail bullishness: {', '.join(bull[:5])} — potential exhaustion risk")
        if bear:
            lines.append(f"Heavy retail bearishness: {', '.join(bear[:5])} — potential squeeze candidate")
        for i in items[:5]:
            lines.append(f"• {i.get('finding','')} [{i.get('confidence','')}]")
        lines.append(f"Summary: {digest.get('summary','')}")
        return "\n".join(lines)
    except Exception as e:
        log(f"Research digest load error: {e}", "WARN")
        return ""


def fetch_reddit_mentions(symbols: list) -> dict:
    """
    Pull top posts from research subreddits using public JSON.
    Rotates user agents and tries multiple URL formats to avoid 403s.
    """
    import random
    all_posts = []

    for sub in RESEARCH_SUBREDDITS:
        for sort in ["top", "hot"]:
            # Try different URL formats
            urls = [
                f"https://www.reddit.com/r/{sub}/{sort}.json",
                f"https://old.reddit.com/r/{sub}/{sort}.json",
            ]
            success = False
            for url in urls:
                if success: break
                try:
                    headers = {
                        "User-Agent": random.choice(REDDIT_UA_LIST),
                        "Accept": "application/json",
                    }
                    r = requests.get(
                        url, headers=headers,
                        params={"limit": limit_per_sub, "t": "week"},
                        timeout=12
                    )
                    if r.status_code == 403:
                        log(f"  Reddit r/{sub}/{sort}: HTTP 403 — trying next URL", "WARN")
                        time.sleep(2)
                        continue
                    if not r.ok:
                        log(f"  Reddit r/{sub}/{sort}: HTTP {r.status_code}", "WARN")
                        continue

                    posts = r.json().get("data", {}).get("children", [])
                    found = 0
                    for post in posts:
                        d     = post.get("data", {})
                        title = d.get("title", "")
                        body  = d.get("selftext", "")
                        score = d.get("score", 0)
                        url_p = f"https://reddit.com{d.get('permalink','')}"

                        text_lower   = (title + " " + body).lower()
                        is_research  = any(kw in text_lower for kw in RESEARCH_INDICATORS)
                        has_traction = score >= 15
                        not_removed  = body not in ("[removed]", "[deleted]", "")

                        if is_research and has_traction and not_removed:
                            all_posts.append({
                                "subreddit": sub,
                                "title":     title[:200],
                                "body":      body[:3000],
                                "score":     score,
                                "url":       url_p,
                                "created":   d.get("created_utc", 0),
                            })
                            found += 1

                    if found >= 0:  # Even 0 results = successful fetch
                        success = True
                        log(f"  Reddit r/{sub}/{sort}: {found} research posts")

                except Exception as e:
                    log(f"  Reddit r/{sub}/{sort}: {e}", "WARN")

            time.sleep(1.5)

    seen    = set()
    unique  = []
    for p in sorted(all_posts, key=lambda x: x["score"], reverse=True):
        if p["title"] not in seen:
            seen.add(p["title"])
            unique.append(p)

    log(f"Reddit research: {len(unique)} posts found across {len(RESEARCH_SUBREDDITS)} subreddits")
    return unique[:20]


def extract_trading_insights(posts: list) -> dict:
    """
    Feed research posts to Sonnet and extract actionable trading insights.
    Returns structured insights injected into signal scoring.
    """
    if not posts:
        return {}

    posts_text = "\n\n---\n\n".join([
        f"SUBREDDIT: r/{p['subreddit']} | UPVOTES: {p['score']}\n"
        f"TITLE: {p['title']}\n"
        f"URL: {p['url']}\n"
        f"CONTENT: {p['body'][:1500]}"
        for p in posts[:12]
    ])

    prompt = (
        "You are analyzing Reddit research posts about trading strategies.\n"
        "Extract ONLY findings that are data-backed and actionable for an algorithmic system.\n"
        "Focus on: win rates, entry conditions, VIX levels, time of day, regime conditions, "
        "RSI ranges, volume patterns, holding periods, sector performance, options strategies.\n"
        "Ignore opinions, predictions, and posts without actual data.\n\n"
        "Output ONLY valid JSON — no markdown, no preamble:\n"
        '{"insights": ['
        '{"finding": "specific data-backed finding",'
        '"tickers": ["SPY"] or [] if general,'
        '"condition": "when/where this applies",'
        '"edge": "the statistical edge found",'
        '"confidence": "high/medium/low",'
        '"source": "r/subreddit — post title",'
        '"actionable": "how to use in signal scoring"}'
        '], "summary": "2-3 sentences on the most important findings this week"}'
    )

    try:
        resp = ai_client.messages.create(
            model=SONNET_MODEL, max_tokens=1500,
            system="Extract only data-backed trading insights. Output only valid JSON.",
            messages=[{"role": "user", "content": f"{prompt}\n\nPOSTS:\n{posts_text}"}]
        )
        raw = resp.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"): raw = raw[4:]
        result = json.loads(raw.strip())
        log(f"Research: extracted {len(result.get('insights',[]))} insights")
        return result
    except Exception as e:
        log(f"Insight extraction error: {e}", "ERROR")
        return {}



def fetch_reddit_mentions(symbols: list) -> dict:
    """
    Now uses StockTwits instead of Reddit (Reddit blocks GitHub Actions IPs).
    Returns same dict format for compatibility with existing signal scoring.
    """
    st = fetch_stocktwits_sentiment(symbols[:15])
    mentions = {}
    for sym in symbols:
        if sym in st:
            v = st[sym]
            mentions[sym] = {
                "mentions": v["message_count"],
                "bullish":  int(v["bullish_pct"] / 10),
                "bearish":  int(v["bearish_pct"] / 10),
                "trending": v["trending"],
            }
        else:
            mentions[sym] = {"mentions": 0, "bullish": 0, "bearish": 0, "trending": False}
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
    log(f"Fetching price data for {len(get_watchlist())} tickers...")
    for symbol in get_watchlist():
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
            # Store macro triggers for AI context and alerts
            if articles_hs := score_headlines(headlines, symbol):
                t.macro_triggers = articles_hs.get("macro_triggers", [])
                t.macro_alert    = articles_hs.get("macro_alert", False)
                if t.macro_alert:
                    log(f"  🚨 MACRO ALERT on {symbol}: "
                        f"{', '.join(m['trigger'] for m in t.macro_triggers if m['impact']=='high')[:3]}")
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

    # StockTwits sentiment (replaces Reddit — no IP blocking)
    log("  Scanning StockTwits sentiment...")
    try:
        macro.reddit_mentions = fetch_reddit_mentions(get_watchlist())
    except Exception as e:
        log(f"  StockTwits error: {e}", "WARN")

    # Fear & Greed Index — market-wide mood
    try:
        macro.fear_greed = fetch_fear_greed()
        fg = macro.fear_greed
        # Adjust risk score based on extreme sentiment
        if fg["score"] >= 80:
            macro.risk_score = max(macro.risk_score - 1, -3)  # Extreme greed = caution
            log(f"  ⚠️  Extreme greed ({fg['score']:.0f}) — reducing risk score")
        elif fg["score"] <= 20:
            macro.risk_score = min(macro.risk_score + 1, 3)   # Extreme fear = opportunity
            log(f"  ⚠️  Extreme fear ({fg['score']:.0f}) — potential opportunity")
    except Exception as e:
        log(f"  Fear & Greed error: {e}", "WARN")

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

def get_1h_confirmation(symbol: str, direction: str) -> tuple[bool, str]:
    """
    Multi-timeframe confirmation — checks 1H chart agrees with daily signal.
    Returns (confirmed: bool, reason: str).
    Requires 1H RSI and trend to align with daily direction.
    """
    try:
        ticker = yf.Ticker(symbol)
        hist_1h = ticker.history(period="5d", interval="1h")
        if hist_1h.empty or len(hist_1h) < 20:
            return True, "1H data unavailable — skipping MTF check"

        closes_1h  = hist_1h["Close"].values
        price_1h   = float(closes_1h[-1])
        sma20_1h   = float(np.mean(closes_1h[-20:]))
        sma8_1h    = float(np.mean(closes_1h[-8:]))
        rsi_1h     = calc_rsi(closes_1h)

        if direction == "long":
            trend_ok = price_1h > sma8_1h > sma20_1h or price_1h > sma20_1h
            rsi_ok   = 45 <= rsi_1h <= 75
            if not trend_ok:
                return False, f"1H trend bearish (price ${price_1h:.2f} vs SMA20 ${sma20_1h:.2f})"
            if not rsi_ok:
                return False, f"1H RSI out of range ({rsi_1h:.1f})"
            return True, f"1H confirmed: RSI {rsi_1h:.1f}, above SMA20"
        else:
            trend_ok = price_1h < sma8_1h or price_1h < sma20_1h
            rsi_ok   = rsi_1h <= 60 or rsi_1h >= 70
            if not trend_ok:
                return False, f"1H trend bullish — short not confirmed"
            return True, f"1H short confirmed: RSI {rsi_1h:.1f}"

    except Exception as e:
        return True, f"1H check error ({e}) — skipping"


def is_good_trading_time() -> tuple[bool, str]:
    """
    Time-of-day filter — blocks signals during high-noise periods.
    Best signals develop 10:00 AM - 3:30 PM ET.
    First 30 min: algos settling, wide spreads, fake breakouts.
    Last 30 min: EOD positioning, forced closes, erratic moves.
    """
    now = datetime.now(ET)
    h, m = now.hour, now.minute
    total_mins = h * 60 + m

    open_mins  = 9 * 60 + 30   # 9:30 AM
    buffer_end = 10 * 60 + 0   # 10:00 AM (30 min buffer after open)
    eod_start  = 15 * 60 + 30  # 3:30 PM (30 min before close)
    close_mins = 16 * 60 + 0   # 4:00 PM

    if total_mins < open_mins:
        return False, "Pre-market — not scanning for new entries"
    if open_mins <= total_mins < buffer_end:
        return False, f"Opening 30 min buffer — waiting until 10:00 AM (now {now.strftime('%H:%M')} ET)"
    if eod_start <= total_mins < close_mins:
        return False, f"EOD 30 min window — no new entries after 3:30 PM (now {now.strftime('%H:%M')} ET)"
    if total_mins >= close_mins:
        return False, "Market closed"

    return True, f"Good trading window ({now.strftime('%H:%M')} ET)"


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
                 40  if m.market_regime == "ranging" else
                 40  if m.market_regime == "volatile" else 15)
    if m.vix_regime == "fear":      mac_score -= 35
    elif m.vix_regime == "elevated": mac_score -= 20
    if m.yield_curve < -0.5:         mac_score -= 10
    if mac_score < 30: return None
    # Exclude ranging — auto-adjust confirmed ranging has lowest WR
    if m.market_regime == "ranging": return None
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

    # ── Multi-timeframe confirmation ──────────────────────────────────────────
    mtf_ok, mtf_reason = get_1h_confirmation(symbol, "long")
    if not mtf_ok:
        log(f"  {symbol} LONG rejected: {mtf_reason}")
        return None

    atr    = t.atr_14 or t.price * 0.015
    stop   = round(t.price - RISK["stop_loss_atr_mult"]   * atr, 2)
    target = round(t.price + RISK["take_profit_atr_mult"] * atr, 2)
    rr     = round((target - t.price) / (t.price - stop), 2) if t.price > stop else 0
    if rr < 1.5: return None
    notes = [f"Trend↑ {pct_above_50:.1%} above SMA50",
             f"RSI={t.rsi_14:.1f} Vol={t.vol_ratio:.1f}x ATR={t.atr_pct:.2%}",
             mtf_reason]
    if t.headline_score > 20: notes.append(f"Headlines bullish ({t.headline_score:+.0f})")
    reddit_mentions = reddit.get("mentions", 0)
    if reddit.get("trending"): notes.append(f"StockTwits trending ({reddit_mentions} msgs)")
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

    # ── Regime gate — shorts only when macro supports them ─────────────────
    if m.market_regime in ("trending_up", "ranging"):
        return None
    if m.market_regime == "unknown":
        return None
    if macro.risk_score > -1:
        return None
    if m.vix_regime == "low":
        return None

    if macro.risk_score >= 2: return None
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

    # ── Multi-timeframe confirmation ──────────────────────────────────────────
    mtf_ok, mtf_reason = get_1h_confirmation(symbol, "short")
    if not mtf_ok:
        log(f"  {symbol} SHORT rejected: {mtf_reason}")
        return None

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
        "notes": [f"Short RSI={t.rsi_14:.1f} Vol={t.vol_ratio:.1f}x ATR={t.atr_pct:.2%}",
                  mtf_reason],
    }

def scan_all() -> list:
    session = get_market_session()

    # ── Time-of-day filter ────────────────────────────────────────────────
    tod_ok, tod_reason = is_good_trading_time()
    if not tod_ok:
        log(f"Scan skipped: {tod_reason}")
        return []

    log(f"Scanning {len(get_watchlist())} tickers | {session} | Regime:{macro.market_regime} | VIX:{macro.vix:.1f}")
    signals = []
    for sym in get_watchlist():
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
    memory   = build_pattern_memory()
    context  = build_context(sig["symbol"])
    research = load_research_digest()
    s        = sig["scores"]
    prompt   = (
        f"PATTERN MEMORY:\n{memory}\n\n"
        + (f"COMMUNITY RESEARCH:\n{research}\n\n" if research else "")
        + f"MARKET CONTEXT:\n{context}\n\n"
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
    day_pnl  = sum(t.get("pnl", 0) for t in today_closed)
    loss_pct = day_pnl / equity if equity else 0
    if loss_pct <= -RISK["max_daily_loss_pct"]:
        log(f"KILL SWITCH: {loss_pct:.2%} daily loss (${day_pnl:.2f})", "WARN")
        return True
    return False
# Runs on every 10-min cycle but does 4 critical jobs:
# 1. Trailing stops   — moves stop up as price rises to lock in profits
# 2. News emergency   — closes position if major negative headline hits
# 3. EOD close        — closes all positions before 3:55 PM ET
# 4. Sync from Alpaca — reads actual Alpaca positions as source of truth
# ══════════════════════════════════════════════════════════════════════════════

def close_position_market(symbol: str, qty: int, side: str, reason: str) -> bool:
    """Send a market close order to Alpaca."""
    try:
        r = requests.post(
            f"{ALPACA_BASE}/v2/orders",
            headers=ALPACA_HEADERS,
            json={
                "symbol":        symbol,
                "qty":           str(abs(qty)),
                "side":          side,
                "type":          "market",
                "time_in_force": "day",
            },
            timeout=10
        )
        if r.status_code in (200, 201):
            log(f"  ✅ CLOSED {symbol} ×{qty} — {reason}")
            return True
        else:
            log(f"  ❌ Close failed {symbol}: {r.status_code} {r.text[:100]}", "ERROR")
    except Exception as e:
        log(f"  ❌ Close error {symbol}: {e}", "ERROR")
    return False


def update_stop_loss(symbol: str, order_id: str, new_stop: float) -> bool:
    """Update the stop loss on an existing Alpaca bracket order."""
    try:
        # Cancel existing order and replace with updated stop
        r = requests.patch(
            f"{ALPACA_BASE}/v2/orders/{order_id}",
            headers=ALPACA_HEADERS,
            json={"stop_price": str(round(new_stop, 2))},
            timeout=10
        )
        if r.status_code in (200, 201):
            log(f"  ✅ Stop updated: {symbol} → ${new_stop:.2f}")
            return True
    except Exception as e:
        log(f"  Stop update error {symbol}: {e}", "WARN")
    return False


def sync_positions_from_alpaca():
    """
    Pull live position data from Alpaca and update our local trades file.
    Uses Alpaca as the source of truth — catches anything we missed.
    Also detects positions that were closed by Alpaca (stop/target hit) 
    and updates our records accordingly.
    """
    log("Syncing positions from Alpaca...")
    alpaca_positions = get_open_positions()
    alpaca_syms      = {p.get("symbol") for p in alpaca_positions}

    trades = load_trades()
    updated = False

    for trade in trades:
        if trade.get("status") != "open":
            continue
        sym = trade["symbol"]

        if sym not in alpaca_syms:
            # Position closed by Alpaca (stop or target hit) — update our record
            trade["status"]     = "closed"
            trade["closed_at"]  = datetime.now(ET).isoformat()
            trade["close_reason"] = "ALPACA_CLOSED (stop or target hit)"

            # Try to get fill price from recent orders
            try:
                r = requests.get(
                    f"{ALPACA_BASE}/v2/orders",
                    headers=ALPACA_HEADERS,
                    params={"symbols": sym, "status": "closed", "limit": 5},
                    timeout=8
                )
                if r.ok:
                    orders = r.json()
                    for o in orders:
                        if o.get("filled_avg_price"):
                            close_price = float(o["filled_avg_price"])
                            entry_price = trade.get("entry_price", close_price)
                            direction   = trade.get("direction", "long")
                            pnl_pct = ((close_price - entry_price) / entry_price * 100
                                      if direction == "long"
                                      else (entry_price - close_price) / entry_price * 100)
                            trade["closed_price"] = close_price
                            trade["pnl_pct"]      = round(pnl_pct, 2)
                            trade["pnl"]          = round(
                                pnl_pct / 100 * entry_price * trade.get("shares", 1), 2)
                            break
            except: pass

            log(f"  Synced: {sym} closed by Alpaca "
                f"P&L: {trade.get('pnl_pct', 0):+.1f}%")
            log_trade_outcome(trade)
            alert_trade_close(
                sym, trade.get("direction", "long"),
                trade.get("entry_price", 0),
                trade.get("closed_price", 0),
                trade.get("shares", 0),
                trade.get("close_reason", "")
            )
            updated = True

    # Update unrealized P&L for still-open positions
    for pos in alpaca_positions:
        sym = pos.get("symbol")
        unreal = float(pos.get("unrealized_pl", 0))
        unreal_pct = float(pos.get("unrealized_plpc", 0)) * 100
        for trade in trades:
            if trade.get("symbol") == sym and trade.get("status") == "open":
                trade["unrealized_pl"]  = round(unreal, 2)
                trade["unrealized_pct"] = round(unreal_pct, 2)
                trade["current_price"]  = float(pos.get("current_price", 0))
                updated = True

    if updated:
        save_trades(trades)
        log("Position sync complete")
    else:
        log("Position sync: no changes")

    return alpaca_positions


def apply_trailing_stops():
    """
    Move stop losses up as positions profit — locks in gains automatically.
    
    Rules:
    - Once up 1%:  move stop to breakeven (entry price)
    - Once up 2%:  trail stop to 0.5% below current price  
    - Once up 4%:  trail stop to 1% below current price
    - Once up 7%:  trail stop to 2% below current price
    
    For short positions, mirror logic applies (stop moves DOWN as price falls).
    """
    trades = load_trades()
    updated = False

    for trade in trades:
        if trade.get("status") != "open":
            continue

        sym         = trade["symbol"]
        entry       = trade.get("entry_price", 0)
        current     = trade.get("current_price", 0) or entry
        direction   = trade.get("direction", "long")
        current_stop = trade.get("stop_loss", 0)
        order_id    = trade.get("order_id", "")

        if not entry or not current:
            continue

        # Calculate profit percentage
        if direction == "long":
            profit_pct = (current - entry) / entry * 100
        else:
            profit_pct = (entry - current) / entry * 100

        # Determine new stop based on profit level
        new_stop = current_stop
        reason   = ""

        if direction == "long":
            if profit_pct >= 7.0:
                new_stop = round(current * 0.98, 2)   # 2% trail
                reason   = f"trailing 2% below (up {profit_pct:.1f}%)"
            elif profit_pct >= 4.0:
                new_stop = round(current * 0.99, 2)   # 1% trail
                reason   = f"trailing 1% below (up {profit_pct:.1f}%)"
            elif profit_pct >= 2.0:
                new_stop = round(current * 0.995, 2)  # 0.5% trail
                reason   = f"trailing 0.5% below (up {profit_pct:.1f}%)"
            elif profit_pct >= 1.0:
                new_stop = round(entry * 1.001, 2)    # Breakeven + 0.1%
                reason   = f"moved to breakeven (up {profit_pct:.1f}%)"
        else:  # short
            if profit_pct >= 7.0:
                new_stop = round(current * 1.02, 2)
                reason   = f"trailing 2% above (down {profit_pct:.1f}%)"
            elif profit_pct >= 4.0:
                new_stop = round(current * 1.01, 2)
                reason   = f"trailing 1% above (down {profit_pct:.1f}%)"
            elif profit_pct >= 2.0:
                new_stop = round(current * 1.005, 2)
                reason   = f"trailing 0.5% above (down {profit_pct:.1f}%)"
            elif profit_pct >= 1.0:
                new_stop = round(entry * 0.999, 2)
                reason   = f"moved to breakeven (down {profit_pct:.1f}%)"

        # Only update if stop improved
        improved = (direction == "long"  and new_stop > current_stop) or \
                   (direction == "short" and new_stop < current_stop)

        if improved and reason:
            log(f"  Trailing stop: {sym} {direction} "
                f"${current_stop:.2f} → ${new_stop:.2f} | {reason}")
            trade["stop_loss"]     = new_stop
            trade["trailing_stop"] = True
            trade["trail_reason"]  = reason

            # Update in Alpaca
            if order_id:
                update_stop_loss(sym, order_id, new_stop)

            lock_msg = "Lock in profit — can't lose now!" if profit_pct >= 1.0 else ""
            _tg(
                f"📌 *Trailing Stop Updated — {sym}*\n"
                f"Stop: ${current_stop:.2f} → *${new_stop:.2f}*\n"
                f"Current: ${current:.2f} | {reason}\n"
                f"{lock_msg}"
            )
            updated = True

    if updated:
        save_trades(trades)


def check_news_emergency_exit():
    """
    Check open positions for major negative headlines that warrant immediate exit.
    Runs on every cycle — catches news-driven drops before the stop is hit.
    
    Triggers on:
    - Headline score < -50 (severe negative news)
    - Specific emergency keywords regardless of score
    """
    EMERGENCY_KEYWORDS = [
        "sec charges", "fraud charges", "going concern", "chapter 11",
        "bankruptcy filing", "emergency shutdown", "trading halted",
        "fda rejection", "clinical trial failed", "ceo arrested",
        "accounting fraud", "restatement", "delisted",
    ]

    trades = load_trades()
    for trade in trades:
        if trade.get("status") != "open":
            continue

        sym       = trade["symbol"]
        direction = trade.get("direction", "long")
        t         = tickers.get(sym)

        if not t:
            continue

        # Only emergency exit long positions on negative news
        # (shorts benefit from negative news)
        if direction == "short":
            continue

        # Check headline score
        emergency = False
        trigger   = ""

        if t.headline_score < -50:
            emergency = True
            trigger   = f"Headline score {t.headline_score:.0f} (severe negative)"

        if not emergency:
            for h in t.headlines:
                h_low = h.lower()
                for kw in EMERGENCY_KEYWORDS:
                    if kw in h_low:
                        emergency = True
                        trigger   = f"Emergency keyword: '{kw}' in headline"
                        break
                if emergency:
                    break

        if emergency:
            qty  = trade.get("shares", 1)
            side = "sell" if direction == "long" else "buy"
            log(f"  🚨 EMERGENCY EXIT: {sym} | {trigger}", "WARN")

            success = close_position_market(sym, qty, side,
                                            f"Emergency exit: {trigger}")
            if success:
                trade["status"]       = "closed"
                trade["close_reason"] = f"EMERGENCY: {trigger}"
                trade["closed_at"]    = datetime.now(ET).isoformat()
                save_trades(trades)
                log_trade_outcome(trade)

                _tg(
                    f"🚨 *EMERGENCY EXIT — {sym}*\n"
                    f"Reason: {trigger}\n"
                    f"Position closed at market to limit damage.\n"
                    f"Headlines: {t.headlines[0][:100] if t.headlines else 'N/A'}"
                )


def eod_close_all():
    """
    Close all open positions before market close (3:55 PM ET).
    Avoids overnight gap risk — positions that haven't hit stop/target
    get closed at market price with whatever P&L is on the table.
    
    Only runs if EOD_CLOSE env var is set to 'true' OR
    if it's between 3:50-4:00 PM ET.
    """
    now         = datetime.now(ET)
    eod_enabled = os.environ.get("EOD_CLOSE", "true").lower() == "true"

    # Only run in the EOD window
    is_eod_window = (now.hour == 15 and now.minute >= 50) or \
                    (now.hour == 16 and now.minute == 0)

    if not is_eod_window or not eod_enabled:
        return

    positions = get_open_positions()
    if not positions:
        log("EOD: No open positions to close")
        return

    log(f"EOD CLOSE: Closing {len(positions)} position(s) before market close...")
    _tg(
        f"🔔 *EOD Close — {now.strftime('%H:%M ET')}*\n"
        f"Closing {len(positions)} open position(s) before market close.\n"
        f"Avoiding overnight risk."
    )

    closed = 0
    for pos in positions:
        sym  = pos.get("symbol")
        qty  = int(float(pos.get("qty", 1)))
        side_held = pos.get("side", "long")
        close_side = "sell" if side_held == "long" else "buy"
        unreal_pct = float(pos.get("unrealized_plpc", 0)) * 100
        unreal_pl  = float(pos.get("unrealized_pl", 0))

        success = close_position_market(
            sym, qty, close_side, f"EOD close (P&L: {unreal_pct:+.1f}%)"
        )
        if success:
            closed += 1
            _tg(
                f"{'✅' if unreal_pl > 0 else '🔴'} *EOD Closed: {sym}*\n"
                f"P&L: {unreal_pct:+.1f}% (${unreal_pl:+.2f})\n"
            )
            # Update trade record
            trades = load_trades()
            for trade in trades:
                if trade.get("symbol") == sym and trade.get("status") == "open":
                    trade["status"]       = "closed"
                    trade["close_reason"] = "EOD_CLOSE"
                    trade["closed_at"]    = datetime.now(ET).isoformat()
                    trade["closed_price"] = float(pos.get("current_price", 0))
                    trade["pnl_pct"]      = round(unreal_pct, 2)
                    trade["pnl"]          = round(unreal_pl, 2)
                    log_trade_outcome(trade)
            save_trades(trades)

    log(f"EOD close complete: {closed}/{len(positions)} closed")


def check_time_based_exits():
    """
    Close positions that have been open too long without moving.
    Rules:
    - If open > 4 hours and unrealized P&L between -0.5% and +0.5% → dead money, close it
    - If open > 8 hours regardless of P&L → force close before EOD
    - If open > 2 hours and down more than 1.5% but above stop → tighten stop to current - 0.5%
    """
    trades = load_trades()
    now    = datetime.now(ET)
    updated = False

    for trade in trades:
        if trade.get("status") != "open":
            continue

        opened_at = trade.get("opened_at", "")
        if not opened_at:
            continue

        try:
            open_time = datetime.fromisoformat(opened_at).astimezone(ET)
        except:
            continue

        hours_open  = (now - open_time).total_seconds() / 3600
        unreal_pct  = trade.get("unrealized_pct", 0)
        sym         = trade["symbol"]
        direction   = trade.get("direction", "long")
        qty         = trade.get("shares", 1)
        close_side  = "sell" if direction == "long" else "buy"

        # Rule 1: Dead money — open > dead_money_hours, barely moved
        if hours_open >= RISK.get("dead_money_hours", 2) and -0.5 <= unreal_pct <= 0.5:
            log(f"  Time exit (dead money): {sym} open {hours_open:.1f}h, "
                f"P&L {unreal_pct:+.1f}% — closing", "WARN")
            success = close_position_market(sym, qty, close_side,
                                            f"Dead money exit after {hours_open:.1f}h")
            if success:
                trade["status"]       = "closed"
                trade["close_reason"] = f"TIME_EXIT_DEAD ({hours_open:.1f}h)"
                trade["closed_at"]    = now.isoformat()
                log_trade_outcome(trade)
                _tg(
                    f"⏰ *Time Exit — {sym}*\n"
                    f"Open {hours_open:.1f}h with no movement ({unreal_pct:+.1f}%)\n"
                    f"Freed slot for better opportunity."
                )
                updated = True

        # Rule 2: Force close if open > max_hold_hours
        elif hours_open >= RISK.get("max_hold_hours", 4):
            log(f"  Time exit (8h max): {sym} open {hours_open:.1f}h — force closing", "WARN")
            success = close_position_market(sym, qty, close_side,
                                            f"8-hour max hold exceeded")
            if success:
                trade["status"]       = "closed"
                trade["close_reason"] = f"TIME_EXIT_8H ({unreal_pct:+.1f}%)"
                trade["closed_at"]    = now.isoformat()
                log_trade_outcome(trade)
                _tg(
                    f"⏰ *8-Hour Exit — {sym}*\n"
                    f"P&L: {unreal_pct:+.1f}% | Open {hours_open:.1f}h\n"
                    f"Maximum hold time reached."
                )
                updated = True

        # Rule 3: Tighten stop if down > 1.5% after 2 hours (weak setup)
        elif hours_open >= 2 and unreal_pct < -1.5:
            current_stop = trade.get("stop_loss", 0)
            current_price = trade.get("current_price", 0)
            if current_price and current_stop:
                if direction == "long":
                    new_stop = max(current_stop, round(current_price * 0.995, 2))
                else:
                    new_stop = min(current_stop, round(current_price * 1.005, 2))
                improved = (direction == "long"  and new_stop > current_stop) or \
                           (direction == "short" and new_stop < current_stop)
                if improved:
                    log(f"  Tightening stop: {sym} down {unreal_pct:+.1f}% "
                        f"after {hours_open:.1f}h → ${new_stop:.2f}")
                    trade["stop_loss"]  = new_stop
                    trade["trail_reason"] = f"Tightened after {hours_open:.1f}h weak"
                    order_id = trade.get("order_id","")
                    if order_id:
                        update_stop_loss(sym, order_id, new_stop)
                    updated = True

    if updated:
        save_trades(trades)


def run_position_monitor():
    """
    Master position monitor — runs every cycle.
    Calls all monitoring functions in the right order.
    """
    log("\n── Position Monitor ──────────────────────────────────────")

    # 1. Sync from Alpaca (source of truth)
    alpaca_positions = sync_positions_from_alpaca()

    open_count = len(alpaca_positions)
    if open_count == 0:
        log("No open positions — monitor complete")
        return

    log(f"Monitoring {open_count} open position(s)")

    # 2. Refresh prices for open positions using Alpaca real-time quotes
    open_syms = [p.get("symbol") for p in alpaca_positions]
    if open_syms:
        try:
            # Use Alpaca latest quotes endpoint — real-time, not delayed
            syms_str = ",".join(open_syms)
            r = requests.get(
                f"{ALPACA_DATA}/v2/stocks/quotes/latest",
                headers={"APCA-API-KEY-ID": ALPACA_KEY,
                         "APCA-API-SECRET-KEY": ALPACA_SECRET},
                params={"symbols": syms_str, "feed": "iex"},
                timeout=8
            )
            if r.ok:
                quotes = r.json().get("quotes", {})
                for sym, q in quotes.items():
                    if sym in tickers:
                        # Use ask price as proxy for current price (most current)
                        ask = float(q.get("ap", 0))
                        bid = float(q.get("bp", 0))
                        if ask > 0 and bid > 0:
                            tickers[sym].price = round((ask + bid) / 2, 2)
                        log(f"  Real-time quote {sym}: ${tickers[sym].price:.2f}")
            else:
                # Fallback to Alpaca snapshot
                r2 = requests.get(
                    f"{ALPACA_DATA}/v2/stocks/snapshots",
                    headers={"APCA-API-KEY-ID": ALPACA_KEY,
                             "APCA-API-SECRET-KEY": ALPACA_SECRET},
                    params={"symbols": syms_str, "feed": "iex"},
                    timeout=8
                )
                if r2.ok:
                    snaps = r2.ok and r2.json()
                    for sym, snap in snaps.items():
                        if sym in tickers:
                            minute_bar = snap.get("minuteBar", {})
                            price = float(minute_bar.get("c", 0))
                            if price > 0:
                                tickers[sym].price = price
        except Exception as e:
            log(f"  Real-time price refresh error: {e} — falling back to yfinance", "WARN")
            for sym in open_syms:
                try:
                    data = yf.Ticker(sym).history(period="1d")
                    if not data.empty and sym in tickers:
                        tickers[sym].price = float(data["Close"].iloc[-1])
                except: pass

        # Quick headline refresh for open positions (last 2 hours)
        for sym in open_syms:
            try:
                since = (datetime.now(ET) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
                r = requests.get(
                    f"{ALPACA_DATA}/v1beta1/news",
                    headers={"APCA-API-KEY-ID": ALPACA_KEY,
                             "APCA-API-SECRET-KEY": ALPACA_SECRET},
                    params={"symbols": sym, "start": since, "limit": 5},
                    timeout=6
                )
                if r.ok and sym in tickers:
                    articles = r.json().get("news", [])
                    headlines = [a["headline"] for a in articles[:5]]
                    tickers[sym].headlines     = headlines
                    tickers[sym].headline_score = score_headlines(headlines, sym)["score"]
            except: pass

    # 3. Apply trailing stops
    apply_trailing_stops()

    # 4. Time-based exits — dead money, 8h max, tighten weak positions
    check_time_based_exits()

    # 5. Check for news emergencies
    check_news_emergency_exit()

    # 6. EOD close check
    eod_close_all()

    log("── Monitor complete ───────────────────────────────────────\n")


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

    # ── Load active watchlist ──────────────────────────────────────────────
    load_active_watchlist()

    # Always fetch data first
    fetch_macro()
    fetch_price_data()

    # ── Update dynamic watchlist at market open (9:30-10:00 AM) ──────────
    if session in ("open", "pre_market") and now.hour == 9 and now.minute <= 45:
        log("Market open — running dynamic watchlist update...")
        update_dynamic_watchlist()

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
        # Also run recent regime comparison
        run_recent_regime_backtest(days=60)
        if now.weekday() == 6:
            run_research_digest()
        return

    if mode == "recent_regime":
        days = int(os.environ.get("BACKTEST_DAYS", "60"))
        run_recent_regime_backtest(days=days)
        commit_state_to_github()
        return

    # ── Auto-adjust only ───────────────────────────────────────────────────
    if mode == "auto_adjust":
        bt_file = STATE_DIR / "backtest_latest.json"
        bt = json.loads(bt_file.read_text()) if bt_file.exists() else {}
        run_auto_adjust(backtest=bt)
        return

    if mode == "research":
        run_research_digest()
        commit_state_to_github()
        return

    # ── Post-close review ──────────────────────────────────────────────────
    if mode == "review" or (session == "closed" and now.hour == 16):
        generate_daily_review()
        commit_state_to_github()
        # Friday — weekly auto-adjust + research digest
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
        # Commit state so dashboard gets updated
        log("Committing state to GitHub for dashboard...")
        commit_state_to_github()
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

    # ── Macro alert broadcast ─────────────────────────────────────────────
    # If high-impact triggers detected on any ticker, alert immediately
    macro_alerts = [(sym, t) for sym, t in tickers.items() if t.macro_alert]
    if macro_alerts:
        alert_lines = []
        for sym, t in macro_alerts[:5]:
            high = [m for m in t.macro_triggers if m["impact"] == "high"]
            for m in high[:2]:
                alert_lines.append(
                    f"*{m['trigger'].upper()}* on {sym}\n"
                    f"_{m['note']}_\n"
                    f"📰 {m['headline'][:80]}"
                )
        if alert_lines:
            _tg(
                f"🚨 *MACRO TRIGGER ALERT*\n\n"
                + "\n\n".join(alert_lines) +
                f"\n\nVIX: {macro.vix:.1f} | Regime: {macro.market_regime}\n"
                f"Review positions and signals carefully."
            )

    # ── Position monitor — always runs first ──────────────────────────────
    # Syncs from Alpaca, applies trailing stops, checks news, handles EOD close
    run_position_monitor()

    open_positions = get_open_positions()
    log(f"Open positions: {len(open_positions)}/{RISK['max_open_positions']}")
    if len(open_positions) >= RISK["max_open_positions"]:
        log("Max positions reached — not scanning for new entries")
        commit_state_to_github()
        return

    signals = scan_all()
    if not signals:
        log("No signals this run — exiting")
        commit_state_to_github()
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

    # Commit state to GitHub so Streamlit dashboard stays current
    commit_state_to_github()


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM ALERTS
# ══════════════════════════════════════════════════════════════════════════════

def _tg(text: str):
    """Send a Telegram message. Escapes problematic characters to avoid parse errors."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram: skipped — token or chat_id missing", "WARN")
        return
    try:
        # Try with Markdown first
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": text,
                  "parse_mode": "Markdown"},
            timeout=8
        )
        if resp.status_code == 400 and "parse" in resp.text.lower():
            # Markdown failed — strip formatting and send as plain text
            clean = text.replace("*","").replace("_","").replace("`","").replace("[","").replace("]","")
            resp = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": clean},
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
    # Macro trigger alert
    macro_str = ""
    t = tickers.get(sig["symbol"])
    if t and hasattr(t, "macro_triggers") and t.macro_triggers:
        high = [m for m in t.macro_triggers if m["impact"] == "high"]
        if high:
            macro_str = f"\n🚨 MACRO: {', '.join(m['trigger'].upper() for m in high[:3])}"
    _tg(
        f"*{d} SIGNAL — {sig['symbol']}*\n"
        f"Entry: ${sig['entry']:.2f}  Stop: ${sig['stop']:.2f}  Target: ${sig['target']:.2f}\n"
        f"R/R: {sig['rr']:.2f}  Criteria: {sig['criteria']:.0f}/100"
        f"{hl_str}{reddit_str}{macro_str}{ai}\n"
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
    wins  = stats.get("wins", 0)
    total = stats.get("total", 0)
    pnl   = stats.get("pnl", 0.0)
    wr    = wins / total * 100 if total else 0
    dash  = f"\n🔗 [Dashboard]({DASHBOARD_URL})" if DASHBOARD_URL else ""
    _tg(
        f"📊 *Daily Summary — {date.today().isoformat()}*\n"
        f"Trades: {total}  |  W:{wins} L:{total-wins}  |  WR: {wr:.0f}%\n"
        f"P&L: ${pnl:+.2f}"
        f"{dash}\n\n"
        f"{review[:500]}"
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
    fg   = macro.fear_greed
    fg_str = f"F&G: {fg['score']:.0f} ({fg['rating'].replace('_',' ').title()})"
    dash = f"\n🔗 [Dashboard]({DASHBOARD_URL})" if DASHBOARD_URL else ""
    _tg(
        f"🤖 *Boticus started [{mode}]*\n"
        f"Session: {get_market_session()}  |  "
        f"VIX: {macro.vix:.1f}  |  Regime: {macro.market_regime}\n"
        f"{fg_str}  |  Watchlist: {len(get_watchlist())} tickers"
        f"{dash}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# BACKTESTING MODULE
# Runs criteria against historical data to validate signal quality
# ══════════════════════════════════════════════════════════════════════════════

def run_recent_regime_backtest(days: int = 60) -> dict:
    """
    Recent-regime backtest — only uses last N days of data.
    Compares win rate in THIS market vs the full 180-day historical average.
    Critical for 2025-2026 which has unique characteristics:
    - Trump tariff headlines moving markets 1-3% instantly
    - AI narrative decoupling tech from broader market
    - Faster VIX spikes and recoveries than historical norms
    - Fed stuck between inflation and growth — high uncertainty
    """
    log(f"Running RECENT REGIME backtest: last {days} days only...")

    # Run two backtests — recent and historical
    recent  = run_backtest(lookback_days=days, notify=False)
    full    = run_backtest(lookback_days=180, notify=False)

    if not recent or not full:
        log("Recent regime backtest: insufficient data", "WARN")
        return {}

    # Compare
    wr_recent  = recent.get("win_rate", 0)
    wr_full    = full.get("win_rate", 0)
    wr_delta   = wr_recent - wr_full

    exp_recent = recent.get("expectancy_pct", 0)
    exp_full   = full.get("expectancy_pct", 0)

    long_recent = recent.get("long_win_rate", 0)
    long_full   = full.get("long_win_rate", 0)

    # Regime characterization
    if wr_recent > wr_full + 5:
        regime_verdict = "IMPROVING — current market conditions favor our strategy"
    elif wr_recent < wr_full - 5:
        regime_verdict = "DEGRADING — current market conditions are harder for our strategy"
    else:
        regime_verdict = "STABLE — current market similar to historical performance"

    result = {
        "days_analyzed":   days,
        "recent_win_rate": round(wr_recent, 1),
        "full_win_rate":   round(wr_full, 1),
        "win_rate_delta":  round(wr_delta, 1),
        "recent_expectancy": round(exp_recent, 2),
        "full_expectancy":   round(exp_full, 2),
        "recent_long_wr":  round(long_recent, 1),
        "full_long_wr":    round(long_full, 1),
        "verdict":         regime_verdict,
        "timestamp":       datetime.now(ET).isoformat(),
    }

    # Save
    regime_file = STATE_DIR / "regime_comparison.json"
    regime_file.write_text(json.dumps(result, indent=2))

    # Telegram alert
    delta_emoji = "📈" if wr_delta > 5 else "📉" if wr_delta < -5 else "➡️"
    _tg(
        f"🔬 *Recent Regime Analysis ({days}-day vs 180-day)*\n\n"
        f"*Recent ({days}d):* {wr_recent:.1f}% WR | {exp_recent:+.2f}% expectancy\n"
        f"*Historical (180d):* {wr_full:.1f}% WR | {exp_full:+.2f}% expectancy\n"
        f"{delta_emoji} *Delta: {wr_delta:+.1f}% WR*\n\n"
        f"*Verdict:* {regime_verdict}\n\n"
        f"Long WR: {long_recent:.1f}% recent vs {long_full:.1f}% historical"
    )

    log(f"Recent regime: {wr_recent:.1f}% WR vs {wr_full:.1f}% historical ({wr_delta:+.1f}%)")
    return result


def run_backtest(symbols: list = None, lookback_days: int = 180,
                 notify: bool = True) -> dict:
    """
    Backtest the long + short signal criteria against historical price data.
    For each day in the lookback window, simulates what signals would have fired
    and tracks hypothetical outcomes.

    Returns a dict with win rates, avg R/R, best/worst setups, and regime breakdown.
    """
    symbols = symbols or get_watchlist()
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
# STATE COMMIT — pushes bot_state/ data files back to GitHub repo after each run
# so Streamlit Cloud can read them as a live data source
# ══════════════════════════════════════════════════════════════════════════════

def commit_state_to_github():
    """
    Commits trades.json, feedback.json, backtest_latest.json, auto_adjust_latest.json
    back to the GitHub repo so Streamlit Cloud can read live data.
    """
    if not GITHUB_TOKEN or not GITHUB_REPO:
        log("State commit: GITHUB_TOKEN or GITHUB_REPOSITORY not set — skipping", "WARN")
        return

    import base64
    api_base = f"https://api.github.com/repos/{GITHUB_REPO}/contents"
    headers  = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    files_to_commit = [
        "trades.json",
        "feedback.json",
        "backtest_latest.json",
        "auto_adjust_latest.json",
        "bot.log",
        "watchlist.json",
        "research_digest.json",
    ]

    committed = 0
    for filename in files_to_commit:
        filepath = STATE_DIR / filename
        if not filepath.exists():
            log(f"  Skip {filename} — not found at {filepath}")
            continue
        try:
            content     = filepath.read_bytes()
            b64_content = base64.b64encode(content).decode()
            repo_path   = f"bot_state/{filename}"

            # Always fetch fresh SHA (never use cached — causes 409 conflicts)
            sha = None
            r = requests.get(f"{api_base}/{repo_path}", headers=headers, timeout=10)
            log(f"  SHA check {filename}: {r.status_code}")
            if r.status_code == 200:
                sha = r.json().get("sha")

            payload = {
                "message": f"bot: update {filename} [{datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}]",
                "content": b64_content,
                "branch":  "main",
            }
            if sha:
                payload["sha"] = sha

            r = requests.put(f"{api_base}/{repo_path}", headers=headers,
                             json=payload, timeout=15)
            log(f"  Commit {filename}: {r.status_code}")

            if r.status_code in (200, 201):
                committed += 1
                log(f"  ✅ Committed {filename} to repo")
            elif r.status_code == 409:
                # SHA conflict — fetch fresh SHA and retry once
                log(f"  SHA conflict on {filename} — retrying with fresh SHA", "WARN")
                r2 = requests.get(f"{api_base}/{repo_path}", headers=headers, timeout=10)
                if r2.status_code == 200:
                    payload["sha"] = r2.json().get("sha")
                    r3 = requests.put(f"{api_base}/{repo_path}", headers=headers,
                                      json=payload, timeout=15)
                    if r3.status_code in (200, 201):
                        committed += 1
                        log(f"  ✅ Committed {filename} (retry)")
                    else:
                        log(f"  ❌ Commit failed {filename} (retry): {r3.status_code}", "WARN")
            else:
                log(f"  ❌ Commit failed {filename}: {r.status_code} — {r.text[:150]}", "WARN")
        except Exception as e:
            log(f"  Commit error {filename}: {e}", "WARN")

    log(f"State commit complete: {committed}/{len(files_to_commit)} files")


# ══════════════════════════════════════════════════════════════════════════════
# STREAMLIT DASHBOARD — full connected version
# Reads data from GitHub repo (committed by bot after each run)
# Deploy free at share.streamlit.io — connect boticus repo, set main file to dashboard.py
# ══════════════════════════════════════════════════════════════════════════════

DASHBOARD_CODE = '''
import streamlit as st
import json
from pathlib import Path
from datetime import datetime, date
import pandas as pd

st.set_page_config(
    page_title="Boticus",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# ── Typography + Dark/Light mode aware CSS ────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
}

/* Container */
.block-container {
    padding: 1.2rem 1.5rem 3rem !important;
    max-width: 1400px !important;
}

/* Header */
h1 { font-size: 2rem !important; font-weight: 700 !important; letter-spacing: -0.5px; }
h2 { font-size: 1.3rem !important; font-weight: 600 !important; margin-top: 1.2rem !important; }
h3 { font-size: 1.1rem !important; font-weight: 500 !important; }

/* Tabs — bigger, cleaner */
.stTabs [data-baseweb="tab-list"] {
    gap: 4px;
    border-bottom: 1px solid rgba(128,128,128,0.2);
}
.stTabs [data-baseweb="tab"] {
    font-size: 14px !important;
    font-weight: 500 !important;
    padding: 10px 18px !important;
    border-radius: 8px 8px 0 0 !important;
    letter-spacing: 0.01em;
}

/* Metrics */
[data-testid="metric-container"] {
    background: rgba(128,128,128,0.07);
    border: 1px solid rgba(128,128,128,0.12);
    border-radius: 12px;
    padding: 16px 20px !important;
}
[data-testid="stMetricLabel"] {
    font-size: 12px !important;
    font-weight: 500 !important;
    letter-spacing: 0.05em;
    text-transform: uppercase;
    opacity: 0.65;
}
[data-testid="stMetricValue"] {
    font-size: 26px !important;
    font-weight: 700 !important;
    letter-spacing: -0.5px;
}
[data-testid="stMetricDelta"] {
    font-size: 12px !important;
    font-weight: 500 !important;
}

/* Cards */
.bcard {
    border-radius: 10px;
    padding: 12px 16px;
    margin: 6px 0;
    font-size: 14px;
    font-weight: 400;
    line-height: 1.5;
    border-left: 3px solid;
}
.bwin  { background: rgba(34,197,94,0.08);  border-color: #22c55e; }
.bloss { background: rgba(239,68,68,0.08);   border-color: #ef4444; }
.balert{ background: rgba(234,179,8,0.08);   border-color: #eab308; }
.binfo { background: rgba(59,130,246,0.08);  border-color: #3b82f6; }
.bmacro{ background: rgba(168,85,247,0.08);  border-color: #a855f7; }

/* Dataframes */
.stDataFrame {
    border-radius: 8px !important;
    overflow: hidden !important;
    font-size: 13px !important;
}

/* Expanders */
.streamlit-expanderHeader {
    font-size: 14px !important;
    font-weight: 500 !important;
}

/* Caption */
.stCaption {
    font-size: 12px !important;
    opacity: 0.5;
}

/* Code blocks */
.stCode {
    font-size: 12px !important;
    border-radius: 8px !important;
}

/* Divider */
hr { opacity: 0.15 !important; margin: 1.5rem 0 !important; }

/* Mobile */
@media (max-width: 768px) {
    .block-container { padding: 0.75rem !important; }
    [data-testid="stMetricValue"] { font-size: 22px !important; }
    h1 { font-size: 1.6rem !important; }
    .stTabs [data-baseweb="tab"] { font-size: 12px !important; padding: 8px 10px !important; }
}
</style>
""", unsafe_allow_html=True)

# Auto-refresh 5 min
st.markdown("<script>setTimeout(()=>window.location.reload(),300000)</script>", unsafe_allow_html=True)

DATA_DIR = Path("bot_state")

def load(f):
    p = DATA_DIR / f
    return json.loads(p.read_text()) if p.exists() else None

def bcard(text, kind="info"):
    return f'<div class="bcard b{kind}">{text}</div>'

# ── Load data ──────────────────────────────────────────────────────────────────
trades   = load("trades.json")   or []
feedback = load("feedback.json") or []
bt       = load("backtest_latest.json")
adj      = load("auto_adjust_latest.json")
wl_data  = load("watchlist.json")

open_t    = [t for t in trades   if t.get("status") == "open"]
today_str = date.today().isoformat()
wins      = [t for t in feedback if t.get("result") == "win"]
losses    = [t for t in feedback if t.get("result") == "loss"]
total_pnl = sum(t.get("pnl_dollar", 0) for t in feedback)
today_pnl = sum(t.get("pnl_dollar", 0) for t in feedback if t.get("date","") == today_str)
wr        = len(wins)/len(feedback)*100 if feedback else 0
avg_win   = sum(t.get("pnl_pct",0) for t in wins)  /len(wins)   if wins   else 0
avg_loss  = sum(t.get("pnl_pct",0) for t in losses)/len(losses) if losses else 0

# ── Header ─────────────────────────────────────────────────────────────────────
st.title("🤖 Boticus")
st.caption(f"Updated {datetime.now().strftime('%b %d %H:%M ET')}  ·  Auto-refreshes every 5 min")

r1a,r1b = st.columns(2)
r2a,r2b = st.columns(2)
r3a,r3b = st.columns(2)
r1a.metric("Open Positions",  len(open_t))
r1b.metric("Win Rate",        f"{wr:.1f}%", delta=f"{wr-50:.1f}% vs 50%")
r2a.metric("Total P&L",       f"${total_pnl:+,.0f}")
r2b.metric("Today P&L",       f"${today_pnl:+,.0f}")
r3a.metric("Avg Win",         f"{avg_win:+.1f}%")
r3b.metric("Avg Loss",        f"{avg_loss:+.1f}%")

st.divider()

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab1,tab2,tab3,tab4,tab5,tab6,tab7,tab8 = st.tabs([
    "📊 Positions", "📈 P&L", "🔬 Quality",
    "📉 Backtest",  "🔧 Adjust", "📋 Log",
    "🗂 Watchlist",  "📝 Changelog"
])

# ══ Tab 1: Positions ══════════════════════════════════════════════════════════
with tab1:
    if open_t:
        st.subheader(f"Open  ({len(open_t)})")
        for t in open_t:
            curr  = t.get("current_price", t.get("entry_price", 0))
            entry = t.get("entry_price", 0)
            unp   = t.get("unrealized_pct", 0)
            trail = "  📌" if t.get("trailing_stop") else ""
            sym   = t.get("symbol","")
            icon  = "🟢" if unp > 0 else "🔴" if unp < 0 else "⚪"
            with st.expander(f"{icon}  **{sym}**  {t.get('direction','').upper()}  {unp:+.1f}%{trail}"):
                ca,cb = st.columns(2)
                ca.metric("Entry",   f"${entry:.2f}")
                cb.metric("Current", f"${curr:.2f}")
                cc,cd = st.columns(2)
                cc.metric("Stop",    f"${t.get('stop_loss',0):.2f}")
                cd.metric("Target",  f"${t.get('take_profit',0):.2f}")
                ce,cf = st.columns(2)
                ce.metric("Shares",  t.get("shares",0))
                cf.metric("AI Score",f"{t.get('ai_score','—')}")
                st.caption(f"Opened: {t.get('opened_at','')[:16].replace('T',' ')}")
    else:
        st.markdown(bcard("No open positions right now — bot is scanning for entries.", "info"), unsafe_allow_html=True)

    st.divider()
    st.subheader("Today's Closed Trades")
    today_fb = [t for t in feedback if t.get("date","") == today_str]
    if today_fb:
        for t in today_fb:
            pnl = t.get("pnl_pct",0)
            kind = "win" if pnl > 0 else "loss"
            icon = "🟢" if pnl > 0 else "🔴"
            st.markdown(bcard(
                f"{icon} <b>{t['symbol']}</b> {t.get('direction','').upper()} &nbsp;|&nbsp; "
                f"{pnl:+.1f}% &nbsp;|&nbsp; ${t.get('pnl_dollar',0):+.0f} &nbsp;|&nbsp; "
                f"{t.get('close_reason','—')}",
                kind
            ), unsafe_allow_html=True)
    else:
        st.markdown(bcard("No completed trades today.", "info"), unsafe_allow_html=True)

# ══ Tab 2: P&L ════════════════════════════════════════════════════════════════
with tab2:
    if feedback:
        df = pd.DataFrame(feedback)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        df["cum_pnl"] = df["pnl_dollar"].cumsum()

        st.subheader("Cumulative P&L")
        st.line_chart(df.set_index("date")["cum_pnl"], use_container_width=True, height=240)

        st.subheader("Trade History")
        disp = df[["date","symbol","direction","pnl_pct","pnl_dollar","close_reason","regime"]].copy()
        disp = disp.sort_values("date", ascending=False)
        disp.columns = ["Date","Sym","Dir","P&L%","P&L$","Reason","Regime"]
        disp["P&L%"] = disp["P&L%"].map(lambda x: f"{x:+.1f}%")
        disp["P&L$"] = disp["P&L$"].map(lambda x: f"${x:+.0f}")
        disp["Date"] = disp["Date"].dt.strftime("%m/%d")
        st.dataframe(disp.head(50), use_container_width=True, hide_index=True)

        if "regime" in df.columns:
            st.subheader("Win Rate by Regime")
            rg = df.groupby("regime").agg(
                Trades=("pnl_dollar","count"),
                TotalPnL=("pnl_dollar","sum")
            ).reset_index()
            rg["Win Rate"] = df.groupby("regime").apply(
                lambda x: f"{sum(v>0 for v in x['pnl_dollar'])/len(x)*100:.0f}%"
            ).values
            rg["Total P&L"] = rg["TotalPnL"].map(lambda x: f"${x:+.0f}")
            rg = rg.drop(columns=["TotalPnL"])
            st.dataframe(rg, use_container_width=True, hide_index=True)
    else:
        st.markdown(bcard("No closed trades yet — keep the bot running.", "info"), unsafe_allow_html=True)

# ══ Tab 3: Signal Quality ═════════════════════════════════════════════════════
with tab3:
    if len(feedback) >= 5:
        df = pd.DataFrame(feedback)

        ca, cb = st.columns(2)
        with ca:
            st.subheader("Win Rate by AI Score")
            if "ai_score" in df.columns:
                df["ai_b"] = pd.cut(df["ai_score"].fillna(0),
                    bins=[0,60,70,80,100], labels=["55-60","60-70","70-80","80+"])
                ab = df.groupby("ai_b", observed=True).apply(
                    lambda x: round(sum(x["result"]=="win")/len(x)*100,0) if len(x)>0 else 0
                ).reset_index()
                ab.columns = ["AI Score","Win %"]
                st.dataframe(ab, use_container_width=True, hide_index=True)

            st.subheader("Win Rate by VIX")
            if "vix" in df.columns:
                df["vix_b"] = pd.cut(df["vix"].fillna(20),
                    bins=[0,15,25,35,100],
                    labels=["<15  low","15-25 normal","25-35 elevated",">35  fear"])
                vb = df.groupby("vix_b", observed=True).apply(
                    lambda x: round(sum(x["result"]=="win")/len(x)*100,0) if len(x)>0 else 0
                ).reset_index()
                vb.columns = ["VIX Range","Win %"]
                st.dataframe(vb, use_container_width=True, hide_index=True)

        with cb:
            st.subheader("Exit Breakdown")
            total_fb = len(feedback) or 1
            stops    = sum(1 for t in feedback if "STOP"      in t.get("close_reason",""))
            targets  = sum(1 for t in feedback if "TARGET"    in t.get("close_reason",""))
            eod      = sum(1 for t in feedback if "EOD"       in t.get("close_reason",""))
            emerg    = sum(1 for t in feedback if "EMERGENCY" in t.get("close_reason",""))
            trail    = sum(1 for t in feedback if "TRAIL"     in t.get("close_reason",""))
            tme      = sum(1 for t in feedback if "TIME"      in t.get("close_reason",""))
            alp      = sum(1 for t in feedback if "ALPACA"    in t.get("close_reason",""))
            ex = pd.DataFrame({
                "Exit":  ["🎯 Target","🛑 Stop","🔔 EOD","🚨 Emergency","📌 Trail","⏰ Time","🔁 Alpaca"],
                "Count": [targets,stops,eod,emerg,trail,tme,alp],
                "Pct":   [f"{x/total_fb*100:.0f}%" for x in [targets,stops,eod,emerg,trail,tme,alp]],
            })
            st.dataframe(ex, use_container_width=True, hide_index=True)

            st.subheader("Long vs Short")
            long_t  = [t for t in feedback if t.get("direction")=="long"]
            short_t = [t for t in feedback if t.get("direction")=="short"]
            lwr = sum(1 for t in long_t  if t["result"]=="win")/len(long_t) *100 if long_t  else 0
            swr = sum(1 for t in short_t if t["result"]=="win")/len(short_t)*100 if short_t else 0
            dp = pd.DataFrame({
                "Direction": ["📈 Long","📉 Short"],
                "Trades":    [len(long_t),len(short_t)],
                "Win Rate":  [f"{lwr:.0f}%",f"{swr:.0f}%"],
                "Avg P&L":   [
                    f"{sum(t.get('pnl_pct',0) for t in long_t)/len(long_t):+.1f}%" if long_t else "—",
                    f"{sum(t.get('pnl_pct',0) for t in short_t)/len(short_t):+.1f}%" if short_t else "—",
                ]
            })
            st.dataframe(dp, use_container_width=True, hide_index=True)
    else:
        st.markdown(bcard(f"Need 5+ closed trades for quality analysis. Have {len(feedback)} so far.", "info"), unsafe_allow_html=True)

# ══ Tab 4: Backtest ═══════════════════════════════════════════════════════════
with tab4:
    if bt:
        r1a,r1b = st.columns(2)
        r2a,r2b = st.columns(2)
        r3a,r3b = st.columns(2)
        r1a.metric("Win Rate",    f"{bt.get('win_rate',0):.1f}%")
        r1b.metric("Expectancy",  f"{bt.get('expectancy_pct',0):+.2f}%")
        r2a.metric("Avg Win",     f"{bt.get('avg_win_pct',0):+.2f}%")
        r2b.metric("Avg Loss",    f"{bt.get('avg_loss_pct',0):+.2f}%")
        r3a.metric("Long WR",     f"{bt.get('long_win_rate',0):.1f}%",
                   delta=f"{bt.get('long_signals',0)} signals")
        r3b.metric("Short WR",    f"{bt.get('short_win_rate',0):.1f}%",
                   delta=f"{bt.get('short_signals',0)} signals")

        if bt.get("rsi_win_rates"):
            st.subheader("RSI Bucket Performance")
            rsi_df = pd.DataFrame([{"RSI Range":k,"Win %":f"{v:.0f}%"}
                                    for k,v in bt["rsi_win_rates"].items()])
            st.dataframe(rsi_df, use_container_width=True, hide_index=True)

        if bt.get("regime_stats"):
            st.subheader("By Regime")
            rg = []
            for r, s in bt["regime_stats"].items():
                tot = s["wins"]+s["losses"]
                if tot:
                    rg.append({"Regime":r,"WR":f"{s['wins']/tot*100:.0f}%","N":tot})
            if rg:
                st.dataframe(pd.DataFrame(rg), use_container_width=True, hide_index=True)
    else:
        st.markdown(bcard("No backtest data yet. Run: Actions → Trading Bot → backtest mode", "info"), unsafe_allow_html=True)

# ══ Tab 5: Auto-Adjust ════════════════════════════════════════════════════════
with tab5:
    if adj:
        conf = adj.get("confidence","").upper()
        icon = "🟢" if conf=="HIGH" else "🟡" if conf=="MEDIUM" else "🔴"
        st.markdown(bcard(
            f"{icon}  <b>Confidence: {conf}</b><br>{adj.get('summary','')}",
            "alert"
        ), unsafe_allow_html=True)
        st.write(f"**Priority change:** {adj.get('priority_change','')}")
        if adj.get("adjustments"):
            for a in adj["adjustments"]:
                st.markdown(bcard(
                    f"🔧 <b>{a['param']}</b>: {a['current']} → <b>{a['suggested']}</b>"
                    f"<br><span style='opacity:0.7;font-size:12px'>{a['reason']}</span>",
                    "alert"
                ), unsafe_allow_html=True)
        st.warning("To apply: edit RISK dict in bot.py → push to GitHub.")
    else:
        st.markdown(bcard("No auto-adjust data yet. Actions → auto_adjust mode", "info"), unsafe_allow_html=True)

# ══ Tab 6: Log ════════════════════════════════════════════════════════════════
with tab6:
    log_path = DATA_DIR / "bot.log"
    if log_path.exists():
        lines = log_path.read_text().strip().split("\\n")[-80:]
        colored = []
        for line in lines:
            if "ERROR"      in line: colored.append(f"🔴 {line}")
            elif "WARN"     in line: colored.append(f"🟡 {line}")
            elif "APPROVED" in line: colored.append(f"✅ {line}")
            elif "REJECTED" in line: colored.append(f"❌ {line}")
            elif "CLOSED"   in line: colored.append(f"💰 {line}")
            elif "EMERGENCY"in line: colored.append(f"🚨 {line}")
            elif "Trailing" in line: colored.append(f"📌 {line}")
            elif "EOD"      in line: colored.append(f"🔔 {line}")
            elif "Time exit"in line: colored.append(f"⏰ {line}")
            elif "MACRO"    in line: colored.append(f"🟣 {line}")
            elif "Watchlist"in line: colored.append(f"🗂 {line}")
            else:                    colored.append(f"   {line}")
        st.code("\\n".join(colored), language="text")
    else:
        st.markdown(bcard("No log file yet — bot hasn't run yet.", "info"), unsafe_allow_html=True)

# ══ Tab 7: Watchlist ══════════════════════════════════════════════════════════
with tab7:
    st.subheader("Active Watchlist")
    if wl_data:
        core    = wl_data.get("core", [])
        active  = wl_data.get("active", [])
        dynamic = [t for t in active if t not in core]
        updated = wl_data.get("updated_at","")[:16].replace("T"," ")
        st.caption(f"Last updated: {updated}")

        ca, cb = st.columns(2)
        ca.metric("Total Tickers",   len(active))
        cb.metric("Dynamic Tickers", len(dynamic))

        st.subheader("Core (always on)")
        st.markdown(bcard(", ".join(core), "info"), unsafe_allow_html=True)

        if dynamic:
            st.subheader("Dynamic (today's movers)")
            st.markdown(bcard(", ".join(dynamic), "win"), unsafe_allow_html=True)
        else:
            st.markdown(bcard("No dynamic tickers yet — updates at market open 9:30 AM.", "alert"), unsafe_allow_html=True)
    else:
        st.markdown(bcard("Watchlist data not available yet.", "info"), unsafe_allow_html=True)

# ══ Tab 8: Changelog ══════════════════════════════════════════════════════════
with tab8:
    st.subheader("Boticus — Changelog")
    st.caption("Full build history — what was added, changed, and why.")

    changelog = [
        {
            "version": "v0.1 — Foundation",
            "date":    "Jun 2026",
            "kind":    "info",
            "items": [
                "Core bot architecture: data feeds → signal engine → AI brain → execution",
                "Alpaca paper trading integration with bracket orders",
                "yfinance for price history, FRED for macro data (Fed funds, CPI, yield curve)",
                "Basic signal scanner: trend (SMA50/200), RSI, volume, ATR gates",
                "Claude Opus for signal scoring — outputs JSON with score, reasoning, risk flag",
                "GitHub Actions deployment — runs every 10 min during market hours, free tier",
                "State persistence via GitHub Actions cache between runs",
            ]
        },
        {
            "version": "v0.2 — Intelligence Layer",
            "date":    "Jun 2026",
            "kind":    "info",
            "items": [
                "Trade feedback loop — every closed trade logged to feedback.json",
                "Pattern memory — AI reads trade history before every signal score",
                "Pattern memory surfaces: win rate by regime, VIX performance, stop vs target ratio",
                "Short signal scanner — downtrend + overbought reversal setups",
                "Bear market mode — shorts auto-activate in trending_down or volatile regimes",
                "Futures suite: ES, NQ, YM, RTY, CL, GC, ZN — risk score drives direction bias",
                "Macro ETF proxies: TLT, GLD, HYG, XLK, XLU for sector rotation detection",
                "Reddit sentiment scanner: r/wallstreetbets, r/stocks, r/options, r/investing",
                "SEC EDGAR Form 4 insider filings scan for watchlist tickers",
                "CBOE put/call ratio monitoring",
            ]
        },
        {
            "version": "v0.3 — Deployment + Alerts",
            "date":    "Jun 2026",
            "kind":    "info",
            "items": [
                "Telegram alerts fully wired: signals, trade opens, trade closes, daily summary",
                "Status check mode — sends account equity and open positions to Telegram on demand",
                "Streamlit dashboard deployed on Streamlit Cloud — always-on, free",
                "State commit to GitHub after every run — dashboard reads live data from repo",
                "Dashboard: 7 tabs — Positions, P&L, Quality, Backtest, Adjust, Log, Watchlist",
                "Dashboard auto-refreshes every 5 minutes",
                "Mobile-optimized CSS — Inter font, 2-column metric grid, expandable position cards",
            ]
        },
        {
            "version": "v0.4 — Signal Quality",
            "date":    "Jun 2026",
            "kind":    "win",
            "items": [
                "Headline scoring system — 3 layers: company keywords, macro triggers, amplification",
                "POS_KEYWORDS (40+): beat expectations, raised guidance, FDA approval, buyback, AI deal...",
                "NEG_KEYWORDS (40+): fraud, SEC investigation, bankruptcy, guidance cut, recall...",
                "Macro trigger dictionary (40+ buzzwords with impact level + directional bias):",
                "  Trump: trump, tariff, trade war, truth social, trump executive order",
                "  Fed: federal reserve, jerome powell, fomc, rate cut, rate hike, cpi, inflation",
                "  Middle East: iran, houthi, israel, opec, oil, strait of hormuz",
                "  Russia/Ukraine: russia, ukraine, nato, putin",
                "  China/Taiwan: china, taiwan, xi jinping, south china sea, semiconductor",
                "  North Korea: north korea, missile test",
                "  Market: flash crash, circuit breaker, bank failure, default",
                "  AI/Tech: openai, deepseek, chatgpt, ai breakthrough",
                "Headline-sensitive tickers (1.5x amplification): TSLA, PLTR, COIN, AMD, META, GME, NVDA, BABA, ARM...",
                "Macro alert broadcaster — immediate Telegram alert when high-impact trigger fires",
                "News emergency exit — closes long positions if headline score < -50 or emergency keyword detected",
                "Emergency keywords: SEC charges, bankruptcy filing, trading halted, FDA rejection, CEO arrested...",
            ]
        },
        {
            "version": "v0.5 — Position Monitor",
            "date":    "Jun 2026",
            "kind":    "win",
            "items": [
                "Alpaca position sync — source of truth, detects silent closes between runs",
                "Real-time prices via Alpaca quotes API (not delayed yfinance) for open positions",
                "Trailing stops: +1% → breakeven, +2% → 0.5% trail, +4% → 1% trail, +7% → 2% trail",
                "Time-based exits: dead money (flat 4h) closes position, max hold 6h force close",
                "Tighten stop after 2h if down >1.5% — limits damage on weak setups",
                "EOD close — all positions closed by 3:55 PM ET, no overnight gap risk",
                "Trailing stop updates sent to Alpaca via order API and Telegram",
                "Kill switch — halts all trading if daily loss exceeds 2% of account",
            ]
        },
        {
            "version": "v0.6 — Backtesting + Auto-Adjust",
            "date":    "Jun 2026",
            "kind":    "win",
            "items": [
                "180-day backtest module — walk-forward simulation on historical price data",
                "Backtest outputs: win rate, avg win/loss, expectancy, regime breakdown, RSI/volume buckets",
                "First backtest result: 23.2% WR, -1.20% expectancy — data collection mode activated",
                "Auto-adjust module — Sonnet reviews backtest + live feedback, suggests parameter changes",
                "Auto-adjust applied (high confidence): RSI min 52, volume 1.3x, data collection mode",
                "Regime-gated shorts — only fires in trending_down or volatile, blocked in bull/ranging",
                "Excluding ranging markets for longs — 16% WR vs 37% in trending_up",
                "Sunday auto-backtest + weekly review sent to Telegram",
            ]
        },
        {
            "version": "v0.7 — Watchlist Expansion",
            "date":    "Jun 2026",
            "kind":    "win",
            "items": [
                "Core watchlist expanded to 40+ tickers across all sectors",
                "Added indexes: MDY (mid-cap), VXX (volatility), EEM, EFA, FXI, EWJ (international)",
                "Added sectors: XLU (utilities), XLI (industrials), XLP (consumer staples)",
                "Added macro proxies: UUP (dollar index), IJR (small cap ETF)",
                "Dynamic universe of 150+ candidates: biotech, meme, small/mid cap, semis, defense, REITs",
                "Daily dynamic scan at 9:30 AM — scores universe on momentum, volume, RSI",
                "Top 15 daily movers auto-added to active watchlist",
                "Auto-removal: tickers dropped if volume dries up, movement flat, not recently traded",
                "Watchlist changes sent to Telegram each morning with top movers and reasons",
                "Watchlist tab added to dashboard — shows core vs dynamic, last update time",
            ]
        },
    ]

    for entry in changelog:
        with st.expander(f"**{entry['version']}**  ·  {entry['date']}", expanded=False):
            for item in entry["items"]:
                if item.startswith("  "):
                    st.markdown(f"&nbsp;&nbsp;&nbsp;&nbsp;↳ {item.strip()}")
                else:
                    st.markdown(f"• {item}")

'''


def generate_dashboard():
    """
    Write dashboard.py to repo root and commit it to GitHub.
    """
    dash_file = Path("dashboard.py")
    dash_file.write_text(DASHBOARD_CODE)
    log(f"Dashboard written to {dash_file}")

    # Commit dashboard.py directly to GitHub
    import base64
    if GITHUB_TOKEN and GITHUB_REPO:
        try:
            api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/dashboard.py"
            headers = {
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            b64 = base64.b64encode(dash_file.read_bytes()).decode()
            # Get existing SHA if file exists
            sha = None
            r = requests.get(api_url, headers=headers, timeout=8)
            if r.status_code == 200:
                sha = r.json().get("sha")
            payload = {
                "message": f"bot: generate dashboard [{datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}]",
                "content": b64,
                "branch": "main",
            }
            if sha:
                payload["sha"] = sha
            r = requests.put(api_url, headers=headers, json=payload, timeout=15)
            if r.status_code in (200, 201):
                log("dashboard.py committed to GitHub repo successfully")
            else:
                log(f"dashboard.py commit failed: {r.status_code} {r.text[:150]}", "WARN")
        except Exception as e:
            log(f"dashboard.py commit error: {e}", "WARN")
    else:
        log("GITHUB_TOKEN not set — dashboard.py written locally only", "WARN")

    dash_url = DASHBOARD_URL or "https://share.streamlit.io"
    _tg(
        f"📊 *Dashboard committed to repo*\n"
        f"File: `dashboard.py` now in boticus repo root\n\n"
        f"*Deploy on Streamlit Cloud (free):*\n"
        f"1. share.streamlit.io → New app\n"
        f"2. Repo: jaydrama21-ai/boticus\n"
        f"3. Main file: `dashboard.py`\n"
        f"4. Deploy\n\n"
        f"{f'Live dashboard: {dash_url}' if DASHBOARD_URL else 'Add DASHBOARD_URL secret once deployed'}"
    )
    return str(dash_file)


if __name__ == "__main__":
    main()
