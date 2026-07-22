"""
bot.py — Trading Bot
Single-file deployment version for GitHub Actions.
Runs once per trigger, does its job, exits cleanly.
Alpaca bracket orders handle stop/target monitoring 24/7 server-side.

FIXES APPLIED (Jun 25 2026):
  1. scan_short() — Mode B intraday breakdown added
  2. _tg() — MarkdownV2 escape prevents parse errors on +/- price strings
  3. execute_signal() — time_in_force "day" → "gtc" so bracket legs survive past 4 PM
  4. update_stop_loss() — now finds actual stop LEG id, not parent order
  5. reprotect_positions() — self-healing: re-places GTC OCO if legs go missing
  6. run_position_monitor() — calls reprotect_positions() after every sync
  7. main() backtest block — commit_state_to_github() added before return
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
# Small-account experiment: if >0, size positions as if the account held this
# much (default 0 = off, use real live equity). SMALL_ACCT_MAX_POS_PCT raises the
# per-position cap so a single share of a mid-priced name can fit; tickers whose
# 1 share still exceeds the cap are skipped rather than silently blowing it.
SMALL_ACCT_EQUITY   = float(os.environ.get("SMALL_ACCT_EQUITY", "0") or 0)
SMALL_ACCT_MAX_POS_PCT = float(os.environ.get("SMALL_ACCT_MAX_POS_PCT", "0.34"))
# Fewer concurrent slots in small-account mode so total deployed capital stays
# near 100% of the simulated equity instead of quietly using margin.
SMALL_ACCT_MAX_POSITIONS = int(os.environ.get("SMALL_ACCT_MAX_POSITIONS", "3"))
# Options-only mode (default off). When on, signals on OPTIONS_TICKERS underlyings
# are traded as long calls/puts instead of stock; non-optionable names are skipped.
OPTIONS_ENABLED   = os.environ.get("OPTIONS_ENABLED", "false").lower() == "true"
OPTIONS_MIN_DTE   = int(os.environ.get("OPTIONS_MIN_DTE", "7"))
OPTIONS_MAX_DTE   = int(os.environ.get("OPTIONS_MAX_DTE", "21"))
OPTIONS_MONEYNESS = os.environ.get("OPTIONS_MONEYNESS", "itm").lower()  # itm | atm
# Dry run: do contract discovery + quoting + sizing and LOG the trade it would
# place, but do NOT send the order. Use this on the first live activation to
# confirm the Alpaca options API responses before trading real (paper) orders.
OPTIONS_DRY_RUN   = os.environ.get("OPTIONS_DRY_RUN", "false").lower() == "true"
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
OPUS_MODEL   = "claude-opus-4-6"
SONNET_MODEL = "claude-sonnet-4-6"


# ── Watchlist ──────────────────────────────────────────────────────────────────
CORE_WATCHLIST = [
    "SPY", "QQQ", "IWM", "DIA", "MDY", "VXX",
    "EEM", "EFA", "FXI", "EWJ", "IEUR",
    "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN",
    "TSLA", "AMD", "PLTR", "COIN",
    "JPM", "BAC", "GS", "MS", "C",
    "XOM", "CVX", "MRO",
    "UNH", "LLY", "ABBV",
    "XLK", "XLF", "XLE", "XLV", "XBI", "XRT", "XLU", "XLI", "XLP",
    "TLT", "GLD", "SLV", "USO", "UUP",
    "IJR", "MDY",
]

DYNAMIC_UNIVERSE = [
    "MRNA", "BNTX", "REGN", "BIIB", "GILD", "VRTX", "SRPT",
    "ALNY", "INCY", "BMRN", "RGEN", "EXEL", "IONS", "RARE",
    "ACAD", "SAGE", "BEAM", "EDIT", "NTLA", "CRSP",
    "GME", "AMC", "SOFI", "RIVN", "LCID", "HOOD",
    "RBLX", "SNAP", "UBER", "LYFT", "DKNG", "PENN",
    "CLOV", "WISH", "WKHS", "NKLA", "GOEV",
    "SMCI", "CELH", "HIMS", "JOBY", "ACHR", "LUNR",
    "SOUN", "BBAI", "KTOS", "RKLB", "ASTS", "SATL",
    "GEVO", "PLUG", "FCEL", "BLNK", "CHPT",
    "CRWD", "PANW", "DDOG", "SNOW", "NET", "MDB",
    "BILL", "ZS", "OKTA", "HUBS", "GTLB", "BRZE",
    "MNDY", "CFLT", "ESTC", "PATH", "AI", "BBAI",
    "ARM", "ASML", "TSM", "AVGO", "QCOM", "MRVL",
    "SWKS", "QRVO", "MPWR", "WOLF", "ON", "AMAT",
    "KLAC", "LRCX", "COHR", "AMBA", "ALGM",
    "SHOP", "MELI", "SE", "GRAB", "BABA", "JD", "PDD",
    "NU", "STNE", "PAGS", "TCEHY", "NTES",
    "FCX", "NEM", "AEM", "WPM", "GOLD", "MP", "LAC",
    "AA", "CLF", "X", "NUE", "STLD",
    "LMT", "RTX", "NOC", "BA", "GD", "HII", "LDOS",
    "RKLB", "ASTS", "MAXR", "SPIR",
    "VNQ", "XLRE", "AMT", "CCI", "EQIX", "PLD",
    "TGT", "WMT", "COST", "HD", "LOW", "NKE", "LULU",
    "ABNB", "BKNG", "MAR", "HLT",
    "TQQQ", "SQQQ", "SPXL", "SPXS", "SOXL", "SOXS",
    "UVXY", "SVXY", "TNA", "TZA",
    "XHB", "ITB", "KRE", "ARKK", "ARKG", "ARKW",
    "ICLN", "TAN", "FAN", "JETS", "PBW",
]

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

_ACTIVE_WATCHLIST = None

def load_active_watchlist() -> list:
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
    global _ACTIVE_WATCHLIST
    _ACTIVE_WATCHLIST = wl
    wl_file = STATE_DIR / "watchlist.json"
    wl_file.write_text(json.dumps({
        "active":    wl,
        "core":      CORE_WATCHLIST,
        "updated_at": datetime.now(ET).isoformat(),
    }, indent=2))

def get_watchlist() -> list:
    if _ACTIVE_WATCHLIST is None:
        return load_active_watchlist()
    return _ACTIVE_WATCHLIST

def update_dynamic_watchlist():
    MAX_DYNAMIC   = 15
    MIN_VOLUME    = 500_000
    log("Updating dynamic watchlist...")
    current_wl  = get_watchlist()
    core_set    = set(CORE_WATCHLIST)
    dynamic_now = [t for t in current_wl if t not in core_set]
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
            ret_5d    = (closes[-1] - closes[-5]) / closes[-5] * 100
            vol_ratio = float(volumes[-1]) / avg_vol if avg_vol else 0
            rsi       = calc_rsi(closes)
            score = 0
            score += min(40, abs(ret_5d) * 4)
            score += min(30, vol_ratio * 15)
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
    candidates.sort(key=lambda x: x["score"], reverse=True)
    top_candidates = [c["symbol"] for c in candidates[:MAX_DYNAMIC]]
    to_remove = []
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
            ret_3d    = (closes[-1] - closes[-3]) / closes[-3] * 100 if len(closes) >= 3 else 0
            vol_ratio = float(volumes[-1]) / avg_vol if avg_vol else 0
            if avg_vol < MIN_VOLUME:
                to_remove.append((sym, f"volume too low (avg {avg_vol:,.0f})"))
            elif abs(ret_3d) < 0.5 and vol_ratio < 0.8 and sym not in traded_syms:
                to_remove.append((sym, f"stale: {ret_3d:+.1f}% 3d, {vol_ratio:.1f}x vol"))
        except:
            to_remove.append((sym, "data error"))
    new_wl = list(CORE_WATCHLIST)
    kept = [s for s in dynamic_now if s not in [r[0] for r in to_remove]]
    new_wl.extend(kept)
    added = []
    for sym in top_candidates:
        if sym not in new_wl and len([s for s in new_wl if s not in core_set]) < MAX_DYNAMIC:
            new_wl.append(sym)
            added.append(sym)
    seen = set()
    new_wl = [x for x in new_wl if not (x in seen or seen.add(x))]
    save_active_watchlist(new_wl)
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
            f"*Watchlist Updated*\n"
            f"Total: {len(new_wl)} tickers "
            f"({len(CORE_WATCHLIST)} core + {len(dynamic_after)} dynamic)\n\n"
            + (f"Added ({len(added)}): {', '.join(added)}\n" if added else "") +
            (f"Removed ({len(removed_syms)}): {', '.join(removed_syms)}\n" if removed_syms else "") +
            f"\nTop movers in universe:\n{top_str}"
        )
    else:
        log("  No watchlist changes today")
    return new_wl

WATCHLIST = property(get_watchlist) if False else CORE_WATCHLIST

# ── Risk config ────────────────────────────────────────────────────────────────
RISK = {
    "stop_loss_atr_mult":    1.8,
    "take_profit_atr_mult":  3.5,   # ride winners: 3.5x ATR target (was 3.0)
    "max_position_pct":      0.05,
    "max_risk_per_trade_pct":0.02,
    "max_daily_loss_pct":    0.02,
    "max_open_positions":    6,
    "rsi_min":               58,    # was 55 — 58-75 momentum band tests strongest
    "rsi_max":               75,    # was 72
    "volume_min_mult":       1.0,   # soft-penalty trigger (was 0.5); NOT a hard gate
    "atr_pct_max":           0.05,  # was 0.04 — 0.05 improves both signal count and EV
    "dead_money_hours":      4,
    "max_hold_hours":        6,
}

# Regime (Mode-A) shorts backtest at ~20% WR and drag per-trade EV negative across
# 1000+ signals. Disabled. Mode-B intraday-breakdown shorts (crash-day hedge) are
# retained — they fire only on confirmed risk-off selloffs and aren't in that sample.
ENABLE_REGIME_SHORTS = False

# ── Data-driven parameter overlay ───────────────────────────────────────────────
# Backtest results feed live params through a reviewed overlay file, NOT by editing
# this dict. Only the SIGNAL params below are auto-tunable, each hard-bounded. Risk
# controls (max_risk_per_trade_pct, max_daily_loss_pct, max_position_pct,
# max_open_positions) are deliberately NOT here — they protect capital and stay
# human-only. See apply_risk_overrides() and run_auto_adjust().
TUNABLE_BOUNDS = {
    "rsi_min":              (45, 68),
    "rsi_max":              (68, 82),
    "volume_min_mult":      (0.5, 3.0),
    "atr_pct_max":          (0.02, 0.08),
    "stop_loss_atr_mult":   (1.0, 3.0),
    "take_profit_atr_mult": (2.0, 5.0),
}
# Candidate must beat current true-EV (mean_pnl_pct) on the held-out window by at
# least this margin (percentage points per trade) to be staged for approval.
VALIDATION_MARGIN   = 0.05
# Compact, liquid, representative set for fast walk-forward validation backtests.
VALIDATION_SYMBOLS  = [
    "SPY", "QQQ", "IWM", "DIA", "AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN",
    "JPM", "GS", "XOM", "CVX", "UNH", "LLY", "XLK", "XLF", "XLE", "XLV",
    "XBI", "GLD", "TLT", "SLV",
]

# ── State files ────────────────────────────────────────────────────────────────
STATE_DIR   = Path(os.environ.get("STATE_DIR", "/tmp/bot_state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
TRADES_FILE   = STATE_DIR / "trades.json"
FEEDBACK_FILE = STATE_DIR / "feedback.json"
LOG_FILE      = STATE_DIR / "bot.log"
RISK_OVERRIDE_FILE = STATE_DIR / "risk_overrides.json"          # active, applied at startup
RISK_PENDING_FILE  = STATE_DIR / "risk_overrides_pending.json"  # proposed, awaiting /apply
RISK_PREV_FILE     = STATE_DIR / "risk_overrides_prev.json"     # backup for /revert


def _clamp_overlay(raw: dict) -> tuple[dict, list]:
    """Filter a param dict to tunable signal params only, clamped to bounds.
    Anything not in TUNABLE_BOUNDS (e.g. risk-control params) is dropped — this is
    the structural guarantee that auto-tuning can never touch capital protection."""
    clean, notes = {}, []
    for k, v in (raw or {}).items():
        if k not in TUNABLE_BOUNDS:
            notes.append(f"{k}: not tunable — ignored")
            continue
        try:
            v = float(v)
        except (TypeError, ValueError):
            notes.append(f"{k}: non-numeric — ignored")
            continue
        lo, hi = TUNABLE_BOUNDS[k]
        cv = max(lo, min(hi, v))
        if cv != v:
            notes.append(f"{k}: clamped {v}->{cv} to [{lo},{hi}]")
        clean[k] = int(round(cv)) if k in ("rsi_min", "rsi_max") else round(cv, 3)
    # Never let RSI band invert
    if "rsi_min" in clean and "rsi_max" in clean and clean["rsi_min"] >= clean["rsi_max"]:
        clean.pop("rsi_min"); clean.pop("rsi_max")
        notes.append("rsi_min >= rsi_max — RSI override skipped")
    return clean, notes


def _overlay_params(path: Path) -> dict:
    """Read an overlay file and return its clamped params ({} if absent/empty)."""
    try:
        if not path.exists():
            return {}
        data = json.loads(path.read_text())
        clean, _ = _clamp_overlay(data.get("params", data))
        return clean
    except Exception:
        return {}


def apply_risk_overrides() -> dict:
    """Overlay the reviewed risk_overrides.json onto the RISK defaults, in place.
    Called at the start of main() so every scan in this run uses the live params."""
    clean = _overlay_params(RISK_OVERRIDE_FILE)
    if clean:
        for k, v in clean.items():
            RISK[k] = v
        log(f"Applied risk overrides: {clean}")
    return clean

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


def calc_adx(highs, lows, closes, period=14):
    """Trend strength 0-100. Above 25 = trending, below 20 = choppy."""
    if len(closes) < period * 2 + 1:
        return 25.0
    tr_list, dm_plus, dm_minus = [], [], []
    for i in range(1, len(closes)):
        h, l, pc = highs[i], lows[i], closes[i-1]
        tr_list.append(max(h - l, abs(h - pc), abs(l - pc)))
        up   = highs[i]  - highs[i-1]
        down = lows[i-1] - lows[i]
        dm_plus.append(up   if up > down and up > 0   else 0.0)
        dm_minus.append(down if down > up and down > 0 else 0.0)
    def ws(data, p):
        s = [float(sum(data[:p]))]
        for x in data[p:]:
            s.append(s[-1] - s[-1] / p + float(x))
        return s
    atr_s = ws(tr_list, period)
    dp_s  = ws(dm_plus,  period)
    dm_s  = ws(dm_minus, period)
    di_p = [100.0 * dp / a if a > 0 else 0.0 for dp, a in zip(dp_s, atr_s)]
    di_m = [100.0 * dm / a if a > 0 else 0.0 for dm, a in zip(dm_s, atr_s)]
    dx   = [100.0 * abs(pp - mm) / (pp + mm) if (pp + mm) > 0 else 0.0
            for pp, mm in zip(di_p, di_m)]
    adx  = ws(dx, period)
    return round(float(adx[-1]), 1) if adx else 25.0


def is_opex_friday():
    """True on 3rd Friday of month — monthly options expiration, avoid new entries."""
    today = date.today()
    if today.weekday() != 4:
        return False
    first = date(today.year, today.month, 1)
    first_fri = first + timedelta(days=(4 - first.weekday()) % 7)
    third_fri = first_fri + timedelta(weeks=2)
    return today == third_fri


def kelly_size(equity, rr, ai_score):
    """Half-Kelly position sizing using live win rate. Falls back to fixed risk."""
    feedback = load_feedback()
    if len(feedback) < 10:
        return RISK["max_position_pct"]
    wins  = sum(1 for t in feedback if t["result"] == "win")
    total = len(feedback)
    p = wins / total
    b = max(rr, 1.0)
    kelly = (p * b - (1 - p)) / b
    half  = max(0.0, kelly / 2.0)
    ai_mult = 1.0 + max(0.0, (ai_score - 55) / 100.0)
    sized = half * ai_mult
    return max(0.005, min(RISK["max_position_pct"], sized))


def calc_market_breadth():
    """Fraction of watchlist above SMA50 — broader regime signal than SPY alone."""
    above = sum(1 for t in tickers.values() if t.sma_50 > 0 and t.price > t.sma_50)
    total = sum(1 for t in tickers.values() if t.sma_50 > 0)
    pct   = above / total * 100 if total > 0 else 50.0
    return {
        "breadth_pct": round(pct, 1),
        "above_50":    above,
        "total":       total,
        "signal":      "bullish" if pct >= 60 else "bearish" if pct <= 40 else "neutral",
    }


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
        self.macro_triggers = []
        self.macro_alert = False
        self.adx_14 = 25.0

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
        self.reddit_mentions = {}
        self.sector_rotation = {}
        self.unusual_volume  = []
        self.fear_greed      = {"score": 50, "rating": "neutral", "change": 0}
        self.breadth         = {"breadth_pct": 50.0, "signal": "neutral"}

tickers: dict[str, TickerData] = {}
macro = MacroData()

NEG_KEYWORDS = [
    "fraud","sec investigation","bankruptcy","recall","downgrade",
    "guidance cut","earnings miss","layoff","lawsuit","restatement","delisting",
    "data breach","accounting","whistleblower","short seller","going concern",
    "class action","criminal charges","doj investigation","ftc investigation",
    "product recall","safety recall","plant shutdown","factory fire",
    "rate hike","hawkish","tightening","inflation surge","cpi higher",
    "recession fears","yield curve inverts","stagflation","credit crunch",
    "bank failure","systemic risk","liquidity crisis","margin calls",
    "fed raises","rate increase","quantitative tightening",
    "war escalates","military strike","missile attack","nuclear threat",
    "sanctions imposed","trade war","tariffs increased","embargo",
    "invasion","terrorist attack","cyber attack","infrastructure attack",
    "oil supply cut","opec cut","energy crisis","supply chain disruption",
    "iran attack","north korea","russia escalates","china threatens",
    "taiwan strait","south china sea","houthi attack","strait of hormuz",
    "trump tariffs","trump imposes","trump threatens","trump sanctions",
    "trump fires","trump bans","trump executive order","trump withdraws",
    "government shutdown","debt ceiling","default risk","credit downgrade",
    "impeachment","indictment","investigation launched","subpoena",
    "election disputed","political crisis","congress blocks",
    "flash crash","circuit breaker","trading halted","market selloff",
    "margin call","forced liquidation","deleveraging","fund collapse",
    "contagion","bank run","credit downgrade","sovereign default",
]

POS_KEYWORDS = [
    "beat expectations","record revenue","upgrade","raised guidance","buyback",
    "dividend increase","fda approval","contract win","partnership","acquisition",
    "ai deal","data center","earnings beat","raised forecast","record profit",
    "market share gain","new product launch","clinical trial success",
    "patent approved","ipo debut","strategic alliance","merger approved",
    "cost cutting","margin expansion","share repurchase","special dividend",
    "rate cut","dovish","easing","inflation cools","cpi lower",
    "soft landing","gdp beat","jobs strong","fed pauses","rate hold",
    "quantitative easing","stimulus","fed pivot","lower rates",
    "inflation falls","deflation","yield drops","bonds rally",
    "ceasefire","peace deal","trade deal","tariffs reduced","sanctions lifted",
    "diplomatic breakthrough","nato unity","alliances strengthened",
    "oil supply increase","opec increases","energy prices fall",
    "supply chain normalizes","trade agreement signed","wto ruling",
    "trump deal","trump signs","trump approves","trump lifts","trump reduces",
    "deregulation","tax cuts","infrastructure bill","trade surplus",
    "trump tariff pause","tariff exemption","trade truce","china deal",
    "bipartisan deal","budget passed","debt ceiling raised","stimulus approved",
    "short squeeze","gamma squeeze","strong earnings season","buyback program",
    "institutional buying","insider buying","record inflows","etf creation",
    "index addition","s&p 500 addition","fund inflows","short covering",
]

MACRO_TRIGGERS = {
    "trump":        {"impact": "high",   "direction": "mixed",  "note": "Trump statement — check context"},
    "tariff":       {"impact": "high",   "direction": "bearish","note": "Tariffs = inflation + trade war risk"},
    "trade war":    {"impact": "high",   "direction": "bearish","note": "Trade war = risk off"},
    "trump tweet":  {"impact": "high",   "direction": "mixed",  "note": "Trump social post — volatile"},
    "truth social": {"impact": "medium", "direction": "mixed",  "note": "Trump platform statement"},
    "federal reserve":   {"impact": "high",   "direction": "mixed",  "note": "Fed statement — major mover"},
    "jerome powell":     {"impact": "high",   "direction": "mixed",  "note": "Fed chair speaking"},
    "fomc":              {"impact": "high",   "direction": "mixed",  "note": "Fed meeting — sit out"},
    "interest rate":     {"impact": "high",   "direction": "mixed",  "note": "Rate decision incoming"},
    "rate cut":          {"impact": "high",   "direction": "bullish","note": "Rate cuts = risk on"},
    "rate hike":         {"impact": "high",   "direction": "bearish","note": "Rate hikes = risk off"},
    "inflation":         {"impact": "medium", "direction": "bearish","note": "Inflation = hawkish risk"},
    "cpi":               {"impact": "high",   "direction": "mixed",  "note": "CPI print — major mover"},
    "iran":              {"impact": "high",   "direction": "bearish","note": "Iran = oil risk + geopolitical"},
    "houthi":            {"impact": "medium", "direction": "bearish","note": "Red Sea = supply chain risk"},
    "israel":            {"impact": "medium", "direction": "bearish","note": "Middle East tension"},
    "oil":               {"impact": "high",   "direction": "mixed",  "note": "Oil price = inflation signal"},
    "opec":              {"impact": "high",   "direction": "mixed",  "note": "OPEC decision = energy sector"},
    "strait of hormuz":  {"impact": "high",   "direction": "bearish","note": "Chokepoint risk = oil spike"},
    "russia":            {"impact": "high",   "direction": "bearish","note": "Russia = energy + risk off"},
    "ukraine":           {"impact": "medium", "direction": "bearish","note": "War escalation risk"},
    "nato":              {"impact": "medium", "direction": "mixed",  "note": "NATO statement = geopolitical"},
    "putin":             {"impact": "high",   "direction": "bearish","note": "Putin statement = risk off"},
    "china":             {"impact": "high",   "direction": "mixed",  "note": "China = trade + tech risk"},
    "taiwan":            {"impact": "high",   "direction": "bearish","note": "Taiwan = chip supply chain"},
    "xi jinping":        {"impact": "high",   "direction": "bearish","note": "Xi statement = China policy"},
    "south china sea":   {"impact": "high",   "direction": "bearish","note": "Military tension"},
    "semiconductor":     {"impact": "high",   "direction": "mixed",  "note": "Chip supply = tech sector"},
    "nvidia ban":        {"impact": "high",   "direction": "bearish","note": "Export controls = tech hit"},
    "north korea":       {"impact": "high",   "direction": "bearish","note": "NK = risk off spike"},
    "missile test":      {"impact": "high",   "direction": "bearish","note": "Military provocation"},
    "flash crash":       {"impact": "high",   "direction": "bearish","note": "Emergency — reduce exposure"},
    "circuit breaker":   {"impact": "high",   "direction": "bearish","note": "Market halt"},
    "bank failure":      {"impact": "high",   "direction": "bearish","note": "Systemic risk"},
    "default":           {"impact": "high",   "direction": "bearish","note": "Sovereign/corporate default"},
    "bitcoin":           {"impact": "medium", "direction": "mixed",  "note": "Crypto sentiment signal"},
    "crypto crash":      {"impact": "high",   "direction": "bearish","note": "Risk off signal"},
    "sec crypto":        {"impact": "medium", "direction": "bearish","note": "Regulatory risk"},
    "openai":            {"impact": "medium", "direction": "bullish","note": "AI news = tech sector boost"},
    "ai breakthrough":   {"impact": "medium", "direction": "bullish","note": "AI = growth narrative"},
    "chatgpt":           {"impact": "low",    "direction": "bullish","note": "AI sentiment"},
    "deepseek":          {"impact": "medium", "direction": "bearish","note": "AI competition risk for NVDA"},
}


def score_headlines(headlines: list, symbol: str) -> dict:
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
        pos = sum(1 for kw in POS_KEYWORDS if kw in h_low)
        neg = sum(1 for kw in NEG_KEYWORDS if kw in h_low)
        bullish += pos
        bearish += neg
        if pos > 0 or neg > 0:
            key.append(f"{'+ ' if pos > neg else '- '}{h[:80]}")
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
                if meta["direction"] == "bullish":
                    bullish += 2 if meta["impact"] == "high" else 1
                elif meta["direction"] == "bearish":
                    bearish += 2 if meta["impact"] == "high" else 1
                elif meta["direction"] == "mixed":
                    bullish += 1
                    bearish += 1
    amp = 1.5 if symbol in HEADLINE_SENSITIVE else 1.0
    score = round((bullish - bearish) * 20 * amp, 1)
    score = max(-100, min(100, score))
    if macro_found:
        high_impact = [m for m in macro_found if m["impact"] == "high"]
        if high_impact:
            log(f"  MACRO TRIGGER on {symbol}: "
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
# STOCKTWITS + FEAR & GREED
# ══════════════════════════════════════════════════════════════════════════════

REDDIT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}
REDDIT_UA_LIST = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/119.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/118.0.0.0 Safari/537.36",
]
RESEARCH_SUBREDDITS = []

def fetch_stocktwits_sentiment(symbols: list) -> dict:
    results = {}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; boticus/1.0)"}
    for sym in symbols[:20]:
        try:
            r = requests.get(
                f"https://api.stocktwits.com/api/2/streams/symbol/{sym}.json",
                headers=headers, timeout=8
            )
            if not r.ok: continue
            data     = r.json()
            messages = data.get("messages", [])
            symbol_d = data.get("symbol", {})
            if not messages: continue
            bullish = sum(1 for m in messages
                         if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bullish")
            bearish = sum(1 for m in messages
                         if m.get("entities", {}).get("sentiment", {}).get("basic") == "Bearish")
            total   = len(messages)
            bull_pct = bullish / total * 100 if total else 50
            bear_pct = bearish / total * 100 if total else 50
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
                    f"({total} msgs) {'TRENDING' if trending else ''}")
        except: pass
        time.sleep(0.3)
    return results

def fetch_fear_greed() -> dict:
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
                "rating":  rating,
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
    try:
        r = requests.get(
            "https://api.stocktwits.com/api/2/trending/symbols.json",
            headers={"User-Agent": "Mozilla/5.0 (compatible; boticus/1.0)"},
            timeout=8
        )
        if r.ok:
            symbols = r.json().get("symbols", [])
            tickers_list = [s["symbol"] for s in symbols[:15] if s.get("symbol")]
            log(f"  StockTwits trending: {', '.join(tickers_list[:10])}")
            return tickers_list
    except Exception as e:
        log(f"  StockTwits trending error: {e}", "WARN")
    return []


# ══════════════════════════════════════════════════════════════════════════════
# RESEARCH DIGEST
# ══════════════════════════════════════════════════════════════════════════════

def fetch_fed_speeches(max_speeches: int = 3) -> list:
    speeches = []
    try:
        from xml.etree import ElementTree as ET_xml
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
        for feed_url, label in [
            ("https://www.federalreserve.gov/feeds/speeches.xml", "speech"),
            ("https://www.federalreserve.gov/feeds/press_monetary.xml", "FOMC"),
        ]:
            r = requests.get(feed_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; boticus/1.0)"},
                timeout=10)
            if not r.ok: continue
            root  = ET_xml.fromstring(r.content)
            items = root.findall(".//item")[:3]
            for item in items:
                title = (item.findtext("title") or "").strip()
                link  = (item.findtext("link") or "").strip()
                if not link or not title: continue
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
            date_match = _re.search(r'/(\d{4})/(\d{2})/(\d{2})/', links[0])
            if date_match:
                t_date = _date(int(date_match.group(1)),
                               int(date_match.group(2)),
                               int(date_match.group(3)))
                if (_date.today() - t_date).days > days_back:
                    continue
            tr = requests.get(f"https://www.fool.com{links[0]}", headers=headers, timeout=12)
            if not tr.ok: continue
            clean = _re.sub(r'<[^>]+>', ' ', tr.text)
            clean = _re.sub(r'\s+', ' ', clean).strip()[:3000]
            if len(clean) < 300: continue
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
        except: pass
    log(f"Earnings transcripts: {len(transcripts)} found")
    return transcripts

def summarize_fed_and_earnings(fed: list, earnings: list) -> dict:
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
    log("Running weekly research digest (StockTwits + Fear & Greed + Fed + Earnings)...")
    fg = fetch_fear_greed()
    trending = fetch_stocktwits_trending()
    core_syms = CORE_WATCHLIST[:20]
    sentiment = fetch_stocktwits_sentiment(core_syms)
    bull_syms = [s for s, v in sentiment.items() if v.get("bullish_pct", 50) >= 65]
    bear_syms = [s for s, v in sentiment.items() if v.get("bearish_pct", 50) >= 65]
    log("Fetching Fed speeches...")
    fed_speeches = fetch_fed_speeches(max_speeches=3)
    log("Fetching earnings transcripts...")
    earnings = fetch_earnings_transcripts(get_watchlist(), days_back=14)
    sentiment_context = (
        f"Fear & Greed Index: {fg['score']:.0f}/100 ({fg['rating']})\n"
        f"Change from yesterday: {fg['change']:+.1f} points\n\n"
        f"StockTwits trending: {', '.join(trending[:10])}\n"
        f"Heavily bullish (65%+ bull): {', '.join(bull_syms) or 'none'}\n"
        f"Heavily bearish (65%+ bear): {', '.join(bear_syms) or 'none'}\n"
        + "\n".join([
            f"  {s}: {v['bullish_pct']:.0f}% bull / {v['bearish_pct']:.0f}% bear ({v['message_count']} msgs)"
            for s, v in sorted(sentiment.items(), key=lambda x: x[1].get("message_count",0), reverse=True)[:8]
        ])
    )
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
    fed_earnings_result = summarize_fed_and_earnings(fed_speeches, earnings)
    fed_insights = fed_earnings_result.get("insights", [])
    fed_summary  = fed_earnings_result.get("summary", "")
    all_insights = sentiment_insights + fed_insights
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
    fg_emoji = "green" if fg["score"] >= 60 else "red" if fg["score"] <= 40 else "yellow"
    findings_txt = "\n".join([f"- {i['finding'][:100]} ({i.get('source','')})" for i in all_insights[:5]])
    fed_str = f"\nFed: {', '.join(s['speaker'] for s in fed_speeches[:2])}" if fed_speeches else ""
    earn_str = f"\nEarnings: {', '.join(e['symbol'] for e in earnings[:5])}" if earnings else ""
    _tg(
        f"Weekly Research Digest\n\n"
        f"Fear and Greed: {fg['score']:.0f}/100 ({fg['rating'].replace('_',' ').title()}) "
        f"{fg['change']:+.1f} from yesterday\n"
        f"Retail trending: {', '.join(trending[:8])}"
        f"{fed_str}{earn_str}\n\n"
        f"Bullish crowd: {', '.join(bull_syms[:5]) or 'none'}\n"
        f"Bearish crowd: {', '.join(bear_syms[:5]) or 'none'}\n\n"
        f"Key insights:\n{findings_txt}"
    )
    return digest

def load_research_digest() -> str:
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
        lines.append(f"Fear & Greed: {fg.get('score',50):.0f}/100 ({fg.get('rating','neutral')})")
        if trend:
            lines.append(f"Retail trending: {', '.join(trend[:8])}")
        if bull:
            lines.append(f"Heavy retail bullishness: {', '.join(bull[:5])} — potential exhaustion risk")
        if bear:
            lines.append(f"Heavy retail bearishness: {', '.join(bear[:5])} — potential squeeze candidate")
        for i in items[:5]:
            lines.append(f"- {i.get('finding','')} [{i.get('confidence','')}]")
        lines.append(f"Summary: {digest.get('summary','')}")
        return "\n".join(lines)
    except Exception as e:
        log(f"Research digest load error: {e}", "WARN")
        return ""

def fetch_reddit_mentions(symbols: list) -> dict:
    """Uses StockTwits instead of Reddit (Reddit blocks GitHub Actions IPs)."""
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
    unusual = []
    for sym, t in tickers.items():
        if t.vol_ratio >= 2.0:
            unusual.append({"symbol": sym, "vol_ratio": t.vol_ratio, "price": t.price})
            log(f"  Unusual volume: {sym} {t.vol_ratio:.1f}x avg")
    return unusual

def fetch_sector_rotation() -> dict:
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
            # Intraday, volumes[-1] is today's in-progress partial bar. Exclude
            # it from the baseline so it doesn't drag avg_vol down (~4-7% early
            # in the session) and overstate vol_ratio; after hours it's a full
            # bar and either window is equivalent.
            _now_et    = datetime.now(ET)
            _mins_open = (_now_et.hour * 60 + _now_et.minute) - (9 * 60 + 30)
            _market_open = _now_et.weekday() < 5 and 0 < _mins_open < 390
            if _market_open and len(volumes) >= 21:
                avg_vol = float(np.mean(volumes[-21:-1]))
            else:
                avg_vol = float(np.mean(volumes[-20:]))
            vol_ratio  = volume / avg_vol if avg_vol else 0
            # Normalize vol_ratio for time of day:
            # At 10 AM we have 30 min of trading vs 390 min full day average
            # Raw vol_ratio of 0.1x at 10 AM = actually healthy pacing
            # Normalize so 0.1x at 10 AM (30/390 = 7.7% of day) → 1.3x normalized
            # ONLY during regular hours: pre-market / after-hours / weekend runs
            # read the last completed daily bar (a full day's volume), so
            # normalizing there would inflate vol_ratio by up to ~390x.
            if _market_open:
                _day_fraction = _mins_open / 390
                if _day_fraction < 0.75:
                    vol_ratio = vol_ratio / _day_fraction
            sma_50     = float(np.mean(closes[-50:]))  if len(closes) >= 50  else 0
            sma_200    = float(np.mean(closes[-200:])) if len(closes) >= 200 else 0
            rsi        = calc_rsi(closes)
            atr        = calc_atr(highs, lows, closes)
            atr_pct    = atr / price if price else 0
            adx        = calc_adx(highs, lows, closes)
            earnings_5d = False
            earnings_dt = None
            # Skip earnings check for ETFs and known non-equity tickers
            EARNINGS_SKIP = {"SPY","QQQ","IWM","DIA","MDY","VXX","EEM","EFA","FXI",
                             "EWJ","IEUR","XLK","XLF","XLE","XLV","XBI","XRT","XLU",
                             "XLI","XLP","TLT","GLD","SLV","USO","UUP","IJR","TQQQ",
                             "SQQQ","SPXL","SPXS","SOXL","SOXS","UVXY","SVXY","TNA",
                             "TZA","ARKK","ARKG","ARKW","ICLN","TAN","FAN","JETS",
                             "PBW","KRE","XHB","ITB","VNQ","XLRE"}
            if symbol not in EARNINGS_SKIP:
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
            headlines = []; has_neg = False; headline_score = 0
            articles = []
            hs = {}  # initialize before try in case news fetch fails partway
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
            t.adx_14  = adx
            t.iv_rank = 50.0; t.implied_move = round(atr_pct * 2, 3)
            t.earnings_within_5d = earnings_5d; t.earnings_date = earnings_dt
            t.has_negative_news  = has_neg
            t.headline_score     = headline_score
            t.headlines = headlines

            # Store macro trigger data from the already-computed headline score
            t.macro_triggers = hs.get("macro_triggers", []) if headlines else []
            t.macro_alert    = hs.get("macro_alert", False) if headlines else False
            if t.macro_alert:
                high_triggers = [m['trigger'] for m in t.macro_triggers if m['impact'] == 'high']
                log(f"  MACRO ALERT on {symbol}: {', '.join(high_triggers[:3])}")
            tickers[symbol] = t
            trend = "up" if price > sma_50 > sma_200 else "dn" if price < sma_50 else "->"
            earn  = " EARN" if earnings_5d else ""
            neg   = " NEG"  if has_neg   else ""
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
    spy = tickers.get("SPY")
    if spy and spy.price > spy.sma_50 > spy.sma_200:
        macro.market_regime = "trending_up"
    elif spy and spy.price < spy.sma_50 < spy.sma_200:
        macro.market_regime = "trending_down"
    elif macro.vix > 30:
        macro.market_regime = "volatile"
    elif macro.risk_score >= 1:
        macro.market_regime = "trending_up"
    else:
        macro.market_regime = "ranging"
    today   = date.today().isoformat()
    cutoff  = (date.today() + timedelta(days=1)).isoformat()
    FOMC = ["2026-06-17","2026-07-29","2026-09-16","2026-11-04","2026-12-16"]
    CPI  = ["2026-06-11","2026-07-14","2026-08-12","2026-09-11","2026-10-14"]
    JOBS = ["2026-06-05","2026-07-10","2026-08-07","2026-09-04","2026-10-02"]
    macro.fomc_24h = any(today <= d <= cutoff for d in FOMC)
    macro.cpi_24h  = any(today <= d <= cutoff for d in CPI)
    macro.jobs_24h = any(today <= d <= cutoff for d in JOBS)
    try:
        es_h = yf.Ticker("ES=F").history(period="2d")
        nq_h = yf.Ticker("NQ=F").history(period="2d")
        gc_h = yf.Ticker("GC=F").history(period="2d")
        es_c = (float(es_h["Close"].iloc[-1])-float(es_h["Close"].iloc[-2]))/float(es_h["Close"].iloc[-2])*100 if len(es_h)>=2 else 0
        nq_c = (float(nq_h["Close"].iloc[-1])-float(nq_h["Close"].iloc[-2]))/float(nq_h["Close"].iloc[-2])*100 if len(nq_h)>=2 else 0
        gc_c = (float(gc_h["Close"].iloc[-1])-float(gc_h["Close"].iloc[-2]))/float(gc_h["Close"].iloc[-2])*100 if len(gc_h)>=2 else 0
        avg  = (es_c + nq_c) / 2
        macro.risk_score = (2 if avg > 0.5 else -2 if avg < -0.5 else 1 if avg > 0.2 else -1 if avg < -0.2 else 0)
        if gc_c > 1.5: macro.risk_score -= 1
        if gc_c < -1.5: macro.risk_score += 1
        macro.futures_sentiment = (
            "RISK-ON"  if macro.risk_score >= 2 else
            "RISK-OFF" if macro.risk_score <= -2 else
            "MILDLY-RISK-ON"  if macro.risk_score == 1 else
            "MILDLY-RISK-OFF" if macro.risk_score == -1 else "NEUTRAL"
        )
        log(f"  Futures: ES={es_c:+.2f}% NQ={nq_c:+.2f}% Gold={gc_c:+.2f}% -> {macro.futures_sentiment}")
    except Exception as e:
        log(f"  Futures: {e}", "WARN")
    log(f"  Fed:{macro.fed_funds:.2f}% CPI:{macro.cpi_yoy:.1f}% "
        f"VIX:{macro.vix:.1f}({macro.vix_regime}) Regime:{macro.market_regime}")
    if macro.fomc_24h or macro.cpi_24h or macro.jobs_24h:
        events = [e for e,f in [("FOMC",macro.fomc_24h),("CPI",macro.cpi_24h),("Jobs",macro.jobs_24h)] if f]
        log(f"  HIGH-IMPACT EVENT TODAY: {events}", "WARN")
    log("  Scanning StockTwits sentiment...")
    try:
        macro.reddit_mentions = fetch_reddit_mentions(get_watchlist())
    except Exception as e:
        log(f"  StockTwits error: {e}", "WARN")
    try:
        macro.fear_greed = fetch_fear_greed()
        fg = macro.fear_greed
        if fg["score"] >= 80:
            macro.risk_score = max(macro.risk_score - 1, -3)
            log(f"  Extreme greed ({fg['score']:.0f}) — reducing risk score")
        elif fg["score"] <= 20:
            macro.risk_score = min(macro.risk_score + 1, 3)
            log(f"  Extreme fear ({fg['score']:.0f}) — potential opportunity")
    except Exception as e:
        log(f"  Fear & Greed error: {e}", "WARN")
    macro.sector_rotation = fetch_sector_rotation()
    macro.unusual_volume  = fetch_unusual_volume_scan()
    breadth = calc_market_breadth()
    macro.breadth = breadth
    log(f"  Market breadth: {breadth['breadth_pct']:.0f}% above SMA50 — {breadth['signal']}")
    if breadth["breadth_pct"] >= 70 and macro.risk_score >= 0:
        macro.risk_score = min(macro.risk_score + 1, 3)
    elif breadth["breadth_pct"] <= 30 and macro.risk_score <= 0:
        macro.risk_score = max(macro.risk_score - 1, -3)
    if is_opex_friday():
        log("  OpEx Friday — no new entries today", "WARN")

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
        lines.append("HIGH-IMPACT EVENT IN 24H")
    if m.sector_rotation:
        leading = [(s, d) for s, d in m.sector_rotation.items() if d["favored"]]
        lagging = [(s, d) for s, d in m.sector_rotation.items() if not d["favored"]]
        if leading: lines.append(f"Sectors leading: {', '.join(s for s,_ in leading[:3])}")
        if lagging: lines.append(f"Sectors lagging: {', '.join(s for s,_ in lagging[:3])}")
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
            f"Headline score: {t.headline_score:+.0f}/100",
            f"Earnings within 5d: {t.earnings_within_5d}",
        ]
        reddit = m.reddit_mentions.get(symbol, {})
        if reddit.get("mentions", 0) > 0:
            bias = "bullish" if reddit["bullish"] > reddit["bearish"] else "bearish" if reddit["bearish"] > reddit["bullish"] else "neutral"
            lines.append(f"Reddit: {reddit['mentions']} mentions ({bias}) {'TRENDING' if reddit.get('trending') else ''}")
        sym_sector = SECTOR_MAP.get(symbol)
        if sym_sector and sym_sector in m.sector_rotation:
            sr = m.sector_rotation[sym_sector]
            lines.append(f"Sector ({sym_sector}): {sr['change_pct']:+.1f}% today")
        if t.headlines:
            lines.append("Headlines:")
            for h in t.headlines[:4]: lines.append(f"  - {h}")
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
            # Lenient check — just needs price above SMA20 OR RSI not extreme
            trend_ok = price_1h > sma20_1h * 0.995  # within 0.5% of SMA20 is fine
            rsi_ok   = 35 <= rsi_1h <= 80            # wider range for ranging markets
            if not trend_ok and rsi_1h < 40:         # only hard-reject if both bad
                return False, f"1H weak: price ${price_1h:.2f} vs SMA20 ${sma20_1h:.2f}, RSI {rsi_1h:.1f}"
            if rsi_1h > 82:
                return False, f"1H overbought: RSI {rsi_1h:.1f}"
            return True, f"1H confirmed: RSI {rsi_1h:.1f}"
        else:
            trend_ok = price_1h < sma8_1h or price_1h < sma20_1h
            rsi_ok   = rsi_1h <= 60 or rsi_1h >= 70
            if not trend_ok:
                return False, f"1H trend bullish — short not confirmed"
            return True, f"1H short confirmed: RSI {rsi_1h:.1f}"
    except Exception as e:
        return True, f"1H check error ({e}) — skipping"


def is_good_trading_time() -> tuple[bool, str]:
    now = datetime.now(ET)
    h, m = now.hour, now.minute
    total_mins = h * 60 + m
    open_mins  = 9 * 60 + 30
    buffer_end = 9 * 60 + 40
    eod_start  = 15 * 60 + 30
    close_mins = 16 * 60 + 0
    if total_mins < open_mins:
        return False, "Pre-market — not scanning for new entries"
    if open_mins <= total_mins < buffer_end:
        return False, f"Opening 10 min buffer — waiting until 9:40 AM (now {now.strftime('%H:%M')} ET)"
    if eod_start <= total_mins < close_mins:
        return False, f"EOD 30 min window — no new entries after 3:30 PM (now {now.strftime('%H:%M')} ET)"
    if total_mins >= close_mins:
        return False, "Market closed"
    return True, f"Good trading window ({now.strftime('%H:%M')} ET)"


def scan_long(symbol) -> dict | None:
    t = tickers.get(symbol)
    if not t or t.price == 0: return None
    m = macro
    if t.earnings_within_5d: return None
    if t.has_negative_news and t.headline_score < -30: return None
    if m.fomc_24h or m.cpi_24h or m.jobs_24h: return None
    if macro.risk_score <= -3: return None
    if is_opex_friday(): return None
    if not (t.price > t.sma_50 > t.sma_200): return None
    pct_above_50 = (t.price - t.sma_50) / t.sma_50
    if pct_above_50 < 0.01: return None
    trend_score = min(100, 90 + pct_above_50 * 200)
    if not (RISK["rsi_min"] <= t.rsi_14 <= RISK["rsi_max"]): return None
    mom_score = max(50, 100 - abs(t.rsi_14 - 62) * 2.5)
    # No hard volume gate — vol_score handles it as soft penalty
    # Low volume reduces score but doesn't auto-reject strong setups
    vol_score = min(100, 55 + (t.vol_ratio - 1) * 25)
    if t.vol_ratio < RISK["volume_min_mult"]: vol_score *= 0.6
    if t.vol_ratio < 0.3: vol_score *= 0.4  # Very low volume = big penalty
    if t.atr_pct > RISK["atr_pct_max"]: return None
    if t.atr_pct < 0.005: return None
    atr_score = 100 if 0.01 <= t.atr_pct <= 0.025 else 75
    mac_score = (100 if m.market_regime == "trending_up" else
                 55  if m.market_regime == "ranging" else
                 45  if m.market_regime == "volatile" else 20)
    if m.vix_regime == "fear":       mac_score -= 35
    elif m.vix_regime == "elevated": mac_score -= 15
    if m.yield_curve < -0.5:         mac_score -= 10
    if mac_score < 25: return None
    ranging_mode = m.market_regime == "ranging"
    hl_score      = max(0, min(100, 50 + t.headline_score / 2))
    reddit        = m.reddit_mentions.get(symbol, {})
    reddit_adj    = (8  if reddit.get("trending") and reddit.get("bullish",0) > reddit.get("bearish",0)
                    else -10 if reddit.get("trending") and reddit.get("bearish",0) > reddit.get("bullish",0)
                    else 0)
    is_unusual    = any(u["symbol"] == symbol for u in m.unusual_volume)
    unusual_adj   = 5 if is_unusual else 0
    vwap_adj,     vwap_note     = get_vwap_score(symbol, t.price, "long")
    sector_adj,   sector_note   = get_sector_momentum_score(symbol, "long")
    earnings_adj, earnings_note = get_post_earnings_score(symbol)
    insider_adj,  insider_note  = get_insider_score(symbol)
    options_adj,  options_note  = get_options_flow_score(symbol, "long")
    congress_adj, congress_note = get_congress_score(symbol)
    adx_adj = (10 if t.adx_14 >= 40 else 5 if t.adx_14 >= 25 else -8 if t.adx_14 < 20 else 0)
    criteria = (trend_score  * 0.25 +
                mom_score    * 0.20 +
                vol_score    * 0.20 +
                atr_score    * 0.15 +
                mac_score    * 0.15 +
                hl_score     * 0.05 +
                reddit_adj + unusual_adj +
                vwap_adj + sector_adj + earnings_adj +
                insider_adj + options_adj + congress_adj + adx_adj)
    min_criteria = 58  # Same threshold all regimes — mac_score already penalizes ranging
    if criteria < min_criteria: return None
    mtf_ok, mtf_reason = get_1h_confirmation(symbol, "long")
    if not mtf_ok:
        log(f"  {symbol} LONG rejected: {mtf_reason}")
        return None
    atr    = t.atr_14 or t.price * 0.015
    stop   = round(t.price - RISK["stop_loss_atr_mult"]   * atr, 2)
    target = round(t.price + RISK["take_profit_atr_mult"] * atr, 2)
    rr     = round((target - t.price) / (t.price - stop), 2) if t.price > stop else 0
    if rr < 1.5: return None
    notes = [f"Trend up {pct_above_50:.1%} above SMA50",
             f"RSI={t.rsi_14:.1f} Vol={t.vol_ratio:.1f}x ATR={t.atr_pct:.2%}",
             mtf_reason]
    for n in [vwap_note, sector_note, earnings_note, insider_note, options_note, congress_note]:
        if n: notes.append(n)
    if t.headline_score > 20: notes.append(f"Headlines bullish ({t.headline_score:+.0f})")
    if reddit.get("trending"):  notes.append("StockTwits trending")
    if is_unusual:              notes.append(f"Unusual volume {t.vol_ratio:.1f}x")
    return {
        "symbol":   symbol, "type": "stock_long", "direction": "long",
        "entry":    t.price, "stop": stop, "target": target, "rr": rr,
        "criteria": round(criteria, 1),
        "scores":   {"trend": trend_score, "momentum": mom_score,
                     "volume": vol_score, "atr": atr_score, "macro": mac_score},
        "notes":          notes,
        "headline_score": t.headline_score,
        "reddit_trending": reddit.get("trending", False),
    }


def scan_short(symbol) -> dict | None:
    """
    Short signal scanner — two modes:

    MODE A — REGIME SHORT (original)
        Fires when macro regime is trending_down or volatile.
        RSI < 55 in downtrend, or RSI > 72 overbought reversal.
        Strict: requires risk_score <= -1, VIX not low.

    MODE B — INTRADAY BREAKDOWN SHORT
        Fires even inside a bull market when there is a confirmed intraday
        selloff (SPY down >= 1.5%) with risk-off futures and elevated VIX.
        Catches days where a macro catalyst (KOSPI crash, chip selloff, Fed
        shock) drives sharp intraday moves the regime detector misses.
    """
    t = tickers.get(symbol)
    if not t or t.price == 0: return None
    m = macro

    # Hard gates — always apply
    if t.earnings_within_5d:               return None
    if m.fomc_24h or m.cpi_24h or m.jobs_24h: return None
    if t.headline_score > 60:              return None

    spy = tickers.get("SPY")

    # Intraday breakdown conditions
    spy_down_hard    = spy and spy.change_pct <= -1.5
    spy_down_extreme = spy and spy.change_pct <= -2.5
    vix_not_low      = m.vix_regime in ("normal", "elevated", "fear")
    futures_bearish  = m.risk_score <= -1
    vix_elevated     = m.vix_regime in ("elevated", "fear")

    intraday_breakdown   = spy_down_hard and vix_not_low and futures_bearish
    regime_allows_shorts = m.market_regime in ("trending_down", "volatile")

    if not regime_allows_shorts and not intraday_breakdown:
        return None

    # ── MODE B — INTRADAY BREAKDOWN ──────────────────────────────────────────
    if intraday_breakdown and not regime_allows_shorts:
        if t.change_pct > -0.3:
            return None  # Stock not participating in selloff

        confirmed_down   = t.price < t.sma_50
        overbought_break = t.rsi_14 >= 60 and t.change_pct < -1
        high_beta_break  = t.change_pct <= -2.0

        if not (confirmed_down or overbought_break or high_beta_break):
            return None

        if t.vol_ratio < 1.2:
            return None

        if t.atr_pct > RISK["atr_pct_max"] * 1.5:
            return None

        pct_below_50 = (t.sma_50 - t.price) / t.sma_50 if t.price < t.sma_50 else 0
        trend_score  = min(100, 65 + pct_below_50 * 300) if confirmed_down else 55
        mom_score    = min(100, 50 + abs(t.change_pct) * 10)
        vol_score    = min(100, 55 + (t.vol_ratio - 1.2) * 30)
        atr_score    = 100 if 0.01 <= t.atr_pct <= 0.035 else 75
        mac_score = (
            100 if spy_down_extreme and vix_elevated    else
            85  if spy_down_extreme                     else
            75  if spy_down_hard    and vix_elevated    else
            65
        )
        if m.risk_score <= -2: mac_score = min(100, mac_score + 10)
        hl_boost = max(0, -t.headline_score / 5)

        criteria = (
            trend_score * 0.25 +
            mom_score   * 0.25 +
            vol_score   * 0.20 +
            atr_score   * 0.15 +
            mac_score   * 0.15 +
            hl_boost
        )
        if criteria < 52: return None

        mtf_ok, mtf_reason = get_1h_confirmation(symbol, "short")
        if not mtf_ok:
            log(f"  {symbol} BREAKDOWN SHORT rejected: {mtf_reason}")
            return None

        atr    = t.atr_14 or t.price * 0.015
        stop   = round(t.price + RISK["stop_loss_atr_mult"] * atr, 2)
        target = round(t.price - RISK["take_profit_atr_mult"] * atr, 2)
        rr     = round((t.price - target) / (stop - t.price), 2) if stop > t.price else 0
        if rr < 1.5: return None

        spy_pct = spy.change_pct if spy else 0
        notes = [
            f"Breakdown short: SPY {spy_pct:+.1f}% | VIX {m.vix:.1f} ({m.vix_regime})",
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

    # ── MODE A — REGIME SHORT (original logic) ────────────────────────────────
    if not ENABLE_REGIME_SHORTS:      return None  # backtested ~20% WR, -EV drag
    if m.market_regime == "unknown":  return None
    if macro.risk_score > -1:         return None
    if m.vix_regime == "low":         return None
    if macro.risk_score >= 2:         return None

    confirmed_down = t.price < t.sma_50 < t.sma_200
    overbought_rev = t.rsi_14 > 72 and t.price > t.sma_50
    if not confirmed_down and not overbought_rev: return None

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
    mac_score = (100 if m.market_regime == "trending_down" else
                 90  if m.market_regime == "volatile" else
                 70  if m.market_regime == "ranging" else 30)
    if m.vix_regime in ("fear", "elevated"): mac_score = min(100, mac_score + 10)
    criteria = (trend_score*0.25 + mom_score*0.20 + vol_score*0.20 +
                atr_score*0.15 + mac_score*0.20)
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
        "symbol": symbol, "type": stype, "direction": "short",
        "entry": t.price, "stop": stop, "target": target, "rr": rr,
        "criteria": round(criteria, 1),
        "scores": {"trend": trend_score, "momentum": mom_score,
                   "volume": vol_score, "atr": atr_score, "macro": mac_score},
        "notes": [f"Short RSI={t.rsi_14:.1f} Vol={t.vol_ratio:.1f}x ATR={t.atr_pct:.2%}",
                  mtf_reason],
        "headline_score":  t.headline_score,
        "reddit_trending": m.reddit_mentions.get(symbol, {}).get("trending", False),
    }


def scan_diagnostic(symbol: str, direction: str) -> dict | None:
    """
    Returns a near-miss dict if the ticker passes hard gates but misses
    criteria threshold. Saved to near_miss.json so we can see what's close.
    """
    t = tickers.get(symbol)
    if not t or t.price == 0: return None
    m = macro
    if t.earnings_within_5d: return None
    if m.fomc_24h or m.cpi_24h or m.jobs_24h: return None

    if direction == "long":
        if t.has_negative_news and t.headline_score < -30: return None
        if macro.risk_score <= -3: return None
        if not (t.price > t.sma_50 > t.sma_200): return None
        pct_above_50 = (t.price - t.sma_50) / t.sma_50
        if pct_above_50 < 0.01: return None
        rsi_ok = RISK["rsi_min"] <= t.rsi_14 <= RISK["rsi_max"]
        vol_ok = t.vol_ratio >= RISK["volume_min_mult"]
        atr_ok = t.atr_pct <= RISK["atr_pct_max"]
        # Compute partial criteria
        trend_score = min(100, 90 + pct_above_50 * 200)
        mom_score   = max(50, 100 - abs(t.rsi_14 - 62) * 2.5)
        vol_score   = min(100, 55 + (t.vol_ratio - 1) * 25)
        atr_score   = 100 if 0.01 <= t.atr_pct <= 0.025 else 75
        mac_score   = (100 if m.market_regime == "trending_up" else
                       55  if m.market_regime == "ranging" else
                       45  if m.market_regime == "volatile" else 20)
        criteria = (trend_score*0.25 + mom_score*0.20 + vol_score*0.20 +
                    atr_score*0.15 + mac_score*0.15)
        blockers = []
        if not rsi_ok:  blockers.append(f"RSI {t.rsi_14:.0f} (need {RISK['rsi_min']}-{RISK['rsi_max']})")
        if not vol_ok:  blockers.append(f"Vol {t.vol_ratio:.1f}x (need {RISK['volume_min_mult']}x)")
        if not atr_ok:  blockers.append(f"ATR {t.atr_pct:.2%} (max {RISK['atr_pct_max']:.2%})")
        if criteria < 40: return None  # too far off, not worth logging
        # Run MTF check so diagnostic shows real reason for scan_long rejection
        if not blockers:
            mtf_ok, mtf_reason = get_1h_confirmation(symbol, direction)
            if not mtf_ok:
                blockers.append(f"1H: {mtf_reason}")
        return {
            "symbol": symbol, "direction": direction,
            "criteria": round(criteria, 1), "rsi": t.rsi_14,
            "vol": t.vol_ratio, "change_pct": t.change_pct,
            "blockers": blockers or ["full adjustments push below 58"],
            "scanned_at": datetime.now(ET).strftime("%H:%M"),
        }
    return None


def save_near_misses(near_misses: list):
    """Persist top near-misses for dashboard display."""
    nm_file = STATE_DIR / "near_miss.json"
    nm_file.write_text(json.dumps({
        "updated_at": datetime.now(ET).isoformat(),
        "near_misses": sorted(near_misses, key=lambda x: x["criteria"], reverse=True)[:15],
    }, indent=2))


def scan_all() -> list:
    session = get_market_session()
    tod_ok, tod_reason = is_good_trading_time()
    if not tod_ok:
        log(f"Scan skipped: {tod_reason}")
        return []
    log(f"Scanning {len(get_watchlist())} tickers | {session} | Regime:{macro.market_regime} | VIX:{macro.vix:.1f}")
    signals    = []
    near_misses = []
    for sym in get_watchlist():
        for fn in (scan_long, scan_short):
            s = fn(sym)
            if s:
                signals.append(s)
                log(f"  {'up' if s['direction']=='long' else 'dn'} SIGNAL: "
                    f"{s['symbol']} [{s['criteria']:.0f}] entry:${s['entry']:.2f} "
                    f"stop:${s['stop']:.2f} target:${s['target']:.2f} R/R:{s['rr']:.2f}")
            else:
                # Check if it was close (near-miss diagnostic)
                direction = "long" if fn == scan_long else "short"
                nm = scan_diagnostic(sym, direction)
                if nm:
                    near_misses.append(nm)

    signals.sort(key=lambda x: x["criteria"], reverse=True)
    log(f"Scan complete: {len(signals)} signal(s) | {len(near_misses)} near-misses")

    # Save near-misses for dashboard
    if near_misses:
        save_near_misses(near_misses)
        top3 = sorted(near_misses, key=lambda x: x["criteria"], reverse=True)[:3]
        log("Near-misses: " + " | ".join(
            f"{n['symbol']} {n['criteria']:.0f} ({n['blockers'][0] if n['blockers'] else 'criteria'})"
            for n in top3
        ))

    return signals


# ══════════════════════════════════════════════════════════════════════════════
# PRE-MARKET GAP SCANNER
# ══════════════════════════════════════════════════════════════════════════════

def scan_premarket_gaps() -> list:
    now = datetime.now(ET)
    if not (now.hour == 9 and now.minute < 30):
        return []
    log("Pre-market gap scan...")
    gaps = []
    for symbol in get_watchlist():
        t = tickers.get(symbol)
        if not t or t.price == 0:
            continue
        try:
            r = requests.get(
                f"{ALPACA_DATA}/v2/stocks/{symbol}/quotes/latest",
                headers={"APCA-API-KEY-ID": ALPACA_KEY,
                         "APCA-API-SECRET-KEY": ALPACA_SECRET},
                params={"feed": "iex"},
                timeout=6
            )
            if not r.ok: continue
            quote     = r.json().get("quote", {})
            ask       = float(quote.get("ap", 0))
            bid       = float(quote.get("bp", 0))
            pre_price = (ask + bid) / 2 if ask > 0 and bid > 0 else 0
            if pre_price <= 0: continue
            prev_close = t.price
            gap_pct    = (pre_price - prev_close) / prev_close * 100
            if abs(gap_pct) < 1.5: continue
            hl_score = t.headline_score
            direction = None
            conviction = 0
            if gap_pct >= 2.0:
                if hl_score >= 20:
                    direction  = "long"
                    conviction = min(100, 60 + gap_pct * 3 + hl_score / 2)
                elif hl_score <= -20:
                    direction  = "short"
                    conviction = min(100, 55 + abs(hl_score) / 2)
                else:
                    direction  = "long"
                    conviction = min(100, 50 + gap_pct * 2)
            elif gap_pct <= -2.0:
                if hl_score <= -20:
                    direction  = "short"
                    conviction = min(100, 60 + abs(gap_pct) * 3)
                elif hl_score >= 20:
                    direction  = "long"
                    conviction = min(100, 55 + hl_score / 2)
            if direction and conviction >= 55:
                atr   = t.atr_14 or pre_price * 0.015
                if direction == "long":
                    stop   = round(pre_price - 1.5 * atr, 2)
                    target = round(pre_price + 2.5 * atr, 2)
                else:
                    stop   = round(pre_price + 1.5 * atr, 2)
                    target = round(pre_price - 2.5 * atr, 2)
                rr = round(abs(target - pre_price) / abs(pre_price - stop), 2) if abs(pre_price - stop) > 0 else 0
                if rr < 1.5: continue
                gaps.append({
                    "symbol":     symbol, "type": "premarket_gap",
                    "direction":  direction, "gap_pct": round(gap_pct, 2),
                    "pre_price":  pre_price, "prev_close": prev_close,
                    "entry":      pre_price, "stop": stop, "target": target,
                    "rr":         rr, "criteria": round(conviction, 1),
                    "scores":     {"trend": 70, "momentum": conviction,
                                   "volume": 70, "atr": 70, "macro": 70},
                    "notes":      [f"Gap {gap_pct:+.1f}% pre-market",
                                   f"Headlines: {hl_score:+.0f}"],
                    "headline_score": hl_score, "reddit_trending": False,
                })
                log(f"  GAP: {symbol} {direction.upper()} {gap_pct:+.1f}% @ ${pre_price:.2f}")
        except: pass
    gaps.sort(key=lambda x: x["criteria"], reverse=True)
    if gaps:
        gap_str = "\n".join([
            f"{g['gap_pct']:+.1f}% {g['symbol']} -> {g['direction'].upper()}"
            for g in gaps[:5]
        ])
        _tg(f"Pre-Market Gap Scan\n{len(gaps)} setup(s):\n{gap_str}")
    return gaps[:5]


# ══════════════════════════════════════════════════════════════════════════════
# VWAP + SECTOR + POST-EARNINGS + INSIDER + OPTIONS + CONGRESS
# ══════════════════════════════════════════════════════════════════════════════

def calculate_vwap(symbol: str) -> float | None:
    try:
        ticker = yf.Ticker(symbol)
        bars   = ticker.history(period="1d", interval="5m")
        if bars.empty or len(bars) < 5: return None
        typical_price = (bars["High"] + bars["Low"] + bars["Close"]) / 3
        vwap = (typical_price * bars["Volume"]).cumsum() / bars["Volume"].cumsum()
        return float(vwap.iloc[-1])
    except: return None

def get_vwap_score(symbol: str, price: float, direction: str) -> tuple[float, str]:
    vwap = calculate_vwap(symbol)
    if vwap is None: return 0, "VWAP unavailable"
    pct_from_vwap = (price - vwap) / vwap * 100
    if direction == "long":
        if price > vwap * 1.002:  return 8,  f"Above VWAP ${vwap:.2f} (+{pct_from_vwap:.1f}%)"
        elif price > vwap * 0.998: return 2, f"At VWAP ${vwap:.2f}"
        else:                      return -10, f"Below VWAP ${vwap:.2f} ({pct_from_vwap:.1f}%)"
    else:
        if price < vwap * 0.998:  return 8,  f"Below VWAP ${vwap:.2f} ({pct_from_vwap:.1f}%)"
        elif price < vwap * 1.002: return 2, f"At VWAP ${vwap:.2f}"
        else:                      return -10, f"Above VWAP ${vwap:.2f} (+{pct_from_vwap:.1f}%)"

def get_sector_momentum_score(symbol: str, direction: str) -> tuple[float, str]:
    sym_sector = SECTOR_MAP.get(symbol)
    if not sym_sector: return 0, ""
    sector_etf = None
    for etf, sector in SECTOR_MAP.items():
        if sector == sym_sector and etf in tickers:
            sector_etf = etf; break
    if not sector_etf: return 0, ""
    etf_data = tickers.get(sector_etf)
    if not etf_data: return 0, ""
    etf_change = etf_data.change_pct
    if direction == "long":
        if etf_change >= 1.5:  return 12, f"{sector_etf} +{etf_change:.1f}% — sector tailwind"
        elif etf_change >= 0.5: return 5, f"{sector_etf} +{etf_change:.1f}% — mild tailwind"
        elif etf_change <= -1.5: return -15, f"{sector_etf} {etf_change:.1f}% — sector headwind"
        elif etf_change <= -0.5: return -8, f"{sector_etf} {etf_change:.1f}% — mild headwind"
    else:
        if etf_change <= -1.5: return 12, f"{sector_etf} {etf_change:.1f}% — sector falling"
        elif etf_change >= 1.5: return -12, f"{sector_etf} +{etf_change:.1f}% — sector rising"
    return 0, ""

EARNINGS_BEATS: dict = {}

def get_post_earnings_score(symbol: str) -> tuple[float, str]:
    beat = EARNINGS_BEATS.get(symbol)
    if not beat: return 0, ""
    try:
        from datetime import date as _date
        beat_date = _date.fromisoformat(beat["date"])
        days_since = (_date.today() - beat_date).days
        if 2 <= days_since <= 7 and beat.get("beat"):
            surprise = beat.get("surprise_pct", 0)
            score = 15 if surprise >= 10 else 10 if surprise >= 5 else 7
            return score, f"Earnings beat {days_since}d ago (+{surprise:.0f}% surprise)"
        elif days_since > 7:
            EARNINGS_BEATS.pop(symbol, None)
    except: pass
    return 0, ""

def load_earnings_beats_from_digest():
    digest_file = STATE_DIR / "research_digest.json"
    if not digest_file.exists(): return
    try:
        digest   = json.loads(digest_file.read_text())
        earnings = digest.get("earnings", [])
        for e in earnings:
            sym = e.get("symbol")
            if sym:
                text = " ".join(str(h) for h in e.get("highlights", []))
                is_beat = any(kw in text.lower() for kw in
                             ["beat", "exceeded", "surpassed", "above estimates",
                              "raised guidance", "record revenue"])
                EARNINGS_BEATS[sym] = {
                    "beat": is_beat, "date": e.get("date", ""),
                    "surprise_pct": 5.0 if is_beat else -5.0,
                }
    except Exception as e:
        log(f"Earnings beats load error: {e}", "WARN")

INSIDER_SIGNALS: dict = {}

def fetch_insider_filings(symbols: list):
    log("Fetching SEC insider filings...")
    headers = {"User-Agent": "Boticus Research bot@boticus.app", "Accept": "application/json"}
    try:
        r = requests.get(
            "https://unusualwhales.com/api/insider/recent",
            headers={"User-Agent": "Mozilla/5.0"}, timeout=10
        )
        if r.ok:
            filings = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            for f in filings[:50]:
                sym      = f.get("ticker", "").upper()
                tx_type  = f.get("transaction_type", "").lower()
                value    = float(f.get("value", 0) or 0)
                title    = f.get("title", "")
                is_exec     = any(t in title.upper() for t in
                                  ["CEO","CFO","COO","CTO","PRESIDENT","DIRECTOR","CHAIRMAN"])
                is_purchase = "purchase" in tx_type or "buy" in tx_type
                if sym in symbols and is_exec and is_purchase and value >= 50000:
                    INSIDER_SIGNALS[sym] = {
                        "buyer": title, "value": value,
                        "date": f.get("date", ""), "source": "Unusual Whales",
                    }
                    log(f"  Insider buy: {sym} — {title} ${value:,.0f}")
    except Exception as e:
        log(f"  Unusual Whales insider: {e}", "WARN")

def get_insider_score(symbol: str) -> tuple[float, str]:
    insider = INSIDER_SIGNALS.get(symbol)
    if not insider: return 0, ""
    value = insider.get("value", 0)
    buyer = insider.get("buyer", "Insider")
    if value >= 500000: return 20, f"Insider {buyer} bought ${value:,.0f}"
    elif value >= 100000: return 12, f"Insider {buyer} bought ${value:,.0f}"
    elif value >= 50000:  return 7,  f"Insider purchase ${value:,.0f}"
    return 3, "Insider activity flagged"

UNUSUAL_OPTIONS: dict = {}

def fetch_unusual_options_flow():
    log("Fetching unusual options flow...")
    headers = {"User-Agent": "Mozilla/5.0 (compatible; boticus/1.0)"}
    try:
        r = requests.get(
            "https://unusualwhales.com/api/optionAlerts/recent",
            headers=headers, timeout=10
        )
        if r.ok:
            alerts = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
            for alert in alerts[:100]:
                sym       = alert.get("ticker", "").upper()
                premium   = float(alert.get("premium", 0) or 0)
                put_call  = alert.get("put_call", "").lower()
                sentiment = alert.get("sentiment", "").lower()
                if sym in get_watchlist() and premium >= 100000:
                    UNUSUAL_OPTIONS[sym] = {
                        "type": put_call, "premium": premium,
                        "sentiment": sentiment, "date": datetime.now(ET).strftime("%Y-%m-%d"),
                    }
                    log(f"  Unusual options: {sym} {put_call.upper()} ${premium:,.0f}")
    except Exception as e:
        log(f"  Unusual options error: {e}", "WARN")

def get_options_flow_score(symbol: str, direction: str) -> tuple[float, str]:
    opt = UNUSUAL_OPTIONS.get(symbol)
    if not opt: return 0, ""
    opt_type  = opt.get("type", "")
    premium   = opt.get("premium", 0)
    sentiment = opt.get("sentiment", "")
    is_bullish = "call" in opt_type or "bullish" in sentiment
    is_bearish = "put"  in opt_type or "bearish" in sentiment
    prem_str = f"${premium/1000:.0f}k" if premium >= 1000 else ""
    if direction == "long" and is_bullish:
        score = 15 if premium >= 500000 else 10 if premium >= 200000 else 7
        return score, f"Unusual CALL flow {prem_str}"
    elif direction == "long" and is_bearish:
        return -12, f"Unusual PUT flow {prem_str} — smart money bearish"
    elif direction == "short" and is_bearish:
        return 15 if premium >= 500000 else 10, f"Unusual PUT flow {prem_str}"
    elif direction == "short" and is_bullish:
        return -12, f"Unusual CALL flow — smart money bullish"
    return 0, ""

CONGRESS_TRADES: dict = {}

def fetch_congress_trades():
    log("Fetching congressional trading data...")
    try:
        r = requests.get(
            "https://unusualwhales.com/api/congress/recent",
            headers={"User-Agent": "Mozilla/5.0 (compatible; boticus/1.0)"}, timeout=10
        )
        if not r.ok: return
        trades = r.json() if isinstance(r.json(), list) else r.json().get("data", [])
        wl     = set(get_watchlist())
        for trade in trades[:100]:
            sym      = trade.get("ticker", "").upper()
            tx_type  = trade.get("type", "").lower()
            amount   = trade.get("amount", "")
            name     = trade.get("representative", trade.get("senator", ""))
            party    = trade.get("party", "")
            tx_date  = trade.get("transaction_date", "")
            if sym not in wl: continue
            if "purchase" not in tx_type and "buy" not in tx_type: continue
            amount_val = 0
            if "$" in str(amount):
                nums = re.findall(r'[\d]+', str(amount).replace(",",""))
                if len(nums) >= 2: amount_val = (int(nums[0]) + int(nums[-1])) / 2
                elif len(nums) == 1: amount_val = int(nums[0])
            if amount_val >= 15000:
                CONGRESS_TRADES[sym] = {
                    "politician": name, "party": party,
                    "type": tx_type, "amount": amount_val, "date": tx_date,
                }
                log(f"  Congress buy: {sym} — {name} ({party}) ~${amount_val:,.0f}")
    except Exception as e:
        log(f"  Congress trades error: {e}", "WARN")

def get_congress_score(symbol: str) -> tuple[float, str]:
    trade = CONGRESS_TRADES.get(symbol)
    if not trade: return 0, ""
    name   = trade.get("politician", "Congress member")
    amount = trade.get("amount", 0)
    score  = 18 if amount >= 250000 else 12 if amount >= 50000 else 8
    return score, f"Congress {name} bought ~${amount:,.0f}"


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
    "Sector rotation matters — don't fight the tape.\n"
    "Minimum 1.5:1 R/R required. Protect capital first. Never rationalize a bad trade."
)

def score_signal(sig: dict) -> dict:
    log(f"AI scoring {sig['symbol']} ({sig['direction'].upper()})...")
    memory   = build_pattern_memory()
    context  = build_context(sig["symbol"])
    research = load_research_digest()
    fed_ins  = load_fed_insights()
    s        = sig["scores"]
    prompt   = (
        f"PATTERN MEMORY:\n{memory}\n\n"
        + (f"COMMUNITY RESEARCH:\n{research}\n\n" if research else "")
        + (f"MANUALLY FED INSIGHTS:\n{fed_ins}\n\n" if fed_ins else "")
        + f"MARKET CONTEXT:\n{context}\n\n"
        f"SIGNAL: {sig['symbol']} {sig['direction'].upper()} {sig['type']}\n"
        f"Entry:${sig['entry']:.2f} Stop:${sig['stop']:.2f} "
        f"Target:${sig['target']:.2f} R/R:{sig['rr']:.2f}\n"
        f"Criteria: Trend={s['trend']:.0f} Mom={s['momentum']:.0f} "
        f"Vol={s['volume']:.0f} ATR={s['atr']:.0f} Macro={s['macro']:.0f} "
        f"TOTAL={sig['criteria']:.0f}\n"
        f"Notes: {' | '.join(sig['notes'][:5])}"
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
        e  = "APPROVED" if approved else "REJECTED"
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

def execute_signal(sig: dict, equity: float) -> tuple[bool, int]:
    if not sig.get("approved"): return False, 0
    rps    = abs(sig["entry"] - sig["stop"])
    if rps <= 0: return False, 0
    kelly_pct = kelly_size(equity, sig.get("rr", 1.5), sig.get("ai_score", 65))
    feedback  = load_feedback()
    blend     = 0.8 if len(feedback) >= 20 else 0.5
    risk_pct  = blend * kelly_pct + (1 - blend) * RISK["max_risk_per_trade_pct"]
    log(f"  Kelly: {kelly_pct:.2%} | Fixed: {RISK['max_risk_per_trade_pct']:.2%} | Blend: {risk_pct:.2%}")
    pos_pct    = SMALL_ACCT_MAX_POS_PCT if SMALL_ACCT_EQUITY > 0 else RISK["max_position_pct"]
    cap_shares = int(equity * pos_pct / sig["entry"])
    if cap_shares < 1:
        # 1 share already exceeds the position cap — cannot size this ticker sanely.
        log(f"  SKIP {sig['symbol']}: 1 share @ ${sig['entry']:.2f} exceeds "
            f"{pos_pct:.0%} cap of ${equity:,.0f} account")
        return False, 0
    risk_shares = int(equity * risk_pct / rps)
    shares = max(1, min(risk_shares, cap_shares))
    shares = max(1, min(cap_shares, int(shares * sig.get("size_adj", 1.0))))
    side   = "buy" if sig["direction"] == "long" else "sell"
    order  = {
        "symbol":        sig["symbol"],
        "qty":           str(shares),
        "side":          side,
        "type":          "market",
        "time_in_force": "gtc",   # FIX: was "day" — bracket legs now survive past 4 PM
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
            log(f"  ORDER SENT: {sig['symbol']} {side.upper()} x{shares} "
                f"@ ${sig['entry']:.2f} | stop:${sig['stop']:.2f} "
                f"target:${sig['target']:.2f} | id:{order_id}")
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
            return True, shares
        else:
            log(f"  Order failed {r.status_code}: {r.text[:150]}", "ERROR")
    except Exception as e:
        log(f"  Execution error: {e}", "ERROR")
    return False, 0

def check_daily_loss(equity: float) -> bool:
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


# ══════════════════════════════════════════════════════════════════════════════
# POSITION MONITOR
# ══════════════════════════════════════════════════════════════════════════════

def close_position_market(symbol: str, qty: int, side: str, reason: str) -> bool:
    try:
        r = requests.post(
            f"{ALPACA_BASE}/v2/orders",
            headers=ALPACA_HEADERS,
            json={"symbol": symbol, "qty": str(abs(qty)),
                  "side": side, "type": "market", "time_in_force": "day"},
            timeout=10
        )
        if r.status_code in (200, 201):
            log(f"  CLOSED {symbol} x{qty} — {reason}")
            return True
        else:
            log(f"  Close failed {symbol}: {r.status_code} {r.text[:100]}", "ERROR")
    except Exception as e:
        log(f"  Close error {symbol}: {e}", "ERROR")
    return False


def update_stop_loss(symbol: str, order_id: str, new_stop: float) -> bool:
    """
    FIX: Find the actual STOP LEG of the bracket order and patch that,
    not the parent order ID (which previously caused silent failures).
    """
    try:
        # Step 1: Fetch parent order to get legs
        r = requests.get(
            f"{ALPACA_BASE}/v2/orders/{order_id}",
            headers=ALPACA_HEADERS, timeout=8
        )
        if r.ok:
            parent = r.json()
            legs   = parent.get("legs") or []
            stop_leg_id = None
            for leg in legs:
                leg_type = leg.get("type", "").lower()
                if "stop" in leg_type:
                    stop_leg_id = leg.get("id")
                    break
            if stop_leg_id:
                r2 = requests.patch(
                    f"{ALPACA_BASE}/v2/orders/{stop_leg_id}",
                    headers=ALPACA_HEADERS,
                    json={"stop_price": str(round(new_stop, 2))},
                    timeout=10
                )
                if r2.status_code in (200, 201):
                    log(f"  Stop leg updated: {symbol} -> ${new_stop:.2f}")
                    return True
                else:
                    log(f"  Stop leg patch failed {symbol}: {r2.status_code}", "WARN")
            else:
                # No leg found in parent — scan open orders by symbol
                log(f"  No stop leg in parent for {symbol} — scanning open orders", "WARN")
                r3 = requests.get(
                    f"{ALPACA_BASE}/v2/orders",
                    headers=ALPACA_HEADERS,
                    params={"symbols": symbol, "status": "open", "limit": 20},
                    timeout=8
                )
                if r3.ok:
                    for o in r3.json():
                        if o.get("type") in ("stop", "stop_limit") and \
                           o.get("symbol") == symbol:
                            r4 = requests.patch(
                                f"{ALPACA_BASE}/v2/orders/{o['id']}",
                                headers=ALPACA_HEADERS,
                                json={"stop_price": str(round(new_stop, 2))},
                                timeout=10
                            )
                            if r4.status_code in (200, 201):
                                log(f"  Stop updated (scan): {symbol} -> ${new_stop:.2f}")
                                return True
        else:
            log(f"  Parent order fetch failed {symbol}: {r.status_code}", "WARN")
    except Exception as e:
        log(f"  Stop update error {symbol}: {e}", "WARN")
    return False


# ══════════════════════════════════════════════════════════════════════════════
# OPTIONS EXECUTION  (options-only mode, gated by OPTIONS_ENABLED, default off)
#
# Long calls for long signals, long puts for shorts — approval level 1, defined
# risk = premium paid. Alpaca has NO bracket/OCO on options, so stop & target are
# SYNTHETIC: monitor_option_positions() watches the UNDERLYING price and market-
# sells the contract when the signal's stop or target is hit, plus a theta/time
# stop and EOD close. Option trades live in trades.json flagged is_option=True
# with option_symbol=OCC; every equity monitor/sync path skips them.
# ══════════════════════════════════════════════════════════════════════════════

def parse_occ_symbol(occ: str) -> dict | None:
    """Parse an OCC symbol e.g. 'SPY260725C00550000' -> underlying/expiry/type/
    strike. Pure — unit-tested in test_options.py."""
    m = re.match(r"^([A-Z]+)(\d{2})(\d{2})(\d{2})([CP])(\d{8})$", (occ or "").strip())
    if not m:
        return None
    root, yy, mm, dd, cp, strike8 = m.groups()
    try:
        expiry = date(2000 + int(yy), int(mm), int(dd))
    except ValueError:
        return None
    return {"underlying": root, "expiry": expiry,
            "type": "call" if cp == "C" else "put",
            "strike": int(strike8) / 1000.0}


def _pick_expiry(expiries: list, today: date, min_dte: int, max_dte: int):
    """Nearest expiry with DTE in [min_dte, max_dte]; else closest future expiry
    to the window. Pure — unit-tested."""
    dated = sorted(set(expiries))
    in_window = [e for e in dated if min_dte <= (e - today).days <= max_dte]
    if in_window:
        return in_window[0]
    future = [e for e in dated if (e - today).days >= 1]
    return min(future, key=lambda e: abs((e - today).days - min_dte)) if future else None


def _pick_strike(strikes: list, spot: float, otype: str, moneyness: str):
    """'itm' = nearest in-the-money strike; 'atm' = nearest to spot. Pure."""
    strikes = sorted(set(strikes))
    if not strikes:
        return None
    if moneyness == "atm":
        return min(strikes, key=lambda s: abs(s - spot))
    if otype == "call":
        itm = [s for s in strikes if s <= spot]
        return max(itm) if itm else min(strikes)
    itm = [s for s in strikes if s >= spot]
    return min(itm) if itm else max(strikes)


def _size_contracts(cap_dollars: float, ask_per_share: float) -> int:
    """Contracts affordable within the dollar cap (each contract = 100 shares)."""
    if ask_per_share <= 0:
        return 0
    return int(cap_dollars / (ask_per_share * 100.0))


def option_exit_decision(direction: str, u_price: float,
                         stop: float, target: float) -> str | None:
    """Synthetic exit on the UNDERLYING: 'target', 'stop', or None. Pure.
    Long signal (call): stop < entry < target. Short signal (put): target <
    entry < stop."""
    if u_price <= 0:
        return None
    if direction == "long":
        if u_price >= target: return "target"
        if u_price <= stop:   return "stop"
    else:
        if u_price <= target: return "target"
        if u_price >= stop:   return "stop"
    return None


def options_trading_enabled() -> bool:
    """Preflight: confirm the Alpaca account is approved for options (level >= 1)."""
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/account", headers=ALPACA_HEADERS, timeout=8)
        if r.ok:
            a = r.json()
            lvl = a.get("options_trading_level", a.get("options_approved_level", 0)) or 0
            return int(lvl) >= 1
    except Exception as e:
        log(f"  Options preflight error: {e}", "WARN")
    return False


def _fetch_option_chain(underlying: str, otype: str, today: date) -> list:
    lo = (today + timedelta(days=OPTIONS_MIN_DTE)).isoformat()
    hi = (today + timedelta(days=OPTIONS_MAX_DTE + 10)).isoformat()  # pad for fallback
    try:
        r = requests.get(
            f"{ALPACA_BASE}/v2/options/contracts",
            headers=ALPACA_HEADERS,
            params={"underlying_symbols": underlying, "type": otype, "status": "active",
                    "expiration_date_gte": lo, "expiration_date_lte": hi, "limit": 1000},
            timeout=10)
        if r.ok:
            return r.json().get("option_contracts", [])
        log(f"  Option chain {underlying}: {r.status_code} {r.text[:100]}", "WARN")
    except Exception as e:
        log(f"  Option chain error {underlying}: {e}", "WARN")
    return []


def select_option_contract(underlying: str, otype: str, spot: float, today: date) -> dict | None:
    contracts = _fetch_option_chain(underlying, otype, today)
    if not contracts:
        return None
    by_exp: dict = {}
    for c in contracts:
        try:
            exp = date.fromisoformat(c["expiration_date"])
        except Exception:
            continue
        by_exp.setdefault(exp, []).append(c)
    exp = _pick_expiry(list(by_exp.keys()), today, OPTIONS_MIN_DTE, OPTIONS_MAX_DTE)
    if not exp:
        return None
    chain   = by_exp[exp]
    strikes = [float(c["strike_price"]) for c in chain]
    strike  = _pick_strike(strikes, spot, otype, OPTIONS_MONEYNESS)
    if strike is None:
        return None
    pick = min(chain, key=lambda c: abs(float(c["strike_price"]) - strike))
    return {"symbol": pick["symbol"], "strike": float(pick["strike_price"]),
            "expiry": exp.isoformat(), "type": otype}


def get_option_quote(occ: str) -> tuple[float, float, float]:
    """(bid, ask, last) per share for an option contract; zeros on failure."""
    try:
        r = requests.get(
            f"{ALPACA_DATA}/v1beta1/options/snapshots",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
            params={"symbols": occ, "feed": "indicative"},
            timeout=8)
        if r.ok:
            snap = r.json().get("snapshots", {}).get(occ, {})
            q = snap.get("latestQuote", {}) or {}
            t = snap.get("latestTrade", {}) or {}
            return (float(q.get("bp", 0) or 0), float(q.get("ap", 0) or 0),
                    float(t.get("p", 0) or 0))
    except Exception as e:
        log(f"  Option quote error {occ}: {e}", "WARN")
    return (0.0, 0.0, 0.0)


def _get_underlying_price(sym: str) -> float:
    t = tickers.get(sym)
    if t and t.price:
        return float(t.price)
    try:
        r = requests.get(f"{ALPACA_DATA}/v2/stocks/{sym}/quotes/latest",
                         headers={"APCA-API-KEY-ID": ALPACA_KEY,
                                  "APCA-API-SECRET-KEY": ALPACA_SECRET}, timeout=6)
        if r.ok:
            q = r.json().get("quote", {})
            ap, bp = float(q.get("ap", 0) or 0), float(q.get("bp", 0) or 0)
            if ap and bp:
                return round((ap + bp) / 2, 2)
    except Exception:
        pass
    return 0.0


def execute_option_signal(sig: dict, equity: float) -> tuple[bool, int]:
    """Options-only execution: buy a long call (long signal) or put (short)."""
    if not sig.get("approved"):
        return False, 0
    underlying = sig["symbol"]
    if underlying not in OPTIONS_TICKERS:
        log(f"  SKIP {underlying}: no liquid options chain (not in OPTIONS_TICKERS)")
        return False, 0
    otype = "call" if sig["direction"] == "long" else "put"
    spot  = sig["entry"]
    today = datetime.now(ET).date()
    contract = select_option_contract(underlying, otype, spot, today)
    if not contract:
        log(f"  SKIP {underlying}: no contract in {OPTIONS_MIN_DTE}-{OPTIONS_MAX_DTE} DTE window")
        return False, 0
    occ = contract["symbol"]
    bid, ask, last = get_option_quote(occ)
    buy_px = ask or last
    if buy_px <= 0:
        log(f"  SKIP {occ}: no quote available", "WARN")
        return False, 0
    pos_pct     = SMALL_ACCT_MAX_POS_PCT if SMALL_ACCT_EQUITY > 0 else RISK["max_position_pct"]
    cap_dollars = equity * pos_pct
    contracts   = _size_contracts(cap_dollars, buy_px)
    if contracts < 1:
        log(f"  SKIP {occ}: 1 contract (${buy_px*100:.0f}) exceeds "
            f"{pos_pct:.0%} cap of ${equity:,.0f}")
        return False, 0
    cost = contracts * buy_px * 100
    if OPTIONS_DRY_RUN:
        log(f"  [DRY RUN] would BUY {occ} x{contracts} @ ~${buy_px:.2f} (${cost:.0f}) | "
            f"{otype.upper()} strike ${contract['strike']:.2f} exp {contract['expiry']} | "
            f"underlying ${spot:.2f} stop ${sig['stop']:.2f} target ${sig['target']:.2f}")
        return False, 0
    order = {"symbol": occ, "qty": str(contracts), "side": "buy",
             "type": "market", "time_in_force": "day"}
    try:
        r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=ALPACA_HEADERS,
                          json=order, timeout=10)
        if r.status_code in (200, 201):
            oid  = r.json().get("id", "unknown")
            log(f"  OPTION ORDER: {occ} BUY x{contracts} @ ~${buy_px:.2f} (${cost:.0f}) | "
                f"{otype.upper()} strike ${contract['strike']:.2f} exp {contract['expiry']} | id:{oid}")
            trades = load_trades()
            trades.append({
                "order_id":          oid,
                "symbol":            underlying,
                "option_symbol":     occ,
                "is_option":         True,
                "option_type":       otype,
                "strike":            contract["strike"],
                "expiry":            contract["expiry"],
                "contracts":         contracts,
                "direction":         sig["direction"],
                "signal_type":       sig["type"],
                "entry_price":       round(buy_px, 4),   # premium/share — drives P&L + learning
                "entry_premium":     round(buy_px, 4),
                "underlying_entry":  spot,
                "underlying_stop":   sig["stop"],
                "underlying_target": sig["target"],
                "shares":            contracts,           # keep field present for shared code
                "risk_amount":       round(cost, 2),      # premium at risk (defined)
                "criteria":          sig["criteria"],
                "ai_score":          sig.get("ai_score"),
                "ai_rec":            sig.get("ai_rec"),
                "vix":               macro.vix,
                "regime":            macro.market_regime,
                "status":            "open",
                "opened_at":         datetime.now(ET).isoformat(),
                "paper_mode":        PAPER_MODE,
            })
            save_trades(trades)
            _tg(f"Option Buy: {underlying} {otype.upper()} ${contract['strike']:.0f} "
                f"exp {contract['expiry']}\nx{contracts} @ ~${buy_px:.2f} (${cost:.0f})\n"
                f"Underlying stop ${sig['stop']:.2f} / target ${sig['target']:.2f}")
            return True, contracts
        log(f"  Option order failed {r.status_code}: {r.text[:150]}", "ERROR")
    except Exception as e:
        log(f"  Option execution error: {e}", "ERROR")
    return False, 0


def close_option_position(trade: dict, reason: str) -> bool:
    """Market-sell an option to close. Mutates `trade` in place; caller saves."""
    occ = trade.get("option_symbol")
    qty = int(trade.get("contracts", 0))
    if not occ or qty < 1:
        return False
    order = {"symbol": occ, "qty": str(qty), "side": "sell",
             "type": "market", "time_in_force": "day"}
    try:
        r = requests.post(f"{ALPACA_BASE}/v2/orders", headers=ALPACA_HEADERS,
                          json=order, timeout=10)
        if r.status_code in (200, 201):
            bid, ask, last = get_option_quote(occ)
            exit_px  = bid or last or trade.get("entry_premium", 0)
            entry_px = trade.get("entry_premium", 0) or trade.get("entry_price", 0)
            pnl_pct    = ((exit_px - entry_px) / entry_px * 100) if entry_px else 0
            pnl_dollar = (exit_px - entry_px) * qty * 100
            trade["status"]       = "closed"
            trade["close_reason"] = reason
            trade["closed_at"]    = datetime.now(ET).isoformat()
            trade["closed_price"] = round(exit_px, 4)
            trade["pnl_pct"]      = round(pnl_pct, 2)
            trade["pnl"]          = round(pnl_dollar, 2)
            log(f"  OPTION CLOSED {occ} x{qty} @ ~${exit_px:.2f} "
                f"P&L {pnl_pct:+.1f}% (${pnl_dollar:+.0f}) — {reason}")
            log_trade_outcome(trade)
            _tg(f"Option Close: {trade['symbol']} {trade.get('option_type','').upper()}\n"
                f"P&L: {pnl_pct:+.1f}% (${pnl_dollar:+.0f})\n{reason}")
            return True
        log(f"  Option close failed {occ}: {r.status_code} {r.text[:100]}", "ERROR")
    except Exception as e:
        log(f"  Option close error {occ}: {e}", "ERROR")
    return False


def monitor_option_positions():
    """Synthetic stop/target + theta time-stop + EOD close for long options."""
    if not OPTIONS_ENABLED:
        return
    trades = load_trades()
    if not any(t.get("is_option") and t.get("status") == "open" for t in trades):
        return
    now         = datetime.now(ET)
    eod_enabled = os.environ.get("EOD_CLOSE", "true").lower() == "true"
    is_eod      = eod_enabled and ((now.hour == 15 and now.minute >= 50) or
                                   (now.hour == 16 and now.minute == 0))
    updated = False
    for trade in trades:
        if not trade.get("is_option") or trade.get("status") != "open":
            continue
        sym  = trade["symbol"]
        u_px = _get_underlying_price(sym)
        decision = option_exit_decision(trade.get("direction", "long"), u_px,
                                        trade.get("underlying_stop", 0),
                                        trade.get("underlying_target", 0))
        reason = None
        if decision == "target":
            reason = f"TARGET (underlying ${u_px:.2f})"
        elif decision == "stop":
            reason = f"STOP (underlying ${u_px:.2f})"
        else:
            try:
                opened = datetime.fromisoformat(trade.get("opened_at", "")).astimezone(ET)
                hrs = (now - opened).total_seconds() / 3600
            except Exception:
                hrs = 0
            if hrs >= RISK.get("max_hold_hours", 6):
                reason = f"TIME_EXIT ({hrs:.1f}h theta)"
            elif is_eod:
                reason = "EOD_CLOSE"
        if reason and close_option_position(trade, reason):
            updated = True
    if updated:
        save_trades(trades)


def reprotect_positions():
    """
    FIX: Self-healing bracket protection.
    After 'day' orders expire at 4 PM, positions are left naked.
    On every monitor cycle, check if any open Alpaca position is missing
    stop/target orders, and re-place GTC OCO bracket using saved trade data.
    """
    positions = get_open_positions()
    if not positions:
        return

    # Get all open orders to see which positions already have protection
    open_orders = []
    try:
        r = requests.get(
            f"{ALPACA_BASE}/v2/orders",
            headers=ALPACA_HEADERS,
            params={"status": "open", "limit": 100},
            timeout=8
        )
        if r.ok:
            open_orders = r.json()
    except Exception as e:
        log(f"  reprotect: order fetch error {e}", "WARN")
        return

    # Build set of symbols that have active stop or limit orders
    protected_syms = set()
    for o in open_orders:
        if o.get("type") in ("stop", "stop_limit", "limit", "bracket", "oco"):
            sym = o.get("symbol")
            if sym:
                protected_syms.add(sym)
        # Also check legs (Alpaca returns "legs": null for simple orders)
        for leg in (o.get("legs") or []):
            leg_type = leg.get("type", "").lower()
            if "stop" in leg_type or "limit" in leg_type:
                protected_syms.add(o.get("symbol", ""))

    trades = load_trades()
    updated = False

    for pos in positions:
        if pos.get("asset_class") == "us_option":
            continue  # options can't be OCO-bracketed; handled by monitor_option_positions
        sym = pos.get("symbol")
        if not sym or sym in protected_syms:
            continue  # Already protected

        # Find saved trade record for this position
        trade = next((t for t in trades
                      if t.get("symbol") == sym and t.get("status") == "open"), None)
        if not trade:
            log(f"  reprotect: no trade record for {sym} — skipping", "WARN")
            continue

        qty       = int(float(pos.get("qty", 1)))
        side_held = pos.get("side", "long")
        direction = trade.get("direction", side_held)
        stop      = trade.get("stop_loss", 0)
        target    = trade.get("take_profit", 0)

        if not stop or not target:
            log(f"  reprotect: no stop/target data for {sym} — skipping", "WARN")
            continue

        log(f"  reprotect: {sym} missing bracket — re-placing GTC OCO "
            f"stop=${stop:.2f} target=${target:.2f}", "WARN")

        close_side = "sell" if direction == "long" else "buy"
        try:
            r = requests.post(
                f"{ALPACA_BASE}/v2/orders",
                headers=ALPACA_HEADERS,
                json={
                    "symbol":        sym,
                    "qty":           str(qty),
                    "side":          close_side,
                    "type":          "market",
                    "time_in_force": "gtc",
                    "order_class":   "oco",
                    "stop_loss":     {"stop_price":  str(round(stop, 2))},
                    "take_profit":   {"limit_price": str(round(target, 2))},
                },
                timeout=10
            )
            if r.status_code in (200, 201):
                new_id = r.json().get("id", "unknown")
                trade["order_id"] = new_id
                log(f"  Reprotected {sym}: stop=${stop:.2f} target=${target:.2f} id={new_id}")
                _tg(
                    f"Position Reprotected: {sym}\n"
                    f"Stop: ${stop:.2f} | Target: ${target:.2f}\n"
                    f"GTC OCO bracket re-placed automatically."
                )
                updated = True
            else:
                log(f"  Reprotect failed {sym}: {r.status_code} {r.text[:100]}", "WARN")
        except Exception as e:
            log(f"  reprotect error {sym}: {e}", "WARN")

    if updated:
        save_trades(trades)


def sync_positions_from_alpaca():
    log("Syncing positions from Alpaca...")
    alpaca_positions = get_open_positions()
    alpaca_syms      = {p.get("symbol") for p in alpaca_positions}
    trades = load_trades()
    updated = False
    for trade in trades:
        if trade.get("status") != "open":
            continue
        if trade.get("is_option"):  # keyed by OCC, lifecycle in monitor_option_positions
            continue
        sym = trade["symbol"]
        if sym not in alpaca_syms:
            trade["status"]     = "closed"
            trade["closed_at"]  = datetime.now(ET).isoformat()
            trade["close_reason"] = "ALPACA_CLOSED (stop or target hit)"
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
            log(f"  Synced: {sym} closed by Alpaca P&L: {trade.get('pnl_pct', 0):+.1f}%")
            log_trade_outcome(trade)
            alert_trade_close(
                sym, trade.get("direction", "long"),
                trade.get("entry_price", 0),
                trade.get("closed_price", 0),
                trade.get("shares", 0),
                trade.get("close_reason", "")
            )
            updated = True
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


def check_stop_proximity():
    """Alert when position is within 0.5% of stop loss."""
    trades = load_trades()
    changed = False
    for trade in trades:
        if trade.get("status") != "open":
            continue
        if trade.get("is_option"):  # synthetic stop tracked in monitor_option_positions
            continue
        sym       = trade["symbol"]
        direction = trade.get("direction", "long")
        stop      = trade.get("stop_loss", 0)
        current   = trade.get("current_price", 0) or trade.get("entry_price", 0)
        alerted   = trade.get("stop_proximity_alerted", False)
        if not stop or not current:
            continue
        dist_pct = (current - stop) / current * 100 if direction == "long" else (stop - current) / current * 100
        if dist_pct <= 0.5 and not alerted:
            log(f"  STOP PROXIMITY: {sym} is {dist_pct:.2f}% from stop ${stop:.2f}", "WARN")
            lines = [
                "STOP PROXIMITY: " + sym,
                "Current: $" + format(current, ".2f") + " | Stop: $" + format(stop, ".2f"),
                "Distance: " + format(dist_pct, ".2f") + "% - stop may be hit soon",
                "Direction: " + direction.upper(),
            ]
            _tg("\n".join(lines))
            trade["stop_proximity_alerted"] = True
            changed = True
        elif dist_pct > 1.0 and alerted:
            trade["stop_proximity_alerted"] = False
            changed = True
    if changed:
        save_trades(trades)


def apply_trailing_stops():
    trades = load_trades()
    updated = False
    for trade in trades:
        if trade.get("status") != "open": continue
        if trade.get("is_option"): continue  # options exit via monitor_option_positions
        sym         = trade["symbol"]
        entry       = trade.get("entry_price", 0)
        current     = trade.get("current_price", 0) or entry
        direction   = trade.get("direction", "long")
        current_stop = trade.get("stop_loss", 0)
        order_id    = trade.get("order_id", "")
        if not entry or not current: continue
        profit_pct = (current - entry) / entry * 100 if direction == "long" else (entry - current) / entry * 100
        new_stop = current_stop
        reason   = ""
        if direction == "long":
            if profit_pct >= 7.0:
                new_stop = round(current * 0.98, 2); reason = f"trailing 2% below (up {profit_pct:.1f}%)"
            elif profit_pct >= 4.0:
                new_stop = round(current * 0.99, 2); reason = f"trailing 1% below (up {profit_pct:.1f}%)"
            elif profit_pct >= 2.0:
                new_stop = round(current * 0.995, 2); reason = f"trailing 0.5% below (up {profit_pct:.1f}%)"
            elif profit_pct >= 1.0:
                new_stop = round(entry * 1.001, 2); reason = f"moved to breakeven (up {profit_pct:.1f}%)"
        else:
            if profit_pct >= 7.0:
                new_stop = round(current * 1.02, 2); reason = f"trailing 2% above (down {profit_pct:.1f}%)"
            elif profit_pct >= 4.0:
                new_stop = round(current * 1.01, 2); reason = f"trailing 1% above (down {profit_pct:.1f}%)"
            elif profit_pct >= 2.0:
                new_stop = round(current * 1.005, 2); reason = f"trailing 0.5% above (down {profit_pct:.1f}%)"
            elif profit_pct >= 1.0:
                new_stop = round(entry * 0.999, 2); reason = f"moved to breakeven (down {profit_pct:.1f}%)"
        improved = (direction == "long"  and new_stop > current_stop) or \
                   (direction == "short" and new_stop < current_stop)
        if improved and reason:
            log(f"  Trailing stop: {sym} {direction} ${current_stop:.2f} -> ${new_stop:.2f} | {reason}")
            trade["stop_loss"]     = new_stop
            trade["trailing_stop"] = True
            trade["trail_reason"]  = reason
            if order_id:
                update_stop_loss(sym, order_id, new_stop)
            lock_msg = "Lock in profit!" if profit_pct >= 1.0 else ""
            _tg(
                f"Trailing Stop Updated: {sym}\n"
                f"Stop: ${current_stop:.2f} -> ${new_stop:.2f}\n"
                f"Current: ${current:.2f} | {reason}\n"
                f"{lock_msg}"
            )
            updated = True
    if updated:
        save_trades(trades)


def check_news_emergency_exit():
    EMERGENCY_KEYWORDS = [
        "sec charges", "fraud charges", "going concern", "chapter 11",
        "bankruptcy filing", "emergency shutdown", "trading halted",
        "fda rejection", "clinical trial failed", "ceo arrested",
        "accounting fraud", "restatement", "delisted",
    ]
    trades = load_trades()
    for trade in trades:
        if trade.get("status") != "open": continue
        sym       = trade["symbol"]
        direction = trade.get("direction", "long")
        t         = tickers.get(sym)
        if not t: continue
        if direction == "short": continue
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
                if emergency: break
        if emergency:
            qty  = trade.get("shares", 1)
            side = "sell" if direction == "long" else "buy"
            log(f"  EMERGENCY EXIT: {sym} | {trigger}", "WARN")
            success = close_position_market(sym, qty, side, f"Emergency exit: {trigger}")
            if success:
                trade["status"]       = "closed"
                trade["close_reason"] = f"EMERGENCY: {trigger}"
                trade["closed_at"]    = datetime.now(ET).isoformat()
                save_trades(trades)
                log_trade_outcome(trade)
                _tg(
                    f"EMERGENCY EXIT: {sym}\n"
                    f"Reason: {trigger}\n"
                    f"Position closed at market."
                )


def eod_close_all():
    now         = datetime.now(ET)
    eod_enabled = os.environ.get("EOD_CLOSE", "true").lower() == "true"
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
        f"EOD Close {now.strftime('%H:%M ET')}\n"
        f"Closing {len(positions)} open position(s).\n"
        f"Avoiding overnight risk."
    )
    closed = 0
    for pos in positions:
        if pos.get("asset_class") == "us_option":
            continue  # options EOD handled by monitor_option_positions
        sym  = pos.get("symbol")
        qty  = int(float(pos.get("qty", 1)))
        side_held = pos.get("side", "long")
        close_side = "sell" if side_held == "long" else "buy"
        unreal_pct = float(pos.get("unrealized_plpc", 0)) * 100
        unreal_pl  = float(pos.get("unrealized_pl", 0))
        success = close_position_market(sym, qty, close_side, f"EOD close (P&L: {unreal_pct:+.1f}%)")
        if success:
            closed += 1
            _tg(
                f"EOD Closed: {sym}\n"
                f"P&L: {unreal_pct:+.1f}% (${unreal_pl:+.2f})\n"
            )
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


def close_all_positions():
    """
    Flatten the account NOW, regardless of time-of-day / EOD window.

    Triggered by RUN_MODE=close_all (manual workflow_dispatch). Cancels all
    resting orders first (bracket stop/target legs would otherwise hold shares
    and reject the market close), then market-closes every open position and
    reconciles the local trades.json ledger the same way eod_close_all does.
    Used to clear legacy oversized positions so the small-account ($2k) sizing
    mode starts from a clean, flat slate.
    """
    log("=" * 60)
    log("CLOSE-ALL: manual flatten requested (RUN_MODE=close_all)")

    # 1. Cancel every resting order so bracket legs don't hold shares.
    try:
        rc = requests.delete(f"{ALPACA_BASE}/v2/orders",
                             headers=ALPACA_HEADERS, timeout=10)
        log(f"CLOSE-ALL: cancelled open orders (HTTP {rc.status_code})")
    except Exception as e:
        log(f"CLOSE-ALL: order-cancel error: {e}", "ERROR")

    positions = get_open_positions()
    if not positions:
        log("CLOSE-ALL: no open positions — account already flat")
        _tg("Close-All\nAccount already flat — no positions to close.")
        # Still reconcile any stale 'open' ledger rows.
        trades = load_trades()
        stale = [t for t in trades if t.get("status") == "open"]
        if stale:
            for t in stale:
                t["status"]       = "closed"
                t["close_reason"] = "CLOSE_ALL_RECONCILE"
                t["closed_at"]    = datetime.now(ET).isoformat()
            save_trades(trades)
            log(f"CLOSE-ALL: reconciled {len(stale)} stale open ledger row(s)")
        commit_state_to_github()
        return

    log(f"CLOSE-ALL: closing {len(positions)} open position(s)...")
    _tg(f"Close-All requested\nFlattening {len(positions)} open position(s) now.")

    closed = 0
    trades = load_trades()
    for pos in positions:
        sym = pos.get("symbol")
        # Options: close via the ledger (needs contracts + OCC symbol).
        if pos.get("asset_class") == "us_option":
            match = next((t for t in trades if t.get("is_option")
                          and t.get("status") == "open"
                          and t.get("option_symbol") == sym), None)
            if match and close_option_position(match, "CLOSE_ALL"):
                closed += 1
            else:
                log(f"CLOSE-ALL: could not close option {sym} via ledger", "WARN")
            continue

        qty        = int(float(pos.get("qty", 1)))
        side_held  = pos.get("side", "long")
        close_side = "sell" if side_held == "long" else "buy"
        unreal_pct = float(pos.get("unrealized_plpc", 0)) * 100
        unreal_pl  = float(pos.get("unrealized_pl", 0))
        if close_position_market(sym, qty, close_side,
                                 f"CLOSE_ALL (P&L: {unreal_pct:+.1f}%)"):
            closed += 1
            _tg(f"Closed: {sym}\nP&L: {unreal_pct:+.1f}% (${unreal_pl:+.2f})")
            for trade in trades:
                if trade.get("symbol") == sym and trade.get("status") == "open" \
                        and not trade.get("is_option"):
                    trade["status"]       = "closed"
                    trade["close_reason"] = "CLOSE_ALL"
                    trade["closed_at"]    = datetime.now(ET).isoformat()
                    trade["closed_price"] = float(pos.get("current_price", 0))
                    trade["pnl_pct"]      = round(unreal_pct, 2)
                    trade["pnl"]          = round(unreal_pl, 2)
                    log_trade_outcome(trade)

    # Reconcile any remaining stale 'open' ledger rows with no broker position.
    broker_syms = {p.get("symbol") for p in positions}
    for trade in trades:
        if trade.get("status") == "open" and trade.get("symbol") not in broker_syms \
                and trade.get("option_symbol") not in broker_syms:
            trade["status"]       = "closed"
            trade["close_reason"] = "CLOSE_ALL_RECONCILE"
            trade["closed_at"]    = datetime.now(ET).isoformat()

    save_trades(trades)
    log(f"CLOSE-ALL complete: {closed}/{len(positions)} closed")
    _tg(f"Close-All complete\n{closed}/{len(positions)} position(s) closed. "
        f"Account is flat — $2k sizing starts fresh next scan.")
    commit_state_to_github()


def check_time_based_exits():
    trades = load_trades()
    now    = datetime.now(ET)
    updated = False
    for trade in trades:
        if trade.get("status") != "open": continue
        if trade.get("is_option"): continue  # options exit via monitor_option_positions
        opened_at = trade.get("opened_at", "")
        if not opened_at: continue
        try:
            open_time = datetime.fromisoformat(opened_at).astimezone(ET)
        except: continue
        hours_open  = (now - open_time).total_seconds() / 3600
        unreal_pct  = trade.get("unrealized_pct", 0)
        sym         = trade["symbol"]
        direction   = trade.get("direction", "long")
        qty         = trade.get("shares", 1)
        close_side  = "sell" if direction == "long" else "buy"
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
                _tg(f"Time Exit: {sym}\nOpen {hours_open:.1f}h with no movement ({unreal_pct:+.1f}%)")
                updated = True
        elif hours_open >= RISK.get("max_hold_hours", 4):
            log(f"  Time exit (max hold): {sym} open {hours_open:.1f}h — force closing", "WARN")
            success = close_position_market(sym, qty, close_side, "Max hold exceeded")
            if success:
                trade["status"]       = "closed"
                trade["close_reason"] = f"TIME_EXIT_8H ({unreal_pct:+.1f}%)"
                trade["closed_at"]    = now.isoformat()
                log_trade_outcome(trade)
                _tg(f"Max-Hold Exit: {sym}\nP&L: {unreal_pct:+.1f}% | Open {hours_open:.1f}h")
                updated = True
        elif hours_open >= 2 and unreal_pct < -1.5:
            current_stop  = trade.get("stop_loss", 0)
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
                        f"after {hours_open:.1f}h -> ${new_stop:.2f}")
                    trade["stop_loss"]    = new_stop
                    trade["trail_reason"] = f"Tightened after {hours_open:.1f}h weak"
                    order_id = trade.get("order_id","")
                    if order_id:
                        update_stop_loss(sym, order_id, new_stop)
                    updated = True
    if updated:
        save_trades(trades)


def run_position_monitor():
    log("\n-- Position Monitor ------------------------------------------")

    # 1. Sync from Alpaca (source of truth)
    alpaca_positions = sync_positions_from_alpaca()

    # 2. FIX: Re-protect any positions missing bracket orders
    reprotect_positions()

    # 2b. Options: synthetic stop/target/time/EOD exits (runs before the zero-
    # equity-position early return so option-only accounts are still monitored).
    monitor_option_positions()

    open_count = len(alpaca_positions)
    if open_count == 0:
        log("No open positions — monitor complete")
        return

    log(f"Monitoring {open_count} open position(s)")

    # 3. Refresh real-time prices for open positions
    open_syms = [p.get("symbol") for p in alpaca_positions]
    if open_syms:
        try:
            syms_str = ",".join(open_syms)
            r = requests.get(
                f"{ALPACA_DATA}/v2/stocks/quotes/latest",
                headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
                params={"symbols": syms_str, "feed": "iex"},
                timeout=8
            )
            if r.ok:
                quotes = r.json().get("quotes", {})
                for sym, q in quotes.items():
                    if sym in tickers:
                        ask = float(q.get("ap", 0))
                        bid = float(q.get("bp", 0))
                        if ask > 0 and bid > 0:
                            tickers[sym].price = round((ask + bid) / 2, 2)
                        log(f"  Real-time quote {sym}: ${tickers[sym].price:.2f}")
        except Exception as e:
            log(f"  Real-time price refresh error: {e}", "WARN")
            for sym in open_syms:
                try:
                    data = yf.Ticker(sym).history(period="1d")
                    if not data.empty and sym in tickers:
                        tickers[sym].price = float(data["Close"].iloc[-1])
                except: pass
        for sym in open_syms:
            try:
                since = (datetime.now(ET) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
                r = requests.get(
                    f"{ALPACA_DATA}/v1beta1/news",
                    headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET},
                    params={"symbols": sym, "start": since, "limit": 5},
                    timeout=6
                )
                if r.ok and sym in tickers:
                    articles = r.json().get("news", [])
                    headlines = [a["headline"] for a in articles[:5]]
                    tickers[sym].headlines     = headlines
                    tickers[sym].headline_score = score_headlines(headlines, sym)["score"]
            except: pass

    # 4. Check stop proximity
    check_stop_proximity()

    # 5. Apply trailing stops
    apply_trailing_stops()

    # 6. Time-based exits
    check_time_based_exits()

    # 7. News emergencies
    check_news_emergency_exit()

    # 8. EOD close check
    eod_close_all()

    log("-- Monitor complete -------------------------------------------\n")


# ══════════════════════════════════════════════════════════════════════════════
# DAILY REVIEW
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
        "Provide:\n1. PERFORMANCE: What worked/didn't.\n"
        "2. SIGNALS: Were criteria appropriate?\n"
        "3. TOMORROW: One specific thing to watch.\n"
        "4. REGIME: How did conditions affect strategy?"
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
        review_file = STATE_DIR / f"review_{today}.txt"
        review_file.write_text(review)
    except Exception as e:
        log(f"Review error: {e}", "ERROR")


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM COMMAND HANDLER
# ══════════════════════════════════════════════════════════════════════════════

_TG_OFFSET_FILE = STATE_DIR / "tg_offset.json"

def _get_tg_offset() -> int:
    try:
        if _TG_OFFSET_FILE.exists():
            return json.loads(_TG_OFFSET_FILE.read_text()).get("offset", 0)
    except: pass
    return 0

def _save_tg_offset(offset: int):
    try:
        _TG_OFFSET_FILE.write_text(json.dumps({"offset": offset}))
    except: pass

def get_tg_updates() -> list:
    if not TELEGRAM_TOKEN: return []
    try:
        offset = _get_tg_offset()
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 2, "limit": 10},
            timeout=8
        )
        if not r.ok: return []
        updates = r.json().get("result", [])
        if updates:
            _save_tg_offset(updates[-1]["update_id"] + 1)
        return updates
    except Exception as e:
        log(f"Telegram getUpdates error: {e}", "WARN")
        return []

_PAUSE_FILE = STATE_DIR / "paused.json"

def is_paused() -> bool:
    try:
        if _PAUSE_FILE.exists():
            return json.loads(_PAUSE_FILE.read_text()).get("paused", False)
    except: pass
    return False

def set_paused(paused: bool, reason: str = ""):
    try:
        _PAUSE_FILE.write_text(json.dumps({
            "paused": paused, "reason": reason,
            "timestamp": datetime.now(ET).isoformat()
        }))
    except: pass

def handle_tg_command(text: str, chat_id: str) -> bool:
    text = text.strip().lower()
    cmd  = text.split()[0].lstrip("/")
    args = text[len(cmd)+1:].strip() if len(text) > len(cmd) else ""

    def reply(msg: str):
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": msg},
                timeout=8
            )
        except: pass

    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        return False

    if cmd in ("help", "h", "?"):
        reply(
            "Boticus Commands\n\n"
            "Scanning\n"
            "/scan — trigger scan now\n"
            "/nearmiss — what almost fired\n"
            "/debug — what is blocking signals\n"
            "/testsignal — test AI pipeline (no order)\n\n"
            "Info\n"
            "/status — equity + open positions\n"
            "/positions — detailed open trades\n"
            "/regime — regime + VIX + breadth\n"
            "/watchlist — active tickers\n"
            "/pnl — today P&L\n\n"
            "Control\n"
            "/pause — halt new trades\n"
            "/resume — resume trading\n"
            "/eod — close all positions now\n\n"
            "Intelligence\n"
            "/research — run research digest\n"
            "/backtest — run 180-day backtest\n"
            "/params — show effective tunable params\n"
            "/apply — apply validated param proposal\n"
            "/revert — undo last applied params\n"
            "/feed <text> — inject text into AI brain\n"
        )
        return True

    elif cmd == "status":
        try:
            acc       = get_account()
            positions = get_open_positions()
            paused    = is_paused()
            fg        = macro.fear_greed if hasattr(macro, 'fear_greed') else {}
            pos_lines = "\n".join([
                f"  {p.get('symbol')} {p.get('side','').upper()} "
                f"x{p.get('qty',0)} P&L: ${float(p.get('unrealized_pl',0)):+.2f}"
                for p in positions[:8]
            ]) or "  None"
            reply(
                f"Boticus Status {'PAUSED' if paused else 'Active'}\n\n"
                f"Equity: ${float(acc.get('equity',0)):,.2f}\n"
                f"Buying power: ${float(acc.get('buying_power',0)):,.2f}\n"
                f"Open positions: {len(positions)}/6\n\n"
                f"Positions:\n{pos_lines}\n\n"
                f"Regime: {macro.market_regime} | VIX: {macro.vix:.1f}\n"
                f"F&G: {fg.get('score',50):.0f} ({fg.get('rating','neutral')})"
            )
        except Exception as e:
            reply(f"Status error: {e}")
        return True

    elif cmd == "positions":
        try:
            positions = get_open_positions()
            if not positions:
                reply("No open positions.")
                return True
            lines = []
            for p in positions:
                sym     = p.get("symbol","")
                side    = p.get("side","").upper()
                qty     = p.get("qty",0)
                entry   = float(p.get("avg_entry_price",0))
                curr    = float(p.get("current_price",0))
                unreal  = float(p.get("unrealized_pl",0))
                unreal_pct = float(p.get("unrealized_plpc",0))*100
                icon    = "+" if unreal > 0 else "-"
                lines.append(
                    f"{icon} {sym} {side} x{qty}\n"
                    f"   Entry: ${entry:.2f} -> ${curr:.2f} "
                    f"({unreal_pct:+.1f}% / ${unreal:+.2f})"
                )
            reply("Open Positions\n\n" + "\n".join(lines))
        except Exception as e:
            reply(f"Positions error: {e}")
        return True

    elif cmd == "regime":
        fg = macro.fear_greed if hasattr(macro, 'fear_greed') else {}
        reply(
            f"Market Regime\n\n"
            f"Regime: {macro.market_regime}\n"
            f"VIX: {macro.vix:.1f} ({macro.vix_regime})\n"
            f"Fear & Greed: {fg.get('score',50):.0f} ({fg.get('rating','neutral')})\n"
            f"Futures: {macro.futures_sentiment}\n"
            f"Risk score: {macro.risk_score:+d}\n"
            f"FOMC today: {'Yes' if macro.fomc_24h else 'No'}\n"
            f"CPI today: {'Yes' if macro.cpi_24h else 'No'}"
        )
        return True

    elif cmd == "watchlist":
        wl = get_watchlist()
        core    = [t for t in wl if t in CORE_WATCHLIST]
        dynamic = [t for t in wl if t not in CORE_WATCHLIST]
        reply(
            f"Active Watchlist ({len(wl)} tickers)\n\n"
            f"Core ({len(core)}): {', '.join(core)}\n\n"
            f"Dynamic ({len(dynamic)}): {', '.join(dynamic) or 'None today'}"
        )
        return True

    elif cmd == "pause":
        set_paused(True, args or "Manual pause via Telegram")
        reply(f"Trading PAUSED\nReason: {args or 'Manual'}\nSend /resume to restart.")
        return True

    elif cmd == "resume":
        set_paused(False)
        reply("Trading RESUMED — bot will scan on next cycle.")
        return True

    elif cmd == "eod":
        reply("EOD Close triggered — closing all positions now...")
        try:
            eod_close_all()
        except Exception as e:
            reply(f"EOD close error: {e}")
        return True

    elif cmd == "research":
        reply("Running research digest... (takes ~2 min)")
        try:
            run_research_digest()
        except Exception as e:
            reply(f"Research error: {e}")
        return True

    elif cmd == "backtest":
        reply("Running backtest... (takes ~5 min)")
        try:
            bt = run_backtest(lookback_days=180)
            if bt:
                run_auto_adjust(backtest=bt)
        except Exception as e:
            reply(f"Backtest error: {e}")
        return True

    elif cmd == "params":
        apply_risk_overrides()
        active = _overlay_params(RISK_OVERRIDE_FILE)
        pend   = _overlay_params(RISK_PENDING_FILE)
        lines  = "\n".join(f"  {k}: {RISK[k]}" for k in TUNABLE_BOUNDS)
        reply(
            "Effective tunable params:\n" + lines +
            f"\n\nOverride active: {'yes ' + json.dumps(active) if active else 'no (bot.py defaults)'}"
            f"\nPending proposal: {'yes — reply /apply. ' + json.dumps(pend) if pend else 'none'}"
        )
        return True

    elif cmd == "apply":
        pend = _overlay_params(RISK_PENDING_FILE)
        if not pend:
            reply("No pending proposal to apply. Run /backtest to generate one.")
            return True
        if RISK_OVERRIDE_FILE.exists():
            RISK_PREV_FILE.write_text(RISK_OVERRIDE_FILE.read_text())
        RISK_OVERRIDE_FILE.write_text(json.dumps({
            "params": pend, "applied_at": datetime.now(ET).isoformat(),
            "source": "auto_adjust /apply",
        }, indent=2))
        RISK_PENDING_FILE.write_text(json.dumps({"params": {}}, indent=2))
        apply_risk_overrides()
        commit_state_to_github()
        reply("✅ Applied: " + json.dumps(pend) +
              "\nLive from the next scan. Send /revert to undo.")
        return True

    elif cmd == "revert":
        if RISK_PREV_FILE.exists() and _overlay_params(RISK_PREV_FILE):
            RISK_OVERRIDE_FILE.write_text(RISK_PREV_FILE.read_text())
            RISK_PREV_FILE.write_text(json.dumps({"params": {}}, indent=2))
            commit_state_to_github()
            reply("Reverted to previous overlay: " +
                  json.dumps(_overlay_params(RISK_OVERRIDE_FILE)))
        elif _overlay_params(RISK_OVERRIDE_FILE):
            RISK_OVERRIDE_FILE.write_text(json.dumps({"params": {}}, indent=2))
            commit_state_to_github()
            reply("Cleared override — back to bot.py defaults.")
        else:
            reply("No override active — already on defaults.")
        return True

    elif cmd == "debug":
        trades   = load_trades()
        open_t   = [t for t in trades if t.get("status") == "open"]
        reply(
            f"Debug - Last Scan\n\n"
            f"Regime: {macro.market_regime}\n"
            f"VIX: {macro.vix:.1f} ({macro.vix_regime})\n"
            f"Risk score: {macro.risk_score:+d}\n"
            f"RSI min required: {RISK['rsi_min']}\n"
            f"Volume min required: {RISK['volume_min_mult']}x\n"
            f"Open positions: {len(open_t)}/6\n"
            f"FOMC: {'BLOCKED' if macro.fomc_24h else 'clear'}\n"
            f"CPI: {'BLOCKED' if macro.cpi_24h else 'clear'}\n"
            f"Paused: {'YES' if is_paused() else 'No'}"
        )
        return True

    elif cmd == "scan":
        session_now = get_market_session()
        if session_now != "open":
            nm_file = STATE_DIR / "near_miss.json"
            if nm_file.exists():
                nm_data = json.loads(nm_file.read_text())
                nms = nm_data.get("near_misses", [])[:5]
                updated = nm_data.get("updated_at", "")[:16].replace("T", " ")
                lines_out = ["Market " + session_now + " - last scan at " + updated + " ET", ""]
                for n in nms:
                    b = n["blockers"][0] if n.get("blockers") else "below threshold"
                    lines_out.append(n["symbol"] + " " + str(round(n["criteria"],1)) + "/58 - " + b)
                reply("\n".join(lines_out))
            else:
                reply("Market " + session_now + " - no scan data yet")
            return True
        reply("Scanning " + str(len(get_watchlist())) + " tickers...")
        try:
            sigs, nms_list = [], []
            for sym in get_watchlist():
                for fn in (scan_long, scan_short):
                    s = fn(sym)
                    if s:
                        sigs.append(s)
                    else:
                        direction = "long" if fn == scan_long else "short"
                        nm = scan_diagnostic(sym, direction)
                        if nm:
                            nms_list.append(nm)
            sigs.sort(key=lambda x: x["criteria"], reverse=True)
            nms_list.sort(key=lambda x: x["criteria"], reverse=True)
            if sigs:
                out_lines = ["Scan: " + str(len(sigs)) + " signal(s)", ""]
                for s in sigs[:5]:
                    d = "LONG" if s["direction"] == "long" else "SHORT"
                    out_lines.append(s["symbol"] + " " + d + " criteria:" + str(round(s["criteria"],1)))
                reply("\n".join(out_lines))
            else:
                out_lines = ["Scan: 0 signals | " + str(len(nms_list)) + " near-misses", ""]
                for n in nms_list[:5]:
                    b = n["blockers"][0] if n.get("blockers") else "criteria"
                    out_lines.append(n["symbol"] + " " + str(round(n["criteria"],1)) + "/58 - " + b)
                reply("\n".join(out_lines) if out_lines else "0 signals, nothing close")
        except Exception as e:
            reply("Scan error: " + str(e))
        return True

    elif cmd == "nearmiss":
        nm_file = STATE_DIR / "near_miss.json"
        if not nm_file.exists():
            reply("No near-miss data yet - run a scan first.")
            return True
        nm_data = json.loads(nm_file.read_text())
        nms = nm_data.get("near_misses", [])
        updated = nm_data.get("updated_at", "")[:16].replace("T", " ")
        if not nms:
            reply("No near-misses in last scan (" + updated + " ET)")
            return True
        out_lines = ["Near-Misses as of " + updated + " ET", ""]
        for n in nms[:8]:
            gap = 58 - n["criteria"]
            b = n["blockers"][0] if n.get("blockers") else "below threshold"
            out_lines.append(
                n["symbol"] + " score:" + str(round(n["criteria"],1)) + "/58 gap:" + str(round(gap,1))
                + " RSI:" + str(round(n.get("rsi",0),0))
                + " Vol:" + format(n.get("vol",0), ".1f") + "x"
                + " | " + b
            )
        reply("\n".join(out_lines))
        return True

    elif cmd == "pnl":
        feedback = load_trades()
        today    = date.today().isoformat()
        today_t  = [t for t in feedback
                    if t.get("closed_at","")[:10] == today and t.get("status") == "closed"]
        total_pnl = sum(t.get("pnl",0) for t in today_t)
        wins      = [t for t in today_t if t.get("pnl",0) > 0]
        reply(
            f"Today's P&L\n\n"
            f"Trades closed: {len(today_t)}\n"
            f"Wins: {len(wins)} | Losses: {len(today_t)-len(wins)}\n"
            f"Total P&L: ${total_pnl:+.2f}\n\n"
            + "\n".join([
                f"{'W' if t.get('pnl',0)>0 else 'L'} {t.get('symbol','')} "
                f"{t.get('pnl_pct',0):+.1f}% ${t.get('pnl',0):+.2f}"
                for t in today_t
            ])
        )
        return True

    elif cmd == "feed":
        if not args:
            reply("Usage: /feed <paste any text, article, analysis>")
            return True
        reply("Processing with Opus...")
        try:
            resp = ai_client.messages.create(
                model=OPUS_MODEL, max_tokens=800,
                system=(
                    "Extract 2-4 actionable trading insights from the text provided. "
                    "Output ONLY a valid JSON object, no markdown, no backticks, no preamble. "
                    "Use only simple ASCII characters in your response — no smart quotes, no special chars. "
                    "Format: "
                    '{"insights":[{"finding":"text","tickers":[],"impact":"bullish","confidence":"high","actionable":"text"}],'
                    '"summary":"1-2 sentences"}'
                ),
                messages=[{"role": "user", "content": "Extract trading insights from this text:\n\n" + args[:3000]}]
            )
            raw = resp.content[0].text.strip()
            # Strip markdown fences if present
            if "```" in raw:
                parts = raw.split("```")
                for part in parts:
                    part = part.strip()
                    if part.startswith("json"):
                        part = part[4:].strip()
                    if part.startswith("{"):
                        raw = part
                        break
            raw = raw.strip()
            # Fix common JSON issues from LLM output
            raw = raw.replace("\u2019", "'").replace("\u2018", "'")
            raw = raw.replace("\u201c", '"').replace("\u201d", '"')
            # Find JSON boundaries
            start_idx = raw.find("{")
            end_idx   = raw.rfind("}") + 1
            if start_idx >= 0 and end_idx > start_idx:
                raw = raw[start_idx:end_idx]
            result = json.loads(raw)
            insights = result.get("insights", [])
            feed_file = STATE_DIR / "fed_insights.json"
            existing  = json.loads(feed_file.read_text()) if feed_file.exists() else []
            existing.extend(insights)
            existing = existing[-50:]
            feed_file.write_text(json.dumps(existing, indent=2))
            findings = "\n".join(["- " + i.get("finding","")[:100] for i in insights])
            out = (
                "Fed to the beast\n\n"
                "Insights extracted:\n" + findings + "\n\n"
                + result.get("summary", "") + "\n\n"
                "Added to knowledge base."
            )
            reply(out)
        except json.JSONDecodeError as e:
            # Fallback: save as plain text insight if JSON fails
            log(f"Feed JSON parse error: {e} — saving as plain text", "WARN")
            feed_file = STATE_DIR / "fed_insights.json"
            existing  = json.loads(feed_file.read_text()) if feed_file.exists() else []
            existing.append({
                "finding":    args[:300],
                "tickers":    [],
                "impact":     "neutral",
                "confidence": "medium",
                "actionable": "manual note — review before trading",
            })
            existing = existing[-50:]
            feed_file.write_text(json.dumps(existing, indent=2))
            reply("Saved as plain text insight (JSON parse failed — content stored directly).")
        except Exception as e:
            reply("Feed error: " + str(e))
        return True

    elif cmd == "testsignal":
        # Force a mock signal through the full pipeline — confirms AI scoring and TG work
        reply("Running test signal through pipeline...")
        try:
            # Find the highest-scoring long candidate right now
            candidates = []
            for sym, t in tickers.items():
                if t.price > t.sma_50 and t.rsi_14 > 40 and t.vol_ratio > 0.5:
                    score = (t.rsi_14 / 100 * 30) + (t.vol_ratio * 20) + (20 if t.price > t.sma_200 else 0)
                    candidates.append((sym, score, t))
            if not candidates:
                reply("No tickers loaded yet — run a scan first.")
                return True
            sym, _, t = max(candidates, key=lambda x: x[1])
            atr = t.atr_14 or t.price * 0.015
            mock_sig = {
                "symbol": sym, "type": "test_signal", "direction": "long",
                "entry": t.price,
                "stop":   round(t.price - RISK["stop_loss_atr_mult"] * atr, 2),
                "target": round(t.price + RISK["take_profit_atr_mult"] * atr, 2),
                "rr": round(RISK["take_profit_atr_mult"] / RISK["stop_loss_atr_mult"], 2),
                "criteria": 65.0,
                "scores": {"trend": 70, "momentum": 65, "volume": 60, "atr": 75, "macro": 55},
                "notes": ["TEST SIGNAL — not a real trade", f"RSI={t.rsi_14:.1f} Vol={t.vol_ratio:.1f}x"],
                "headline_score": t.headline_score,
                "reddit_trending": False,
            }
            scored = score_signal(mock_sig)
            alert_signal(scored)
            reply(
                f"Test signal complete\n"
                f"Symbol: {sym}\n"
                f"AI score: {scored.get('ai_score', 0):.0f}/100\n"
                f"Rec: {scored.get('ai_rec', '')}\n"
                f"Reasoning: {scored.get('ai_reasoning', '')[:200]}\n"
                f"Approved: {scored.get('approved', False)}\n"
                f"(No order placed — test only)"
            )
        except Exception as e:
            reply(f"Test signal error: {e}")
        return True

    return False


def process_tg_commands():
    if not TELEGRAM_TOKEN: return
    updates = get_tg_updates()
    for update in updates:
        msg     = update.get("message", {})
        text    = msg.get("text", "")
        chat_id = str(msg.get("chat", {}).get("id", ""))
        if not text or not chat_id: continue
        if text.startswith("/"):
            log(f"Telegram command from {chat_id}: {text[:50]}")
            handled = handle_tg_command(text, chat_id)
            if not handled:
                try:
                    requests.post(
                        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                        json={"chat_id": chat_id, "text": "Unknown command. Send /help for list."},
                        timeout=8
                    )
                except: pass


def load_fed_insights() -> str:
    feed_file = STATE_DIR / "fed_insights.json"
    if not feed_file.exists(): return ""
    try:
        insights = json.loads(feed_file.read_text())
        if not insights: return ""
        lines = ["=== MANUALLY FED INSIGHTS ==="]
        for i in insights[-10:]:
            lines.append(f"- {i.get('finding','')} [{i.get('confidence','')}]")
        return "\n".join(lines)
    except: return ""


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def cleanup_flag_files():
    """Delete flag files older than 2 days to prevent STATE_DIR bloat."""
    cutoff = datetime.now(ET) - timedelta(days=2)
    for f in STATE_DIR.glob("*.flag"):
        try:
            import os as _os
            mtime = datetime.fromtimestamp(_os.path.getmtime(str(f)), tz=ET)
            if mtime < cutoff:
                f.unlink()
        except Exception:
            pass


def main():
    now     = datetime.now(ET)
    session = get_market_session()
    mode    = os.environ.get("RUN_MODE", "scan")

    log("=" * 60)
    log(f"TRADING BOT RUN | {now.strftime('%Y-%m-%d %H:%M ET')} | {session} | mode={mode}")
    log(f"Paper mode: {PAPER_MODE} | Telegram: {'yes' if TELEGRAM_TOKEN else 'no'}")
    log("=" * 60)
    apply_risk_overrides()   # overlay reviewed backtest-driven params onto RISK
    cleanup_flag_files()

    if session == "closed" and mode == "scan":
        log("Keep-alive run — market closed, exiting cleanly")
        return

    if is_paused() and mode == "scan":
        log("Bot is PAUSED — skipping scan. Send /resume to restart.")
        _tg("Bot is paused — skipping this cycle. Send /resume to restart.")
        return

    fetch_macro()
    fetch_price_data()
    load_earnings_beats_from_digest()

    # Process Telegram commands after data is loaded so /debug shows real values
    process_tg_commands()

    if session in ("open", "pre_market") and now.hour == 9 and now.minute <= 45:
        log("Market open — running dynamic watchlist update...")
        update_dynamic_watchlist()

    if session == "pre_market" and now.hour == 9 and now.minute < 30:
        gap_signals = scan_premarket_gaps()
        if gap_signals:
            log(f"Pre-market: {len(gap_signals)} gap setup(s) found")

    if session in ("open", "pre_market") and now.hour == 9:
        fetch_insider_filings(get_watchlist())
        fetch_unusual_options_flow()
        fetch_congress_trades()

    if mode == "close_all":
        close_all_positions()
        return

    if mode == "dashboard":
        generate_dashboard()
        return

    # FIX: backtest mode now commits state to GitHub before returning
    if mode == "backtest":
        days = int(os.environ.get("BACKTEST_DAYS", "180"))
        bt   = run_backtest(lookback_days=days)
        if bt:
            run_auto_adjust(backtest=bt)
        run_recent_regime_backtest(days=60)
        if now.weekday() == 6:
            run_research_digest()
        commit_state_to_github()   # FIX: was missing, backtest results never reached repo
        return

    if mode == "recent_regime":
        days = int(os.environ.get("BACKTEST_DAYS", "60"))
        run_recent_regime_backtest(days=days)
        commit_state_to_github()
        return

    if mode == "auto_adjust":
        bt_file = STATE_DIR / "backtest_latest.json"
        bt = json.loads(bt_file.read_text()) if bt_file.exists() else {}
        run_auto_adjust(backtest=bt)
        commit_state_to_github()
        return

    if mode == "research":
        run_research_digest()
        commit_state_to_github()
        return

    if mode == "review" or (session == "closed" and now.hour == 16):
        generate_daily_review()
        commit_state_to_github()
        if now.weekday() == 4:
            log("Friday — running weekly auto-adjust...")
            bt_file = STATE_DIR / "backtest_latest.json"
            bt = json.loads(bt_file.read_text()) if bt_file.exists() else {}
            result = run_auto_adjust(backtest=bt)
            if result:
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

    if mode == "status":
        acc = get_account()
        log(f"Account equity: ${acc['equity']:,.2f}")
        positions = get_open_positions()
        log(f"Open positions: {len(positions)}")
        for p in positions:
            log(f"  {p.get('symbol')} {p.get('side')} x{p.get('qty')} "
                f"@ ${float(p.get('avg_entry_price',0)):.2f} "
                f"P&L: ${float(p.get('unrealized_pl',0)):+.2f}")
        _tg(
            f"Status Check\n"
            f"Equity: ${acc['equity']:,.2f}\n"
            f"Open positions: {len(positions)}\n"
            + "\n".join([
                f"  {p.get('symbol')} {p.get('side')} x{p.get('qty')} "
                f"P&L: ${float(p.get('unrealized_pl',0)):+.2f}"
                for p in positions
            ])
        )
        log("Committing state to GitHub for dashboard...")
        commit_state_to_github()
        return

    # Main scan mode
    if session not in ("open", "pre_market"):
        log(f"Market {session} — no trading. Exiting.")
        return

    if now.hour == 9 and now.minute < 40:
        send_startup_ping()

    acc    = get_account()
    equity = acc.get("equity", ACCOUNT_EQUITY)
    log(f"Account equity: ${equity:,.2f} | Buying power: ${acc.get('buying_power',0):,.2f}")

    if SMALL_ACCT_EQUITY > 0 and equity > SMALL_ACCT_EQUITY:
        log(f"Small-account mode: sizing as ${SMALL_ACCT_EQUITY:,.2f} "
            f"(real equity ${equity:,.2f}) | pos cap {SMALL_ACCT_MAX_POS_PCT:.0%}", "WARN")
        equity = SMALL_ACCT_EQUITY

    if OPTIONS_ENABLED:
        log(f"Options-only mode ON | {OPTIONS_MIN_DTE}-{OPTIONS_MAX_DTE} DTE | "
            f"{OPTIONS_MONEYNESS.upper()} strike", "WARN")
        if not options_trading_enabled():
            log("Options NOT approved on this Alpaca account (level < 1) — option "
                "orders will be rejected. Enable options in the Alpaca dashboard.", "ERROR")

    if check_daily_loss(equity):
        log("Kill switch active — no new trades today", "WARN")
        alert_kill_switch(
            sum(t.get("pnl",0) for t in load_trades()
                if t.get("closed_at","")[:10] == date.today().isoformat()) / equity,
            equity
        )
        return

    macro_alerts = [(sym, t) for sym, t in tickers.items() if t.macro_alert]
    if macro_alerts:
        # Dedup: only send macro alert once per day, not every 5-min cycle
        alert_flag = STATE_DIR / f"macro_alert_{date.today().isoformat()}.flag"
        if not alert_flag.exists():
            alert_lines = []
            seen_triggers = set()
            for sym, t in macro_alerts[:5]:
                high = [m for m in t.macro_triggers if m["impact"] == "high"]
                for m in high[:2]:
                    key = m["trigger"]
                    if key not in seen_triggers:
                        seen_triggers.add(key)
                        alert_lines.append(
                            f"{m['trigger'].upper()} on {sym}\n"
                            f"{m['note']}\n"
                            f"{m['headline'][:80]}"
                        )
            if alert_lines:
                _tg("MACRO TRIGGER ALERT\n\n" + "\n\n".join(alert_lines[:5]) +
                    f"\n\nVIX: {macro.vix:.1f} | Regime: {macro.market_regime}")
                alert_flag.write_text(date.today().isoformat())
                log(f"Macro alert sent — suppressed for rest of today")
        else:
            log("Macro alert already sent today — skipping")

    run_position_monitor()

    max_pos = SMALL_ACCT_MAX_POSITIONS if SMALL_ACCT_EQUITY > 0 else RISK["max_open_positions"]
    open_positions = get_open_positions()
    log(f"Open positions: {len(open_positions)}/{max_pos}")
    if len(open_positions) >= max_pos:
        log("Max positions reached — not scanning for new entries")
        commit_state_to_github()
        return

    if session not in ("open",):
        log(f"Session is '{session}' — scanning only, no order execution")
        commit_state_to_github()
        return

    signals = scan_all()

    # Load near-misses for summary (saved inside scan_all)
    nm_file = STATE_DIR / "near_miss.json"
    near_misses = json.loads(nm_file.read_text()).get("near_misses", []) if nm_file.exists() else []

    # Send scan summary every 30 min (confirms bot alive + shows what's building)
    send_scan_summary(signals, near_misses)

    if not signals:
        log("No signals this run — exiting")
        commit_state_to_github()
        return

    slots  = max_pos - len(open_positions)
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
            rps = abs(scored["entry"] - scored["stop"])
            if OPTIONS_ENABLED:
                success, shares = execute_option_signal(scored, equity)
            else:
                success, shares = execute_signal(scored, equity)
            if success:
                risk_amt = shares * rps
                alert_trade_open(scored, shares, risk_amt)
                filled += 1
                open_positions.append({"symbol": sig["symbol"]})

    log(f"\nRun complete: {filled} new position(s) opened")
    commit_state_to_github()


# ══════════════════════════════════════════════════════════════════════════════
# TELEGRAM ALERTS
# ══════════════════════════════════════════════════════════════════════════════

def _tg_escape(text: str) -> str:
    """Escape underscores within identifier strings for Telegram Markdown v1."""
    return re.sub(r'(?<=[^\s*`\[])_(?=[^\s*`\]])', r'\\_', text)


def _tg_send_raw(text: str) -> bool:
    """Single send attempt. Returns True on success."""
    safe = _tg_escape(text)
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": safe, "parse_mode": "Markdown"},
            timeout=10
        )
        if resp.status_code == 200:
            return True
        if resp.status_code == 400 and "parse" in resp.text.lower():
            # Markdown failed — strip formatting and retry as plain text
            clean = text.replace("*","").replace("_","").replace("`","")
            resp2 = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": clean},
                timeout=10
            )
            return resp2.status_code == 200
        log(f"Telegram HTTP {resp.status_code}: {resp.text[:120]}", "WARN")
        return False
    except Exception as e:
        log(f"Telegram send error: {e}", "WARN")
        return False


def _tg(text: str) -> None:
    """
    Send a Telegram message with:
    - Underscore escaping (prevents parse errors on ALPACA_CLOSED, trending_up etc)
    - Message splitting (Telegram max is 4096 chars)
    - Retry with backoff (3 attempts)
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return

    # Split long messages at newlines near the 4000-char limit
    MAX_LEN = 4000
    chunks = []
    if len(text) <= MAX_LEN:
        chunks = [text]
    else:
        remaining = text
        while remaining:
            if len(remaining) <= MAX_LEN:
                chunks.append(remaining)
                break
            # Find last newline before limit
            split_at = remaining.rfind("\n", 0, MAX_LEN)
            if split_at == -1:
                split_at = MAX_LEN
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:].lstrip("\n")

    for i, chunk in enumerate(chunks):
        prefix = f"(part {i+1}/{len(chunks)})\n" if len(chunks) > 1 else ""
        payload = prefix + chunk
        # Retry up to 3 times with backoff
        for attempt in range(3):
            if _tg_send_raw(payload):
                log(f"Telegram sent ({len(payload)} chars) attempt {attempt+1}")
                break
            wait = 2 ** attempt
            log(f"Telegram retry {attempt+1}/3 in {wait}s...", "WARN")
            time.sleep(wait)
        else:
            log(f"Telegram failed after 3 attempts — chunk {i+1}/{len(chunks)}", "ERROR")


def alert_signal(sig: dict):
    d   = "📈 LONG" if sig["direction"] == "long" else "📉 SHORT"
    ai  = f"\nAI: {sig.get('ai_score',0):.0f}/100 — {sig.get('ai_reasoning','')[:120]}" if sig.get("ai_score") else ""
    hl  = sig.get("headline_score", 0)
    hl_str = f"\nHeadlines: {hl:+.0f} ({'bullish' if hl > 20 else 'bearish' if hl < -20 else 'neutral'})" if hl != 0 else ""
    reddit_str = "\nReddit trending" if sig.get("reddit_trending") else ""
    macro_str = ""
    t = tickers.get(sig["symbol"])
    if t and hasattr(t, "macro_triggers") and t.macro_triggers:
        high = [m for m in t.macro_triggers if m["impact"] == "high"]
        if high:
            macro_str = f"\nMACRO: {', '.join(m['trigger'].upper() for m in high[:3])}"
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
        f"{e} *TRADE CLOSED — {symbol}*\n"
        f"Reason: {reason}\n"
        f"Entry: ${entry:.2f}  Exit: ${close:.2f}\n"
        f"PnL: {pnl_pct:+.1f}%  (${pnl:+.2f})\n"
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
    dash  = f"\n[Dashboard]({DASHBOARD_URL})" if DASHBOARD_URL else ""
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
    mode = "PAPER" if PAPER_MODE else "LIVE"
    fg   = macro.fear_greed
    fg_str = f"F&G: {fg['score']:.0f} ({fg['rating'].replace('_',' ').title()})"
    dash = f"\n[Dashboard]({DASHBOARD_URL})" if DASHBOARD_URL else ""
    _tg(
        f"🤖 *Boticus started [{mode}]*\n"
        f"Session: {get_market_session()}  |  "
        f"VIX: {macro.vix:.1f}  |  Regime: {macro.market_regime}\n"
        f"{fg_str}  |  Watchlist: {len(get_watchlist())} tickers"
        f"{dash}"
    )


def send_scan_summary(signals: list, near_misses: list):
    """
    Send a brief scan result to Telegram every cycle.
    Even when 0 signals — confirms bot is alive and shows what's building.
    """
    now = datetime.now(ET)
    fg  = macro.fear_greed

    # Only send summary every 30 min to avoid spam (or always if signals found)
    summary_flag = STATE_DIR / f"scan_summary_{now.strftime('%Y-%m-%d %H')}{'30' if now.minute >= 30 else '00'}.flag"
    has_signals  = len(signals) > 0
    if not has_signals and summary_flag.exists():
        return  # Already sent this 30-min window
    summary_flag.write_text(now.isoformat())

    risk_str = {2:"RISK-ON", 1:"MILD-ON", 0:"NEUTRAL",
                -1:"MILD-OFF", -2:"RISK-OFF", -3:"EXTREME-OFF"}.get(macro.risk_score, str(macro.risk_score))

    lines = [
        f"📡 *Scan {now.strftime('%H:%M ET')}*",
        f"Regime: {macro.market_regime} | VIX: {macro.vix:.1f} | {risk_str}",
        f"F&G: {fg.get('score',50):.0f} ({fg.get('rating','neutral')})",
        f"Signals: {len(signals)} | Near-misses: {len(near_misses)}",
    ]

    if signals:
        lines.append("\n*Signals firing:*")
        for s in signals[:3]:
            d = "📈" if s["direction"] == "long" else "📉"
            lines.append(f"{d} {s['symbol']} criteria:{s['criteria']:.0f} R/R:{s['rr']:.1f}")

    if near_misses and not signals:
        top = sorted(near_misses, key=lambda x: x["criteria"], reverse=True)[:4]
        lines.append("\n*Close but not yet:*")
        for n in top:
            blocker = n["blockers"][0] if n["blockers"] else "criteria"
            lines.append(f"  {n['symbol']} {n['criteria']:.0f}/58 — {blocker}")

    _tg("\n".join(lines))


# ══════════════════════════════════════════════════════════════════════════════
# BACKTESTING MODULE
# ══════════════════════════════════════════════════════════════════════════════

def run_recent_regime_backtest(days: int = 60) -> dict:
    log(f"Running RECENT REGIME backtest: last {days} days only...")
    recent  = run_backtest(lookback_days=days, notify=False)
    full    = run_backtest(lookback_days=180, notify=False)
    if not recent or not full:
        log("Recent regime backtest: insufficient data", "WARN")
        return {}
    wr_recent  = recent.get("win_rate", 0)
    wr_full    = full.get("win_rate", 0)
    wr_delta   = wr_recent - wr_full
    exp_recent = recent.get("expectancy_pct", 0)
    exp_full   = full.get("expectancy_pct", 0)
    long_recent = recent.get("long_win_rate", 0)
    long_full   = full.get("long_win_rate", 0)
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
    regime_file = STATE_DIR / "regime_comparison.json"
    regime_file.write_text(json.dumps(result, indent=2))
    delta_str = "up" if wr_delta > 5 else "down" if wr_delta < -5 else "flat"
    _tg(
        f"🔬 *Recent Regime Analysis ({days}-day vs 180-day)*\n\n"
        f"Recent ({days}d): {wr_recent:.1f}% WR | {exp_recent:+.2f}% expectancy\n"
        f"Historical (180d): {wr_full:.1f}% WR | {exp_full:+.2f}% expectancy\n"
        f"Delta: {wr_delta:+.1f}% WR ({delta_str})\n\n"
        f"Verdict: {regime_verdict}\n\n"
        f"Long WR: {long_recent:.1f}% recent vs {long_full:.1f}% historical"
    )
    log(f"Recent regime: {wr_recent:.1f}% WR vs {wr_full:.1f}% historical ({wr_delta:+.1f}%)")
    return result


def run_backtest(symbols: list = None, lookback_days: int = 180,
                 notify: bool = True, save: bool = True) -> dict:
    symbols = symbols or get_watchlist()
    log(f"Running backtest: {len(symbols)} symbols, {lookback_days} days lookback")
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
                long_signal = (
                    price > sma_50 > sma_200 and
                    RISK["rsi_min"] <= rsi <= RISK["rsi_max"] and
                    atr_pct <= RISK["atr_pct_max"]
                )
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
                    regime = ("trending_up"   if price > sma_50 > sma_200 else
                              "trending_down" if price < sma_50 < sma_200 else "ranging")
                    sym_trades.append({
                        "symbol": symbol, "date": str(dates[i].date()),
                        "direction": direction, "entry": round(entry, 2),
                        "stop": round(stop, 2), "target": round(target, 2),
                        "exit": round(exit_price, 2), "exit_day": exit_day,
                        "outcome": outcome, "pnl_pct": round(pnl_pct, 2),
                        "rsi": round(rsi, 1), "atr_pct": round(atr_pct, 4),
                        "vol_ratio": round(vol_ratio, 2), "regime": regime,
                    })
            all_trades.extend(sym_trades)
            wins   = [t for t in sym_trades if t["outcome"] == "target"]
            losses = [t for t in sym_trades if t["outcome"] == "stop"]
            if sym_trades:
                log(f"    {symbol}: {len(sym_trades)} signals | W:{len(wins)} L:{len(losses)} | WR:{len(wins)/len(sym_trades)*100:.0f}%")
            else:
                log(f"    {symbol}: 0 signals")
        except Exception as e:
            log(f"  Backtest error {symbol}: {e}", "ERROR")
    if not all_trades:
        log("Backtest: no trades generated", "WARN")
        return {}
    wins    = [t for t in all_trades if t["outcome"] == "target"]
    losses  = [t for t in all_trades if t["outcome"] == "stop"]
    timeout = [t for t in all_trades if t["outcome"] == "timeout"]
    total   = len(all_trades)
    wr      = len(wins) / total * 100
    avg_win  = sum(t["pnl_pct"] for t in wins)   / len(wins)   if wins   else 0
    avg_loss = sum(t["pnl_pct"] for t in losses)  / len(losses) if losses else 0
    expectancy = (wr/100 * avg_win) + ((1-wr/100) * avg_loss)
    # TRUE per-trade EV: mean realized pnl across ALL trades, incl. timeouts at
    # their actual exit. `expectancy` above wrongly treats every timeout as if it
    # lost avg_loss, badly understating a trend system where ~37% time out near
    # breakeven. Optimize/read against mean_pnl_all, not `expectancy`.
    mean_pnl_all = sum(t["pnl_pct"] for t in all_trades) / total if total else 0
    regime_stats = {}
    for t in all_trades:
        r = t["regime"]
        if r not in regime_stats: regime_stats[r] = {"wins": 0, "losses": 0, "total": 0}
        regime_stats[r]["total"] += 1
        if t["outcome"] == "target": regime_stats[r]["wins"] += 1
        if t["outcome"] == "stop":   regime_stats[r]["losses"] += 1
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
        "mean_pnl_pct":   round(mean_pnl_all, 3),  # TRUE per-trade EV (incl. timeouts)
        "long_signals":   len(long_trades),
        "long_win_rate":  round(long_wr, 1),
        "short_signals":  len(short_trades),
        "short_win_rate": round(short_wr, 1),
        "regime_stats":   regime_stats,
        "rsi_win_rates":  rsi_wr,
        "volume_win_rates": vol_wr,
        "raw_trades":     all_trades,
    }
    log("=" * 60)
    log(f"BACKTEST RESULTS — {lookback_days} days | {total} signals")
    log("=" * 60)
    log(f"Win rate:    {wr:.1f}%  ({len(wins)}W / {len(losses)}L / {len(timeout)} timeout)")
    log(f"Avg win:     {avg_win:+.2f}%   Avg loss:  {avg_loss:+.2f}%")
    log(f"Expectancy:  {expectancy:+.2f}% per trade (win/loss only — understates)")
    log(f"Mean pnl:    {mean_pnl_all:+.3f}% per trade (TRUE EV, incl. timeouts)")
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
    if save:
        bt_file = STATE_DIR / "backtest_latest.json"
        bt_file.write_text(json.dumps(
            {k: v for k, v in summary.items() if k != "raw_trades"},
            indent=2, default=str
        ))
        log(f"Backtest saved to {bt_file}")
    if notify and TELEGRAM_TOKEN:
        best_regime = max(regime_stats, key=lambda r: regime_stats[r]["wins"]/regime_stats[r]["total"] if regime_stats[r]["total"] else 0) if regime_stats else "N/A"
        _tg(
            f"📊 *Backtest Complete — {lookback_days} days*\n"
            f"Signals: {total}  |  Win rate: {wr:.1f}%\n"
            f"Avg win: {avg_win:+.2f}%  |  Avg loss: {avg_loss:+.2f}%\n"
            f"Mean pnl: {mean_pnl_all:+.3f}% per trade (true EV)\n"
            f"Long WR: {long_wr:.1f}%  |  Short WR: {short_wr:.1f}%\n"
            f"Best regime: {best_regime}"
        )
    return summary


# ══════════════════════════════════════════════════════════════════════════════
# AUTO-ADJUST
# ══════════════════════════════════════════════════════════════════════════════

def _evaluate_overlay(overlay: dict, lookback_days: int, symbols: list) -> float | None:
    """Return true per-trade EV (mean_pnl_pct) for RISK with `overlay` applied,
    without mutating persisted state. Restores RISK afterward."""
    snapshot = {k: RISK[k] for k in overlay}
    try:
        RISK.update(overlay)
        bt = run_backtest(symbols=symbols, lookback_days=lookback_days,
                          notify=False, save=False)
        return bt.get("mean_pnl_pct") if bt else None
    except Exception as e:
        log(f"Overlay evaluation error: {e}", "WARN")
        return None
    finally:
        RISK.update(snapshot)


def stage_pending_overlay(adjustments: list) -> dict:
    """Turn AI adjustment suggestions into a validated, staged proposal.

    Only tunable signal params survive (risk controls are dropped). The candidate
    is walk-forward validated: it must beat the CURRENT params' true per-trade EV
    on a held-out recent window by VALIDATION_MARGIN before being written to the
    pending file for /apply. Returns the proposal dict (with validation stats)."""
    raw = {a.get("param"): a.get("suggested") for a in (adjustments or [])
           if a.get("param") in TUNABLE_BOUNDS}
    clean, notes = _clamp_overlay(raw)
    proposal = {"params": clean, "notes": notes,
                "proposed_at": datetime.now(ET).isoformat()}
    if not clean:
        proposal["validation"] = {"passed": False, "reason": "no tunable params suggested"}
        return proposal
    log(f"Validating candidate overlay {clean} on held-out {len(VALIDATION_SYMBOLS)}-symbol / 90d window...")
    base = _evaluate_overlay({},    90, VALIDATION_SYMBOLS)   # current params
    cand = _evaluate_overlay(clean, 90, VALIDATION_SYMBOLS)   # candidate params
    passed = (base is not None and cand is not None
              and cand > 0 and cand >= base + VALIDATION_MARGIN)
    proposal["validation"] = {
        "window_days": 90, "symbols": len(VALIDATION_SYMBOLS),
        "base_mean_pnl": base, "cand_mean_pnl": cand,
        "margin_required": VALIDATION_MARGIN, "passed": passed,
    }
    if passed:
        RISK_PENDING_FILE.write_text(json.dumps(proposal, indent=2))
        log(f"Overlay PASSED validation (base {base} -> cand {cand}) — staged for /apply")
    else:
        RISK_PENDING_FILE.write_text(json.dumps({"params": {}, "last_rejected": proposal}, indent=2))
        log(f"Overlay FAILED validation (base {base} -> cand {cand}) — not staged", "WARN")
    return proposal


def run_auto_adjust(backtest: dict = None, notify: bool = True, stage: bool = True) -> dict:
    feedback = load_feedback()
    bt       = backtest or {}
    log("Running auto-adjust analysis...")
    live_summary = ""
    bt_summary   = ""
    if feedback:
        wins   = [t for t in feedback if t["result"] == "win"]
        losses = [t for t in feedback if t["result"] == "loss"]
        total  = len(feedback)
        wr     = len(wins) / total * 100 if total else 0
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
            f"Mean pnl (TRUE EV, incl timeouts): {bt.get('mean_pnl_pct',0):+.3f}% per trade "
            f"— optimize against THIS, not the win/loss-only expectancy below\n"
            f"Expectancy (win/loss only, understates trend systems): {bt.get('expectancy_pct',0):+.2f}%\n"
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
        "Optimize for mean_pnl (TRUE per-trade EV), NOT the win/loss-only expectancy.\n"
        f"Only these SIGNAL params are auto-applied (each bounded): "
        f"{', '.join(f'{k}{TUNABLE_BOUNDS[k]}' for k in TUNABLE_BOUNDS)}. "
        "You may still comment on risk-control params, but they are human-only and "
        "will not be auto-applied.\n"
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
            model=OPUS_MODEL, max_tokens=800,
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
            log(f"  {adj['param']:30} {adj['current']} -> {adj['suggested']}")
            log(f"    Reason: {adj['reason']}")
        log("=" * 60)
        adj_file = STATE_DIR / "auto_adjust_latest.json"
        adj_file.write_text(json.dumps(result, indent=2))

        # Walk-forward validate the tunable subset and stage it for one-tap /apply
        proposal = stage_pending_overlay(adjustments) if stage else {"validation": {}}
        v = proposal.get("validation", {})
        result["proposal"] = proposal

        if notify and TELEGRAM_TOKEN and adjustments:
            adj_lines = "\n".join([
                f"- {a['param']}: {a['current']} -> {a['suggested']}"
                for a in adjustments[:5]
            ])
            if v.get("passed"):
                p_lines = "\n".join(f"- {k}: {val}" for k, val in proposal["params"].items())
                verdict = (
                    f"✅ *Validated* on held-out {v['window_days']}d: true EV "
                    f"{v['base_mean_pnl']:+.3f}% → {v['cand_mean_pnl']:+.3f}% per trade.\n\n"
                    f"*Staged for approval:*\n{p_lines}\n\n"
                    f"Reply /apply to go live, or ignore to keep current params.\n"
                    f"(Risk controls unchanged — signal params only.)"
                )
            elif proposal.get("params"):
                verdict = (
                    f"⚠️ Candidate did NOT beat current params on the held-out "
                    f"{v.get('window_days','?')}d window "
                    f"(EV {v.get('base_mean_pnl')} vs {v.get('cand_mean_pnl')}). "
                    f"Not staged — keeping current params."
                )
            else:
                verdict = "No tunable-param changes suggested. Not staged."
            _tg(
                f"🔧 *Auto-Adjust* (confidence: {confidence})\n\n"
                f"{adj_lines}\n\n"
                f"Priority: {priority}\n\n"
                f"_{summary}_\n\n{verdict}"
            )
        return result
    except json.JSONDecodeError as e:
        log(f"Auto-adjust parse error: {e}", "ERROR")
    except Exception as e:
        log(f"Auto-adjust error: {e}", "ERROR")
    return {}


# ══════════════════════════════════════════════════════════════════════════════
# STATE COMMIT
# ══════════════════════════════════════════════════════════════════════════════

def commit_state_to_github():
    """
    Commits bot state files back to the GitHub repo so Streamlit Cloud can read live data.
    FIX: Header is "2022-11-28" — this is GitHub's API version label (released Nov 28 2022),
    NOT the current year. It does not change. The original had "2026-11-28" which caused 400 errors.
    """
    if not GITHUB_TOKEN or not GITHUB_REPO:
        log("State commit: GITHUB_TOKEN or GITHUB_REPOSITORY not set — skipping", "WARN")
        return
    import base64
    api_base = f"https://api.github.com/repos/{GITHUB_REPO}/contents"
    headers  = {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",   # Fixed: was "2026-11-28"
    }
    files_to_commit = [
        "trades.json", "feedback.json", "backtest_latest.json",
        "auto_adjust_latest.json", "bot.log", "watchlist.json",
        "research_digest.json", "fed_insights.json", "tg_offset.json",
        "paused.json", "near_miss.json", "last_run.json",
        "risk_overrides.json", "risk_overrides_pending.json", "risk_overrides_prev.json",
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
                log(f"  Committed {filename} to repo")
            elif r.status_code == 409:
                log(f"  SHA conflict on {filename} — retrying with fresh SHA", "WARN")
                r2 = requests.get(f"{api_base}/{repo_path}", headers=headers, timeout=10)
                if r2.status_code == 200:
                    payload["sha"] = r2.json().get("sha")
                    r3 = requests.put(f"{api_base}/{repo_path}", headers=headers,
                                      json=payload, timeout=15)
                    if r3.status_code in (200, 201):
                        committed += 1
                        log(f"  Committed {filename} (retry)")
                    else:
                        log(f"  Commit failed {filename} (retry): {r3.status_code}", "WARN")
            else:
                log(f"  Commit failed {filename}: {r.status_code} — {r.text[:150]}", "WARN")
        except Exception as e:
            log(f"  Commit error {filename}: {e}", "WARN")
    log(f"State commit complete: {committed}/{len(files_to_commit)} files")


# ══════════════════════════════════════════════════════════════════════════════
# STREAMLIT DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

def generate_dashboard():
    """Write dashboard.py and commit to GitHub."""
    dashboard_source = """
import streamlit as st, json, pandas as pd
from pathlib import Path
from datetime import datetime, date, timedelta

st.set_page_config(page_title="Boticus", page_icon="🤖", layout="wide")
st.markdown('''<style>
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap");
html,body,[class*="css"]{font-family:"Inter",sans-serif!important}
.block-container{padding:1rem 1.5rem 3rem!important;max-width:1400px!important}
[data-testid="metric-container"]{background:rgba(128,128,128,0.07);border:1px solid rgba(128,128,128,0.12);border-radius:12px;padding:14px 18px!important}
[data-testid="stMetricValue"]{font-size:24px!important;font-weight:700!important}
.card{border-radius:8px;padding:10px 14px;margin:5px 0;font-size:13px;border-left:3px solid}
.cwin{background:rgba(34,197,94,0.08);border-color:#22c55e}
.closs{background:rgba(239,68,68,0.08);border-color:#ef4444}
.calert{background:rgba(234,179,8,0.08);border-color:#eab308}
.cinfo{background:rgba(59,130,246,0.08);border-color:#3b82f6}
.cnear{background:rgba(168,85,247,0.08);border-color:#a855f7}
hr{opacity:.15!important}
</style>''', unsafe_allow_html=True)

st.markdown("<script>setTimeout(()=>window.location.reload(),180000)</script>", unsafe_allow_html=True)

D = Path("bot_state")
def load(f):
    p = D/f; return json.loads(p.read_text()) if p.exists() else None

trades   = load("trades.json")   or []
feedback = load("feedback.json") or []
bt       = load("backtest_latest.json")
adj      = load("auto_adjust_latest.json")
wl       = load("watchlist.json")
nm       = load("near_miss.json")
digest   = load("research_digest.json")

open_t    = [t for t in trades if t.get("status")=="open"]
today_str = date.today().isoformat()
week_str  = (date.today()-timedelta(days=7)).isoformat()
wins      = [t for t in feedback if t.get("result")=="win"]
losses    = [t for t in feedback if t.get("result")=="loss"]
today_fb  = [t for t in feedback if t.get("date","")==today_str]
week_fb   = [t for t in feedback if t.get("date","")>=week_str]
total_pnl = sum(t.get("pnl_dollar",0) for t in feedback)
today_pnl = sum(t.get("pnl_dollar",0) for t in today_fb)
week_pnl  = sum(t.get("pnl_dollar",0) for t in week_fb)
wr        = len(wins)/len(feedback)*100 if feedback else 0
avg_win   = sum(t.get("pnl_pct",0) for t in wins)/len(wins) if wins else 0
avg_loss  = sum(t.get("pnl_pct",0) for t in losses)/len(losses) if losses else 0

# ── Header ───────────────────────────────────────────────────────
st.title("🤖 Boticus")
st.caption(f"Refreshes every 3 min · {datetime.now().strftime('%b %d %H:%M ET')}")

lr = load("last_run.json")
if lr:
    ts   = lr.get("timestamp","")[:16].replace("T"," ")
    reg  = lr.get("regime","?")
    vix  = lr.get("vix", 0)
    risk = lr.get("risk_score", 0)
    sigs = lr.get("signals", 0)
    rstr = {2:"RISK-ON",1:"MILD-ON",0:"NEUTRAL",-1:"MILD-OFF",-2:"RISK-OFF",-3:"EXTREME-OFF"}.get(risk,"?")
    col  = "#22c55e" if sigs > 0 else "#aaa"
    st.markdown(
        f'<div style="font-size:13px;color:{col};margin:0 0 8px">' +
        f'Last run: <b>{ts} ET</b> · {reg} · VIX {vix:.1f} · {rstr} · {sigs} signal(s)</div>',
        unsafe_allow_html=True
    )
else:
    st.markdown('<div style="font-size:13px;color:#aaa;margin:0 0 8px">Waiting for first bot run...</div>',
                unsafe_allow_html=True)

# ── Top metrics ──────────────────────────────────────────────────
c1,c2,c3,c4,c5,c6 = st.columns(6)
c1.metric("Positions", f"{len(open_t)}/6")
c2.metric("Win Rate",  f"{wr:.1f}%", delta=f"{wr-50:.1f}%")
c3.metric("Today P&L", f"${today_pnl:+,.0f}")
c4.metric("Week P&L",  f"${week_pnl:+,.0f}")
c5.metric("Avg Win",   f"{avg_win:+.1f}%")
c6.metric("Avg Loss",  f"{avg_loss:+.1f}%")

st.divider()

# ── Near-misses alert bar ─────────────────────────────────────────
if nm and nm.get("near_misses"):
    top_nm = nm["near_misses"][:3]
    updated = nm.get("updated_at","")[:16].replace("T"," ")
    nm_str  = "  |  ".join([
        f"{n['symbol']} {n['criteria']:.0f}/58 — {n['blockers'][0] if n['blockers'] else 'criteria'}"
        for n in top_nm
    ])
    st.markdown(
        f'<div class="card calert">📡 <b>Near-misses as of {updated} ET:</b>  {nm_str}</div>',
        unsafe_allow_html=True
    )

# ── Tabs ──────────────────────────────────────────────────────────
t1,t2,t3,t4,t5,t6,t7,t8 = st.tabs(
    ["📊 Positions","📈 P&L","🔭 Near-Misses","📉 Backtest","🔧 Adjust","📋 Log","🗂 Watchlist","📚 Research"]
)

# ── Tab 1: Positions ─────────────────────────────────────────────
with t1:
    if open_t:
        for pos in open_t:
            unp  = pos.get("unrealized_pct",0)
            icon = "🟢" if unp>0 else "🔴" if unp<0 else "⚪"
            trail = " 📌" if pos.get("trailing_stop") else ""
            with st.expander(f"{icon} **{pos.get('symbol')}**  {pos.get('direction','').upper()}  {unp:+.1f}%{trail}"):
                a,b,c,d = st.columns(4)
                a.metric("Entry",   f"${pos.get('entry_price',0):.2f}")
                b.metric("Current", f"${pos.get('current_price', pos.get('entry_price',0)):.2f}")
                c.metric("Stop",    f"${pos.get('stop_loss',0):.2f}")
                d.metric("Target",  f"${pos.get('take_profit',0):.2f}")
                e,f = st.columns(2)
                e.metric("Shares",   pos.get("shares",0))
                f.metric("AI Score", f"{pos.get('ai_score','—')}/100")
                st.caption(f"Opened: {pos.get('opened_at','')[:16].replace('T',' ')}  |  "
                          f"P&L: ${pos.get('unrealized_pl',0):+.2f}")
    else:
        st.markdown('<div class="card cinfo">No open positions right now — bot is scanning for entries.</div>',
                    unsafe_allow_html=True)
    st.divider()
    st.subheader("Today's Closed Trades")
    if today_fb:
        for t in today_fb:
            pnl = t.get("pnl_pct",0)
            st.markdown(
                f'<div class="card {"cwin" if pnl>0 else "closs"}">'
                f'{"🟢" if pnl>0 else "🔴"} <b>{t["symbol"]}</b> {t.get("direction","").upper()} | '
                f'{pnl:+.1f}% | ${t.get("pnl_dollar",0):+.0f} | {t.get("close_reason","—")}</div>',
                unsafe_allow_html=True
            )
    else:
        st.markdown('<div class="card cinfo">No completed trades today.</div>', unsafe_allow_html=True)

# ── Tab 2: P&L ──────────────────────────────────────────────────
with t2:
    if feedback:
        df = pd.DataFrame(feedback)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        df["cum_pnl"] = df["pnl_dollar"].cumsum()
        st.subheader("Cumulative P&L")
        st.line_chart(df.set_index("date")["cum_pnl"], use_container_width=True, height=220)
        col1, col2 = st.columns(2)
        with col1:
            if "regime" in df.columns:
                st.subheader("Win Rate by Regime")
                rg = df.groupby("regime").apply(
                    lambda x: pd.Series({"WR": f"{sum(v>0 for v in x['pnl_dollar'])/len(x)*100:.0f}%",
                                         "Trades": len(x),
                                         "P&L": f"${sum(x['pnl_dollar']):+.0f}"})
                ).reset_index()
                st.dataframe(rg, use_container_width=True, hide_index=True)
        with col2:
            st.subheader("Exit Breakdown")
            reasons = {"Target": "TARGET", "Stop": "STOP", "EOD": "EOD",
                       "Time": "TIME", "Emergency": "EMERGENCY", "Alpaca": "ALPACA"}
            ex = pd.DataFrame([{"Exit": k, "Count": sum(1 for t in feedback if v in t.get("close_reason",""))}
                               for k,v in reasons.items()])
            st.dataframe(ex, use_container_width=True, hide_index=True)
        st.subheader("All Trades")
        disp = df[["date","symbol","direction","pnl_pct","pnl_dollar","close_reason","regime"]].copy()
        disp = disp.sort_values("date", ascending=False)
        disp.columns = ["Date","Sym","Dir","P&L%","P&L$","Reason","Regime"]
        disp["P&L%"] = disp["P&L%"].map(lambda x: f"{x:+.1f}%")
        disp["P&L$"] = disp["P&L$"].map(lambda x: f"${x:+.0f}")
        disp["Date"] = disp["Date"].dt.strftime("%m/%d")
        st.dataframe(disp.head(50), use_container_width=True, hide_index=True)
    else:
        st.markdown('<div class="card cinfo">No closed trades yet.</div>', unsafe_allow_html=True)

# ── Tab 3: Near-Misses ──────────────────────────────────────────
with t3:
    st.subheader("Near-Miss Signals")
    st.caption("Tickers that passed hard gates but missed criteria threshold. Updated every scan.")
    if nm and nm.get("near_misses"):
        updated = nm.get("updated_at","")[:16].replace("T"," ")
        st.caption(f"Last scan: {updated} ET")
        for n in nm["near_misses"][:12]:
            pct  = n.get("change_pct", 0)
            crit = n.get("criteria", 0)
            gap  = 58 - crit  # how far from threshold (long min is 58)
            color = "cwin" if gap < 5 else "calert" if gap < 10 else "cnear"
            blocker = n["blockers"][0] if n.get("blockers") else "below threshold"
            st.markdown(
                f'<div class="card {color}">' +
                f'<b>{n["symbol"]}</b> {n.get("direction","long").upper()} — ' +
                f'Criteria: {crit:.0f}/58 (gap: {gap:.0f}) | ' +
                f'RSI: {n.get("rsi",0):.0f} | Vol: {n.get("vol",0):.1f}x | ' +
                f'Change: {pct:+.1f}% | Blocked by: {blocker}' +
                '</div>',
                unsafe_allow_html=True
            )
    else:
        st.markdown('<div class="card cinfo">No near-miss data yet — run a scan first.</div>',
                    unsafe_allow_html=True)

# ── Tab 4: Backtest ─────────────────────────────────────────────
with t4:
    if bt:
        a,b,c,d = st.columns(4)
        a.metric("Win Rate",   f"{bt.get('win_rate',0):.1f}%")
        b.metric("Expectancy", f"{bt.get('expectancy_pct',0):+.2f}%")
        c.metric("Long WR",    f"{bt.get('long_win_rate',0):.1f}%",
                 delta=f"{bt.get('long_signals',0)} signals")
        d.metric("Short WR",   f"{bt.get('short_win_rate',0):.1f}%",
                 delta=f"{bt.get('short_signals',0)} signals")
        col1, col2 = st.columns(2)
        with col1:
            if bt.get("regime_stats"):
                st.subheader("By Regime")
                rg = []
                for r,s in bt["regime_stats"].items():
                    tot = s["wins"]+s["losses"]
                    if tot: rg.append({"Regime":r,"WR":f"{s['wins']/tot*100:.0f}%","N":tot})
                st.dataframe(pd.DataFrame(rg), use_container_width=True, hide_index=True)
        with col2:
            if bt.get("rsi_win_rates"):
                st.subheader("RSI Buckets")
                rsi_df = pd.DataFrame([{"RSI":k,"WR":f"{v:.0f}%"} for k,v in bt["rsi_win_rates"].items()])
                st.dataframe(rsi_df, use_container_width=True, hide_index=True)
    else:
        st.markdown('<div class="card cinfo">No backtest data yet.</div>', unsafe_allow_html=True)

# ── Tab 5: Adjust ───────────────────────────────────────────────
with t5:
    if adj:
        conf = adj.get("confidence","").upper()
        icon = "🟢" if conf=="HIGH" else "🟡" if conf=="MEDIUM" else "🔴"
        st.markdown(f'<div class="card calert">{icon} <b>Confidence: {conf}</b><br>{adj.get("summary","")}</div>',
                    unsafe_allow_html=True)
        st.write(f"**Priority:** {adj.get('priority_change','')}")
        for a in adj.get("adjustments",[]):
            st.markdown(
                f'<div class="card calert">🔧 <b>{a["param"]}</b>: {a["current"]} → <b>{a["suggested"]}</b>' +
                f'<br><span style="opacity:.7;font-size:12px">{a["reason"]}</span></div>',
                unsafe_allow_html=True
            )
        st.warning("Edit RISK dict in bot.py to apply changes.")
    else:
        st.markdown('<div class="card cinfo">No auto-adjust data yet.</div>', unsafe_allow_html=True)

# ── Tab 6: Log ──────────────────────────────────────────────────
with t6:
    log_path = D/"bot.log"
    if log_path.exists():
        lines = log_path.read_text().strip().split("\n")[-100:]
        colored = []
        for line in lines:
            if "ERROR"    in line: colored.append(f"🔴 {line}")
            elif "WARN"   in line: colored.append(f"🟡 {line}")
            elif "APPROVED" in line: colored.append(f"✅ {line}")
            elif "REJECTED" in line: colored.append(f"❌ {line}")
            elif "SIGNAL"  in line: colored.append(f"📡 {line}")
            elif "CLOSED"  in line: colored.append(f"💰 {line}")
            elif "EMERGENCY" in line: colored.append(f"🚨 {line}")
            elif "Trailing" in line: colored.append(f"📌 {line}")
            elif "Near-miss" in line: colored.append(f"🔭 {line}")
            elif "Macro alert" in line: colored.append(f"🟣 {line}")
            else: colored.append(f"   {line}")
        st.code("\n".join(colored), language="text")
    else:
        st.markdown('<div class="card cinfo">No log yet.</div>', unsafe_allow_html=True)

# ── Tab 7: Watchlist ────────────────────────────────────────────
with t7:
    if wl:
        core    = wl.get("core",[])
        active  = wl.get("active",[])
        dynamic = [t for t in active if t not in core]
        a,b,c = st.columns(3)
        a.metric("Total",   len(active))
        b.metric("Core",    len(core))
        c.metric("Dynamic", len(dynamic))
        st.caption(f"Updated: {wl.get('updated_at','')[:16].replace('T',' ')}")
        st.subheader("Core (always scanned)")
        st.markdown(f'<div class="card cinfo">{", ".join(core)}</div>', unsafe_allow_html=True)
        if dynamic:
            st.subheader("Dynamic (today\'s movers)")
            st.markdown(f'<div class="card cwin">{", ".join(dynamic)}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="card cinfo">Watchlist not loaded yet.</div>', unsafe_allow_html=True)

# ── Tab 8: Research ─────────────────────────────────────────────
with t8:
    if digest:
        fg = digest.get("fear_greed",{})
        st.metric("Fear & Greed", f"{fg.get('score',50):.0f}/100 ({fg.get('rating','neutral')})",
                  delta=f"{fg.get('change',0):+.1f} from yesterday")
        trending = digest.get("trending",[])
        if trending:
            st.markdown(f'<div class="card cinfo"><b>Retail trending:</b> {", ".join(trending[:12])}</div>',
                        unsafe_allow_html=True)
        insights = digest.get("insights",[])
        if insights:
            st.subheader("Insights")
            for i in insights[:6]:
                conf  = i.get("confidence","")
                color = "cwin" if conf=="high" else "calert" if conf=="medium" else "cnear"
                st.markdown(
                    f'<div class="card {color}">{i.get("finding","")}' +
                    (f' — <i>{i.get("actionable","")}</i>' if i.get("actionable") else "") +
                    f' [{conf}]</div>', unsafe_allow_html=True
                )
        st.caption(f"Digest from: {digest.get('updated_at','')[:10]}")
    else:
        st.markdown('<div class="card cinfo">No research digest yet. Trigger mode=research to generate.</div>',
                    unsafe_allow_html=True)
"""

    dash_file = Path("dashboard.py")
    dash_file.write_text(dashboard_source)
    log(f"Dashboard v2 written to {dash_file}")

    import base64
    if GITHUB_TOKEN and GITHUB_REPO:
        try:
            api_url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/dashboard.py"
            headers = {
                "Authorization": f"Bearer {GITHUB_TOKEN}",
                "Accept":        "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            b64 = base64.b64encode(dash_file.read_bytes()).decode()
            sha = None
            r = requests.get(api_url, headers=headers, timeout=8)
            if r.status_code == 200:
                sha = r.json().get("sha")
            payload = {
                "message": f"bot: dashboard v2 [{datetime.now(ET).strftime('%Y-%m-%d %H:%M ET')}]",
                "content": b64, "branch": "main",
            }
            if sha: payload["sha"] = sha
            r = requests.put(api_url, headers=headers, json=payload, timeout=15)
            log(f"Dashboard commit: {r.status_code}")
        except Exception as e:
            log(f"Dashboard commit error: {e}", "WARN")

    _tg("Dashboard v2 deployed — 8 tabs, near-miss panel, scan summary, research digest")
    return str(dash_file)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        import traceback
        print("FATAL ERROR: " + str(e))
        traceback.print_exc()
        raise
