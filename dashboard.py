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
