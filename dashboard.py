
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

# ── Next scan countdown ───────────────────────────────────────────────────────
from datetime import datetime as _dt
try:
    import pytz as _pytz
    _ET  = _pytz.timezone("America/New_York")
    _now = _dt.now(_ET)
except:
    import zoneinfo as _zi
    _ET  = _zi.ZoneInfo("America/New_York")
    _now = _dt.now(_ET)

_h, _m, _s  = _now.hour, _now.minute, _now.second
_weekday     = _now.weekday()
_total_mins  = _h * 60 + _m
_market_open = _total_mins >= 9 * 60 + 30
_mkt_close   = _total_mins < 16 * 60
_is_market   = _weekday < 5 and _market_open and _mkt_close

if _is_market:
    _secs_left = (10 - (_m % 10)) * 60 - _s
    _mm = _secs_left // 60
    _ss = _secs_left % 60
    _urgent = _secs_left <= 90
    _color  = "#22c55e" if _urgent else "#aaa"
    st.markdown(
        f'<div style="font-size:13px;color:{_color};margin:0 0 12px">'
        f'🟢 Market open &nbsp;·&nbsp; Next scan in '
        f'<b>{_mm}:{str(_ss).zfill(2)}</b>'
        f'{"  ⚡ scanning soon" if _urgent else ""}'
        f'</div>',
        unsafe_allow_html=True
    )
else:
    _reason = "Weekend" if _weekday >= 5 else ("Pre-market" if not _market_open else "After hours")
    st.markdown(
        f'<div style="font-size:13px;color:#555;margin:0 0 12px">'
        f'🔴 {_reason} — bot paused'
        f'</div>',
        unsafe_allow_html=True
    )

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
        lines = log_path.read_text().strip().split("\n")[-80:]
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
        st.code("\n".join(colored), language="text")
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

