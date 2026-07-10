import streamlit as st, json, pandas as pd, altair as alt
from pathlib import Path
from datetime import datetime, timedelta

st.set_page_config(page_title="Boticus", page_icon="🤖", layout="wide")

# ── Time (bot writes all timestamps in US/Eastern) ───────────────
try:
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")
except Exception:
    ET = None
def now_et():
    return datetime.now(ET) if ET else datetime.now()

# ── Theme ────────────────────────────────────────────────────────
POS, NEG, WARN, INFO, VIOLET, TEAL = "#22c55e", "#ef4444", "#f59e0b", "#3b82f6", "#a855f7", "#14b8a6"
st.markdown('''<style>
@import url("https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap");
html,body,[class*="css"]{font-family:"Inter",system-ui,sans-serif!important}
.block-container{padding:1rem 1.6rem 3rem!important;max-width:1440px!important}
[data-testid="stMetricValue"]{font-size:22px!important;font-weight:700!important;font-variant-numeric:tabular-nums}
[data-testid="stMetricLabel"]{opacity:.65}
h1,h2,h3{letter-spacing:-.01em}
hr{opacity:.12!important;margin:.6rem 0!important}
.stTabs [data-baseweb="tab-list"]{gap:2px}
.stTabs [data-baseweb="tab"]{padding:6px 12px;font-size:13px}

/* stat tiles */
.tiles{display:grid;grid-template-columns:repeat(6,1fr);gap:10px;margin:2px 0 4px}
@media(max-width:1100px){.tiles{grid-template-columns:repeat(3,1fr)}}
@media(max-width:640px){.tiles{grid-template-columns:repeat(2,1fr)}}
.tile{background:rgba(128,128,128,0.06);border:1px solid rgba(128,128,128,0.14);
  border-radius:14px;padding:12px 15px;position:relative;overflow:hidden}
.tile .lab{font-size:11px;text-transform:uppercase;letter-spacing:.06em;opacity:.55;font-weight:600}
.tile .val{font-size:25px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1.15;margin-top:3px}
.tile .sub{font-size:11.5px;opacity:.6;margin-top:2px;font-variant-numeric:tabular-nums}
.tile::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--ac,#888)}

/* pills */
.pill{display:inline-block;padding:2px 10px;border-radius:999px;font-size:12px;font-weight:600;
  border:1px solid;vertical-align:middle}

/* cards */
.card{border-radius:10px;padding:10px 14px;margin:5px 0;font-size:13px;border-left:3px solid;
  background:rgba(128,128,128,0.05)}
.cwin{border-color:''' + POS + '''}
.closs{border-color:''' + NEG + '''}
.calert{border-color:''' + WARN + '''}
.cinfo{border-color:''' + INFO + '''}
.cnear{border-color:''' + VIOLET + '''}

/* progress bars for positions */
.bar{height:7px;border-radius:6px;background:rgba(128,128,128,0.18);position:relative;margin:6px 0 2px}
.bar>span{position:absolute;top:0;bottom:0;left:0;border-radius:6px}
</style>''', unsafe_allow_html=True)

# auto-refresh the parent page every 3 min (iframe -> parent)
try:
    st.iframe("<script>setTimeout(()=>{try{window.parent.location.reload()}catch(e){location.reload()}},180000)</script>", height=0)
except Exception:
    pass

D = Path("bot_state")
def load(f):
    p = D/f
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except Exception:
        return None

trades   = load("trades.json")   or []
feedback = load("feedback.json") or []
bt       = load("backtest_latest.json")
adj      = load("auto_adjust_latest.json")
wl       = load("watchlist.json")
nm       = load("near_miss.json")
digest   = load("research_digest.json")

open_t    = [t for t in trades if t.get("status") == "open"]
today_et  = now_et().date()
today_str = today_et.isoformat()
week_str  = (today_et - timedelta(days=7)).isoformat()
wins      = [t for t in feedback if t.get("result") == "win"]
losses    = [t for t in feedback if t.get("result") == "loss"]
today_fb  = [t for t in feedback if t.get("date", "") == today_str]
week_fb   = [t for t in feedback if t.get("date", "") >= week_str]

total_pnl = sum(t.get("pnl_dollar", 0) for t in feedback)
today_pnl = sum(t.get("pnl_dollar", 0) for t in today_fb)
week_pnl  = sum(t.get("pnl_dollar", 0) for t in week_fb)
wr        = len(wins) / len(feedback) * 100 if feedback else 0
avg_win   = sum(t.get("pnl_pct", 0) for t in wins) / len(wins) if wins else 0
avg_loss  = sum(t.get("pnl_pct", 0) for t in losses) / len(losses) if losses else 0

gross_win  = sum(t.get("pnl_dollar", 0) for t in wins)
gross_loss = -sum(t.get("pnl_dollar", 0) for t in losses)
profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)
expectancy = total_pnl / len(feedback) if feedback else 0
payoff = (avg_win / abs(avg_loss)) if avg_loss else 0
best   = max((t.get("pnl_dollar", 0) for t in feedback), default=0)
worst  = min((t.get("pnl_dollar", 0) for t in feedback), default=0)

# equity curve + max drawdown (chronological realized P&L)
fb_sorted = sorted(feedback, key=lambda t: (t.get("date", ""), t.get("symbol", "")))
equity, peak, max_dd, cum = [], 0.0, 0.0, 0.0
for t in fb_sorted:
    cum += t.get("pnl_dollar", 0)
    peak = max(peak, cum)
    max_dd = min(max_dd, cum - peak)
    equity.append({"n": len(equity) + 1, "date": t.get("date", ""), "cum": cum,
                   "dd": cum - peak, "pnl": t.get("pnl_dollar", 0), "symbol": t.get("symbol", "")})

# current streak (from most recent)
streak = 0
if fb_sorted:
    last = fb_sorted[-1].get("result")
    for t in reversed(fb_sorted):
        if t.get("result") == last:
            streak += 1
        else:
            break
streak_txt = f"{streak}{'W' if last == 'win' else 'L'}" if fb_sorted else "—"

def money(x):
    return f"-${abs(x):,.0f}" if x < 0 else f"${x:,.0f}"

def tile(lab, val, sub="", ac="#888"):
    sub = f'<div class="sub">{sub}</div>' if sub else ""
    return (f'<div class="tile" style="--ac:{ac}"><div class="lab">{lab}</div>'
            f'<div class="val">{val}</div>{sub}</div>')

# ── Header ───────────────────────────────────────────────────────
h1, h2 = st.columns([3, 2])
with h1:
    st.markdown("## 🤖 Boticus")
with h2:
    st.markdown(
        f'<div style="text-align:right;opacity:.55;font-size:12.5px;padding-top:14px">'
        f'auto-refresh 3 min · {now_et().strftime("%b %d · %H:%M ET")}</div>',
        unsafe_allow_html=True)

lr = load("last_run.json")
if lr:
    ts   = lr.get("timestamp", "")[:16].replace("T", " ")
    reg  = lr.get("regime", "?")
    vix  = lr.get("vix", 0) or 0
    risk = lr.get("risk_score", 0)
    sigs = lr.get("signals", lr.get("open_positions", 0))
    rmap = {2: ("RISK-ON", POS), 1: ("MILD-ON", POS), 0: ("NEUTRAL", INFO),
            -1: ("MILD-OFF", WARN), -2: ("RISK-OFF", NEG), -3: ("EXTREME-OFF", NEG)}
    rstr, rcol = rmap.get(risk, ("?", "#888"))
    vcol = POS if vix < 18 else WARN if vix < 26 else NEG
    def _pill(txt, col):
        return f'<span class="pill" style="color:{col};border-color:{col}55;background:{col}14">{txt}</span>'
    st.markdown(
        '<div style="margin:2px 0 10px;display:flex;gap:8px;flex-wrap:wrap;align-items:center">'
        + _pill(reg.replace("_", " ").upper(), INFO)
        + _pill(rstr, rcol)
        + _pill(f"VIX {vix:.1f}", vcol)
        + _pill(f"{sigs} signal(s)", POS if (sigs or 0) > 0 else "#888")
        + f'<span style="opacity:.45;font-size:12px">last run {ts} ET</span>'
        + '</div>', unsafe_allow_html=True)
else:
    st.markdown('<div style="opacity:.55;font-size:13px;margin-bottom:8px">Waiting for first bot run…</div>',
                unsafe_allow_html=True)

# ── Hero stat tiles ──────────────────────────────────────────────
pnl_col = POS if total_pnl >= 0 else NEG
pf_txt = "∞" if profit_factor == float("inf") else f"{profit_factor:.2f}"
pf_col = POS if profit_factor >= 1.5 else WARN if profit_factor >= 1 else NEG
wr_col = POS if wr >= 50 else WARN if wr >= 40 else NEG
dd_col = NEG if max_dd < 0 else "#888"
st.markdown('<div class="tiles">'
    + tile("Total P&amp;L", money(total_pnl), f"{len(feedback)} closed trades", pnl_col)
    + tile("Win Rate", f"{wr:.1f}%", f"{len(wins)}W / {len(losses)}L · streak {streak_txt}", wr_col)
    + tile("Profit Factor", pf_txt, f"payoff {payoff:.2f}·1", pf_col)
    + tile("Expectancy", f"{money(expectancy)}", "per trade", POS if expectancy >= 0 else NEG)
    + tile("Max Drawdown", money(max_dd), f"best {money(best)} · worst {money(worst)}", dd_col)
    + tile("Open Positions", f"{len(open_t)}/6", f"today {money(today_pnl)} · wk {money(week_pnl)}", INFO)
    + '</div>', unsafe_allow_html=True)

st.markdown("<hr>", unsafe_allow_html=True)

# ── Near-miss ticker bar ─────────────────────────────────────────
if nm and nm.get("near_misses"):
    top_nm = nm["near_misses"][:4]
    updated = nm.get("updated_at", "")[:16].replace("T", " ")
    nm_str = "   ·   ".join(
        f'<b>{n["symbol"]}</b> {n.get("criteria",0):.0f} — {(n["blockers"][0] if n.get("blockers") else "criteria")}'
        for n in top_nm)
    st.markdown(f'<div class="card calert">📡 <b>Near-misses</b> ({updated} ET)   {nm_str}</div>',
                unsafe_allow_html=True)

# ── Altair helpers ───────────────────────────────────────────────
def equity_chart(rows):
    df = pd.DataFrame(rows)
    base = alt.Chart(df).encode(
        x=alt.X("n:Q", title=None, axis=alt.Axis(labels=False, ticks=False, domain=False)))
    area = base.mark_area(
        line={"color": TEAL, "strokeWidth": 2},
        color=alt.Gradient(gradient="linear",
                           stops=[alt.GradientStop(color=TEAL, offset=1),
                                  alt.GradientStop(color="rgba(20,184,166,0)", offset=0)],
                           x1=1, x2=1, y1=1, y2=0),
    ).encode(
        y=alt.Y("cum:Q", title="Cumulative P&L ($)"),
        tooltip=[alt.Tooltip("symbol:N", title="Trade"),
                 alt.Tooltip("pnl:Q", title="P&L $", format="+,.0f"),
                 alt.Tooltip("cum:Q", title="Equity $", format="+,.0f")])
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(
        color="#888", strokeDash=[3, 3], opacity=.5).encode(y="y:Q")
    return (zero + area).properties(height=240)

def drawdown_chart(rows):
    df = pd.DataFrame(rows)
    return alt.Chart(df).mark_area(
        line={"color": NEG, "strokeWidth": 1.5},
        color=alt.Gradient(gradient="linear",
                           stops=[alt.GradientStop(color="rgba(239,68,68,0)", offset=1),
                                  alt.GradientStop(color=NEG, offset=0)],
                           x1=1, x2=1, y1=1, y2=0),
    ).encode(
        x=alt.X("n:Q", title=None, axis=alt.Axis(labels=False, ticks=False, domain=False)),
        y=alt.Y("dd:Q", title="Drawdown ($)"),
        tooltip=[alt.Tooltip("dd:Q", title="Drawdown $", format=",.0f")],
    ).properties(height=150)

def trade_bars(rows):
    df = pd.DataFrame(rows)
    return alt.Chart(df).mark_bar(cornerRadius=2, size=8).encode(
        x=alt.X("n:Q", title=None, axis=alt.Axis(labels=False, ticks=False, domain=False)),
        y=alt.Y("pnl:Q", title="Per-trade P&L ($)"),
        color=alt.condition(alt.datum.pnl >= 0, alt.value(POS), alt.value(NEG)),
        tooltip=[alt.Tooltip("symbol:N", title="Trade"),
                 alt.Tooltip("pnl:Q", title="P&L $", format="+,.0f")],
    ).properties(height=170)

def hbar(data, cat, val, note, color=INFO, fmt=".0f", suffix="%"):
    if not data:
        return None
    df = pd.DataFrame(data)
    bars = alt.Chart(df).mark_bar(cornerRadius=3, height=16, color=color).encode(
        x=alt.X(f"{val}:Q", title=None, scale=alt.Scale(domain=[0, max(df[val].max()*1.15, 1)])),
        y=alt.Y(f"{cat}:N", title=None, sort="-x"),
        tooltip=[alt.Tooltip(f"{cat}:N"), alt.Tooltip(f"{val}:Q", format=fmt),
                 alt.Tooltip(f"{note}:Q", title="N")])
    txt = bars.mark_text(align="left", dx=4, fontSize=11, color="#999").encode(
        text=alt.Text(f"{val}:Q", format=fmt))
    return (bars + txt).properties(height=max(len(df) * 34, 60))

# ── Tabs ─────────────────────────────────────────────────────────
t1, t2, t3, t4, t5, t6, t7, t8 = st.tabs(
    ["📊 Positions", "📈 Performance", "🔭 Near-Misses", "📉 Backtest",
     "🔧 Adjust", "📋 Log", "🗂 Watchlist", "📚 Research"])

# Tab 1: Positions
with t1:
    if open_t:
        for pos in open_t:
            unp   = pos.get("unrealized_pct", 0)
            icon  = "🟢" if unp > 0 else "🔴" if unp < 0 else "⚪"
            trail = " 📌" if pos.get("trailing_stop") else ""
            with st.expander(f"{icon}  **{pos.get('symbol')}**  ·  {pos.get('direction','').upper()}  ·  {unp:+.1f}%{trail}",
                             expanded=True):
                entry = pos.get("entry_price", 0) or 0
                cur   = pos.get("current_price", entry) or entry
                stop  = pos.get("stop_loss", 0) or 0
                tgt   = pos.get("take_profit", 0) or 0
                a, b, c, d = st.columns(4)
                a.metric("Entry", f"${entry:.2f}")
                b.metric("Current", f"${cur:.2f}", delta=f"{unp:+.1f}%")
                c.metric("Stop", f"${stop:.2f}")
                d.metric("Target", f"${tgt:.2f}")
                # progress from stop -> target (target may sit below stop for shorts)
                lo, hi = min(stop, tgt), max(stop, tgt)
                if hi > lo:
                    frac = max(0.0, min(1.0, (cur - lo) / (hi - lo)))
                    ep   = max(0.0, min(1.0, (entry - lo) / (hi - lo)))
                    left_stop = stop <= tgt  # long: stop on left; short: target on left
                    left_lab  = f"stop ${stop:.2f}"   if left_stop else f"target ${tgt:.2f}"
                    right_lab = f"target ${tgt:.2f}"   if left_stop else f"stop ${stop:.2f}"
                    st.markdown(
                        f'<div class="bar"><span style="width:{frac*100:.1f}%;'
                        f'background:{POS if unp>=0 else NEG}"></span>'
                        f'<span style="left:{ep*100:.1f}%;width:2px;background:#fff;opacity:.7"></span></div>'
                        f'<div style="display:flex;justify-content:space-between;font-size:11px;opacity:.55">'
                        f'<span>{left_lab}</span><span>entry ${entry:.2f}</span><span>{right_lab}</span></div>',
                        unsafe_allow_html=True)
                e, f, g = st.columns(3)
                e.metric("Shares", pos.get("shares", 0))
                f.metric("AI Score", f"{pos.get('ai_score','—')}/100")
                g.metric("Unrealized", money(pos.get("unrealized_pl", 0)))
                st.caption(f"Opened {pos.get('opened_at','')[:16].replace('T',' ')} · "
                           f"criteria {pos.get('criteria','—')} · regime {pos.get('regime','—')}")
    else:
        st.markdown('<div class="card cinfo">No open positions — bot is scanning for entries.</div>',
                    unsafe_allow_html=True)
    st.markdown("<hr>", unsafe_allow_html=True)
    st.subheader("Today's Closed Trades")
    if today_fb:
        for t in today_fb:
            pnl = t.get("pnl_pct", 0)
            st.markdown(
                f'<div class="card {"cwin" if pnl>0 else "closs"}">'
                f'{"🟢" if pnl>0 else "🔴"} <b>{t["symbol"]}</b> {t.get("direction","").upper()} · '
                f'{pnl:+.1f}% · {money(t.get("pnl_dollar",0))} · '
                f'<span style="opacity:.6">{t.get("close_reason","—")}</span></div>',
                unsafe_allow_html=True)
    else:
        st.markdown('<div class="card cinfo">No completed trades today.</div>', unsafe_allow_html=True)

# Tab 2: Performance
with t2:
    if feedback:
        st.subheader("Equity Curve")
        st.altair_chart(equity_chart(equity), width="stretch")
        cA, cB = st.columns([3, 2])
        with cA:
            st.caption("Drawdown (underwater)")
            st.altair_chart(drawdown_chart(equity), width="stretch")
        with cB:
            st.caption("Per-trade P&L")
            st.altair_chart(trade_bars(equity), width="stretch")

        st.markdown("<hr>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("By Regime")
            regs = {}
            for t in feedback:
                r = t.get("regime", "—")
                d = regs.setdefault(r, {"w": 0, "n": 0, "pnl": 0.0})
                d["n"] += 1
                d["w"] += 1 if t.get("pnl_dollar", 0) > 0 else 0
                d["pnl"] += t.get("pnl_dollar", 0)
            reg_rows = [{"Regime": r, "WR": v["w"]/v["n"]*100, "N": v["n"], "P&L": v["pnl"]}
                        for r, v in regs.items()]
            ch = hbar(reg_rows, "Regime", "WR", "N", color=INFO, fmt=".0f")
            if ch is not None:
                st.altair_chart(ch, width="stretch")
            rdf = pd.DataFrame(reg_rows)
            rdf["WR"] = rdf["WR"].map(lambda x: f"{x:.0f}%")
            rdf["P&L"] = rdf["P&L"].map(money)
            st.dataframe(rdf, width="stretch", hide_index=True)
        with c2:
            st.subheader("Exit Reasons")
            reasons = {"Target": "TARGET", "Stop": "STOP", "EOD": "EOD", "Time": "TIME",
                       "Emergency": "EMERGENCY", "Alpaca": "ALPACA"}
            ex_rows = [{"Exit": k, "Count": sum(1 for t in feedback if v in t.get("close_reason", ""))}
                       for k, v in reasons.items()]
            ex_rows = [r for r in ex_rows if r["Count"]]
            if ex_rows:
                ec = alt.Chart(pd.DataFrame(ex_rows)).mark_bar(cornerRadius=3, height=16, color=VIOLET).encode(
                    x=alt.X("Count:Q", title=None), y=alt.Y("Exit:N", sort="-x", title=None),
                    tooltip=["Exit", "Count"]).properties(height=max(len(ex_rows)*34, 60))
                st.altair_chart(ec, width="stretch")
            else:
                st.caption("No exit data.")

        st.markdown("<hr>", unsafe_allow_html=True)
        st.subheader("All Trades")
        df = pd.DataFrame(feedback)
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
        cols = [c for c in ["date", "symbol", "direction", "pnl_pct", "pnl_dollar", "close_reason", "regime"] if c in df.columns]
        disp = df[cols].sort_values("date", ascending=False).copy()
        rename = {"date": "Date", "symbol": "Sym", "direction": "Dir", "pnl_pct": "P&L%",
                  "pnl_dollar": "P&L$", "close_reason": "Reason", "regime": "Regime"}
        disp = disp.rename(columns=rename)
        if "P&L%" in disp: disp["P&L%"] = disp["P&L%"].map(lambda x: f"{x:+.1f}%")
        if "P&L$" in disp: disp["P&L$"] = disp["P&L$"].map(money)
        if "Date" in disp: disp["Date"] = disp["Date"].dt.strftime("%m/%d")
        st.dataframe(disp.head(60), width="stretch", hide_index=True)
    else:
        st.markdown('<div class="card cinfo">No closed trades yet.</div>', unsafe_allow_html=True)

# Tab 3: Near-Misses
with t3:
    st.subheader("Near-Miss Signals")
    st.caption("Tickers that passed hard gates but missed the criteria threshold. Updated every scan.")
    if nm and nm.get("near_misses"):
        updated = nm.get("updated_at", "")[:16].replace("T", " ")
        st.caption(f"Last scan: {updated} ET")
        for n in nm["near_misses"][:14]:
            crit = n.get("criteria", 0)
            color = "cwin" if crit >= 82 else "calert" if crit >= 78 else "cnear"
            blocker = n["blockers"][0] if n.get("blockers") else "below threshold"
            st.markdown(
                f'<div class="card {color}"><b>{n["symbol"]}</b> {n.get("direction","long").upper()} · '
                f'criteria <b>{crit:.0f}</b> · RSI {n.get("rsi",0):.0f} · Vol {n.get("vol",0):.1f}× · '
                f'{n.get("change_pct",0):+.1f}% · <span style="opacity:.65">blocked: {blocker}</span></div>',
                unsafe_allow_html=True)
    else:
        st.markdown('<div class="card cinfo">No near-miss data yet — run a scan first.</div>',
                    unsafe_allow_html=True)

# Tab 4: Backtest
with t4:
    if bt:
        a, b, c, d = st.columns(4)
        a.metric("Win Rate", f"{bt.get('win_rate',0):.1f}%")
        b.metric("Expectancy", f"{bt.get('expectancy_pct',0):+.2f}%")
        c.metric("Long WR", f"{bt.get('long_win_rate',0):.1f}%", delta=f"{bt.get('long_signals',0)} sig")
        d.metric("Short WR", f"{bt.get('short_win_rate',0):.1f}%", delta=f"{bt.get('short_signals',0)} sig")
        st.caption(f"{bt.get('total_signals',0):,} signals over {bt.get('period_days','?')} days · "
                   f"avg win {bt.get('avg_win_pct',0):+.2f}% · avg loss {bt.get('avg_loss_pct',0):+.2f}%")
        st.markdown("<hr>", unsafe_allow_html=True)
        c1, c2 = st.columns(2)
        with c1:
            if bt.get("regime_stats"):
                st.subheader("By Regime")
                rows = []
                for r, s in bt["regime_stats"].items():
                    tot = s.get("wins", 0) + s.get("losses", 0)
                    if tot:
                        rows.append({"Regime": r, "WR": s["wins"]/tot*100, "N": tot})
                ch = hbar(rows, "Regime", "WR", "N", color=INFO)
                if ch is not None:
                    st.altair_chart(ch, width="stretch")
        with c2:
            st.subheader("Win Rate by Signal Quality")
            rsi = [{"Bucket": f"RSI {k}", "WR": v, "N": 0} for k, v in (bt.get("rsi_win_rates") or {}).items()]
            vol = [{"Bucket": f"Vol {k}", "WR": v, "N": 0} for k, v in (bt.get("volume_win_rates") or {}).items()]
            combo = rsi + vol
            ch = hbar(combo, "Bucket", "WR", "N", color=TEAL)
            if ch is not None:
                st.altair_chart(ch, width="stretch")
            else:
                st.caption("No bucket data.")
    else:
        st.markdown('<div class="card cinfo">No backtest data yet.</div>', unsafe_allow_html=True)

# Tab 5: Adjust
with t5:
    if adj:
        conf = (adj.get("confidence", "") or "").upper()
        icon = "🟢" if conf == "HIGH" else "🟡" if conf == "MEDIUM" else "🔴"
        if adj.get("summary"):
            st.markdown(f'<div class="card calert">{icon} <b>Confidence: {conf or "—"}</b><br>{adj.get("summary","")}</div>',
                        unsafe_allow_html=True)
        if adj.get("priority_change"):
            st.write(f"**Priority:** {adj.get('priority_change','')}")
        for a in adj.get("adjustments", []):
            st.markdown(
                f'<div class="card calert">🔧 <b>{a.get("param","")}</b>: '
                f'{a.get("current","")} → <b>{a.get("suggested","")}</b>'
                f'<br><span style="opacity:.65;font-size:12px">{a.get("reason","")}</span></div>',
                unsafe_allow_html=True)
        st.warning("Edit the RISK dict in bot.py to apply changes.")
    else:
        st.markdown('<div class="card cinfo">No auto-adjust data yet.</div>', unsafe_allow_html=True)

# Tab 6: Log
with t6:
    log_path = D / "bot.log"
    if log_path.exists():
        lines = log_path.read_text().strip().split("\n")[-120:]
        tag = {"ERROR": "🔴", "WARN": "🟡", "APPROVED": "✅", "REJECTED": "❌", "SIGNAL": "📡",
               "CLOSED": "💰", "EMERGENCY": "🚨", "Trailing": "📌", "Near-miss": "🔭", "Macro alert": "🟣"}
        out = []
        for line in lines:
            pre = next((v for k, v in tag.items() if k in line), "  ")
            out.append(f"{pre} {line}")
        st.code("\n".join(out), language="text")
    else:
        st.markdown('<div class="card cinfo">No log yet.</div>', unsafe_allow_html=True)

# Tab 7: Watchlist
with t7:
    if wl:
        core    = wl.get("core", [])
        active  = wl.get("active", [])
        dynamic = [t for t in active if t not in core]
        a, b, c = st.columns(3)
        a.metric("Total", len(active))
        b.metric("Core", len(core))
        c.metric("Dynamic", len(dynamic))
        st.caption(f"Updated: {wl.get('updated_at','')[:16].replace('T',' ')}")
        st.subheader("Core (always scanned)")
        st.markdown(f'<div class="card cinfo">{", ".join(core)}</div>', unsafe_allow_html=True)
        if dynamic:
            st.subheader("Dynamic (today's movers)")
            st.markdown(f'<div class="card cwin">{", ".join(dynamic)}</div>', unsafe_allow_html=True)
    else:
        st.markdown('<div class="card cinfo">Watchlist not loaded yet.</div>', unsafe_allow_html=True)

# Tab 8: Research
with t8:
    if digest:
        fg = digest.get("fear_greed", {}) or {}
        score = fg.get("score", 50)
        fg_col = NEG if score < 25 else WARN if score < 45 else POS if score > 55 else INFO
        st.markdown(tile("Fear &amp; Greed", f"{score:.0f}/100",
                         f'{fg.get("rating","neutral")} · {fg.get("change",0):+.1f} vs prior', fg_col),
                    unsafe_allow_html=True)
        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        trending = digest.get("trending", []) or []
        if trending:
            st.markdown(f'<div class="card cinfo"><b>Retail trending:</b> {", ".join(trending[:14])}</div>',
                        unsafe_allow_html=True)
        insights = digest.get("insights", []) or []
        if insights:
            st.subheader("Insights")
            for i in insights[:8]:
                conf = i.get("confidence", "")
                color = "cwin" if conf == "high" else "calert" if conf == "medium" else "cnear"
                impact = i.get("impact", "")
                tick = ", ".join(i.get("tickers", [])[:6])
                st.markdown(
                    f'<div class="card {color}">{i.get("finding","")}'
                    + (f'<br><span style="opacity:.7"><i>{i.get("actionable","")}</i></span>' if i.get("actionable") else "")
                    + f'<br><span style="opacity:.5;font-size:11.5px">{impact} · {conf}'
                    + (f' · {tick}' if tick else "") + '</span></div>',
                    unsafe_allow_html=True)
        speeches = digest.get("fed_speeches", []) or []
        if speeches:
            st.subheader("Recent Fed Speeches")
            for s in speeches[:5]:
                st.markdown(f'- [{s.get("speaker","")}: {s.get("title","")}]({s.get("url","#")})')
        st.caption(f"Digest from {digest.get('updated_at','')[:10]} · {digest.get('source','')}")
    else:
        st.markdown('<div class="card cinfo">No research digest yet. Trigger mode=research to generate.</div>',
                    unsafe_allow_html=True)
