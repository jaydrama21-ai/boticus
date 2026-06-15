import streamlit as st
import json
from pathlib import Path
from datetime import datetime, date
import pandas as pd

st.set_page_config(page_title="Boticus", page_icon="🤖", layout="wide", initial_sidebar_state="collapsed")

st.markdown("""<style>
.block-container{padding:0.75rem 0.75rem 2rem !important}
.stTabs [data-baseweb="tab"]{font-size:15px !important;padding:10px 12px !important}
[data-testid="metric-container"]{background:var(--secondary-background-color);border-radius:10px;padding:12px 16px}
[data-testid="stMetricValue"]{font-size:22px !important}
.pcard{padding:10px 14px;border-radius:6px;margin:6px 0;font-size:14px;border-left:4px solid}
.pwin{background:#d4edda;border-color:#28a745}
.ploss{background:#f8d7da;border-color:#dc3545}
.palert{background:#fff3cd;border-color:#ffc107}
@media(max-width:768px){.stTabs [data-baseweb="tab"]{font-size:13px !important;padding:8px 6px !important}}
</style>""", unsafe_allow_html=True)

st.markdown("<script>setTimeout(()=>window.location.reload(),300000)</script>", unsafe_allow_html=True)

DATA_DIR = Path("bot_state")
def load(f):
    p = DATA_DIR / f
    return json.loads(p.read_text()) if p.exists() else None

def card(sym, direction, pnl_pct, pnl_dollar, reason):
    cls = "pwin" if pnl_pct > 0 else "ploss"
    ico = "🟢" if pnl_pct > 0 else "🔴"
    return f'<div class="pcard {cls}">{ico} <b>{sym}</b> {direction.upper()} | {pnl_pct:+.1f}% | ${pnl_dollar:+.0f} | {reason}</div>'

trades   = load("trades.json")   or []
feedback = load("feedback.json") or []
bt       = load("backtest_latest.json")
adj      = load("auto_adjust_latest.json")

open_t    = [t for t in trades   if t.get("status") == "open"]
today_str = date.today().isoformat()
wins      = [t for t in feedback if t.get("result") == "win"]
losses    = [t for t in feedback if t.get("result") == "loss"]
total_pnl = sum(t.get("pnl_dollar", 0) for t in feedback)
today_pnl = sum(t.get("pnl_dollar", 0) for t in feedback if t.get("date","") == today_str)
wr        = len(wins)/len(feedback)*100 if feedback else 0
avg_win   = sum(t.get("pnl_pct",0) for t in wins)  /len(wins)   if wins   else 0
avg_loss  = sum(t.get("pnl_pct",0) for t in losses)/len(losses) if losses else 0

st.title("🤖 Boticus")
st.caption(f"Updated {datetime.now().strftime('%b %d %H:%M ET')} · Auto-refreshes every 5 min")

r1a,r1b = st.columns(2)
r2a,r2b = st.columns(2)
r3a,r3b = st.columns(2)
r1a.metric("Open Positions", len(open_t))
r1b.metric("Win Rate",       f"{wr:.1f}%", delta=f"{wr-50:.1f}% vs 50%")
r2a.metric("Total P&L",      f"${total_pnl:+,.0f}")
r2b.metric("Today P&L",      f"${today_pnl:+,.0f}")
r3a.metric("Avg Win",        f"{avg_win:+.1f}%")
r3b.metric("Avg Loss",       f"{avg_loss:+.1f}%")
st.divider()

tab1,tab2,tab3,tab4,tab5,tab6 = st.tabs(["📊 Positions","📈 P&L","🔬 Quality","📉 Backtest","🔧 Adjust","📋 Log"])

with tab1:
    if open_t:
        st.subheader(f"Open ({len(open_t)})")
        for t in open_t:
            curr  = t.get("current_price", t.get("entry_price",0))
            entry = t.get("entry_price",0)
            unp   = t.get("unrealized_pct",0)
            trail = " 📌" if t.get("trailing_stop") else ""
            sym   = t.get("symbol","")
            icon  = "🟢" if unp > 0 else "🔴" if unp < 0 else "⚪"
            with st.expander(f"{icon} **{sym}** {t.get('direction','').upper()}  {unp:+.1f}%{trail}"):
                ca,cb = st.columns(2)
                ca.metric("Entry",   f"${entry:.2f}")
                cb.metric("Current", f"${curr:.2f}")
                cc,cd = st.columns(2)
                cc.metric("Stop",    f"${t.get('stop_loss',0):.2f}")
                cd.metric("Target",  f"${t.get('take_profit',0):.2f}")
                ce,cf = st.columns(2)
                ce.metric("Shares",  t.get("shares",0))
                cf.metric("AI",      f"{t.get('ai_score','—')}")
                st.caption(f"Opened: {t.get('opened_at','')[:16].replace('T',' ')}")
    else:
        st.info("No open positions right now")
    st.divider()
    st.subheader("Today")
    today_fb = [t for t in feedback if t.get("date","") == today_str]
    if today_fb:
        st.markdown("".join([card(t["symbol"],t.get("direction",""),t.get("pnl_pct",0),t.get("pnl_dollar",0),t.get("close_reason","—")) for t in today_fb]), unsafe_allow_html=True)
    else:
        st.info("No completed trades today")

with tab2:
    if feedback:
        df = pd.DataFrame(feedback)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        df["cum_pnl"] = df["pnl_dollar"].cumsum()
        st.subheader("Cumulative P&L")
        st.line_chart(df.set_index("date")["cum_pnl"], use_container_width=True, height=220)
        st.subheader("Trade History")
        disp = df[["date","symbol","direction","pnl_pct","pnl_dollar","close_reason","regime"]].copy()
        disp = disp.sort_values("date", ascending=False)
        disp.columns = ["Date","Sym","Dir","P&L%","P&L$","Reason","Regime"]
        disp["P&L%"] = disp["P&L%"].map(lambda x: f"{x:+.1f}%")
        disp["P&L$"] = disp["P&L$"].map(lambda x: f"${x:+.0f}")
        disp["Date"] = disp["Date"].dt.strftime("%m/%d")
        st.dataframe(disp.head(50), use_container_width=True, hide_index=True)
        if "regime" in df.columns:
            st.subheader("By Regime")
            rg = df.groupby("regime").agg(Trades=("pnl_dollar","count"), TotalPnL=("pnl_dollar","sum")).reset_index()
            rg["Win Rate"] = df.groupby("regime").apply(lambda x: f"{sum(v>0 for v in x['pnl_dollar'])/len(x)*100:.0f}%").values
            rg["TotalPnL"] = rg["TotalPnL"].map(lambda x: f"${x:+.0f}")
            st.dataframe(rg, use_container_width=True, hide_index=True)
    else:
        st.info("No closed trades yet")

with tab3:
    if len(feedback) >= 5:
        df = pd.DataFrame(feedback)
        st.subheader("Win Rate by AI Score")
        if "ai_score" in df.columns:
            df["ai_b"] = pd.cut(df["ai_score"].fillna(0), bins=[0,60,70,80,100], labels=["55-60","60-70","70-80","80+"])
            ab = df.groupby("ai_b", observed=True).apply(lambda x: round(sum(x["result"]=="win")/len(x)*100,0) if len(x)>0 else 0).reset_index()
            ab.columns = ["AI Score","Win %"]
            st.dataframe(ab, use_container_width=True, hide_index=True)
        st.subheader("Win Rate by VIX")
        if "vix" in df.columns:
            df["vix_b"] = pd.cut(df["vix"].fillna(20), bins=[0,15,25,35,100], labels=["<15 low","15-25 normal","25-35 elevated",">35 fear"])
            vb = df.groupby("vix_b", observed=True).apply(lambda x: round(sum(x["result"]=="win")/len(x)*100,0) if len(x)>0 else 0).reset_index()
            vb.columns = ["VIX","Win %"]
            st.dataframe(vb, use_container_width=True, hide_index=True)
        st.subheader("Exit Breakdown")
        total_fb = len(feedback) or 1
        stops=sum(1 for t in feedback if "STOP" in t.get("close_reason",""))
        targets=sum(1 for t in feedback if "TARGET" in t.get("close_reason",""))
        eod=sum(1 for t in feedback if "EOD" in t.get("close_reason",""))
        emerg=sum(1 for t in feedback if "EMERGENCY" in t.get("close_reason",""))
        trail=sum(1 for t in feedback if "TRAIL" in t.get("close_reason",""))
        tme=sum(1 for t in feedback if "TIME" in t.get("close_reason",""))
        ex = pd.DataFrame({"Exit":["🎯 Target","🛑 Stop","🔔 EOD","🚨 Emergency","📌 Trail","⏰ Time"],"Count":[targets,stops,eod,emerg,trail,tme],"Pct":[f"{x/total_fb*100:.0f}%" for x in [targets,stops,eod,emerg,trail,tme]]})
        st.dataframe(ex, use_container_width=True, hide_index=True)
        st.subheader("Long vs Short")
        long_t=[t for t in feedback if t.get("direction")=="long"]
        short_t=[t for t in feedback if t.get("direction")=="short"]
        lwr=sum(1 for t in long_t if t["result"]=="win")/len(long_t)*100 if long_t else 0
        swr=sum(1 for t in short_t if t["result"]=="win")/len(short_t)*100 if short_t else 0
        dp = pd.DataFrame({"Direction":["📈 Long","📉 Short"],"Trades":[len(long_t),len(short_t)],"Win Rate":[f"{lwr:.0f}%",f"{swr:.0f}%"]})
        st.dataframe(dp, use_container_width=True, hide_index=True)
    else:
        st.info(f"Need 5+ closed trades. Have {len(feedback)} so far.")

with tab4:
    if bt:
        r1a,r1b=st.columns(2);r2a,r2b=st.columns(2);r3a,r3b=st.columns(2)
        r1a.metric("Win Rate",   f"{bt.get('win_rate',0):.1f}%")
        r1b.metric("Expectancy", f"{bt.get('expectancy_pct',0):+.2f}%")
        r2a.metric("Avg Win",    f"{bt.get('avg_win_pct',0):+.2f}%")
        r2b.metric("Avg Loss",   f"{bt.get('avg_loss_pct',0):+.2f}%")
        r3a.metric("Long WR",    f"{bt.get('long_win_rate',0):.1f}%", delta=f"{bt.get('long_signals',0)} signals")
        r3b.metric("Short WR",   f"{bt.get('short_win_rate',0):.1f}%", delta=f"{bt.get('short_signals',0)} signals")
        if bt.get("rsi_win_rates"):
            st.subheader("RSI Performance")
            st.dataframe(pd.DataFrame([{"RSI":k,"Win %":f"{v:.0f}%"} for k,v in bt["rsi_win_rates"].items()]), use_container_width=True, hide_index=True)
        if bt.get("regime_stats"):
            st.subheader("By Regime")
            rg=[{"Regime":r,"WR":f"{s['wins']/(s['wins']+s['losses'])*100:.0f}%","N":s['wins']+s['losses']} for r,s in bt["regime_stats"].items() if s['wins']+s['losses']>0]
            if rg: st.dataframe(pd.DataFrame(rg), use_container_width=True, hide_index=True)
    else:
        st.info("No backtest yet. Actions → Trading Bot → backtest mode")

with tab5:
    if adj:
        conf=adj.get("confidence","").upper()
        ico="🟢" if conf=="HIGH" else "🟡" if conf=="MEDIUM" else "🔴"
        st.markdown(f'<div class="pcard palert">{ico} <b>Confidence: {conf}</b><br>{adj.get("summary","")}</div>', unsafe_allow_html=True)
        st.write(f"**Priority:** {adj.get('priority_change','')}")
        if adj.get("adjustments"):
            for a in adj["adjustments"]:
                st.markdown(f'<div class="pcard palert">🔧 <b>{a["param"]}</b>: {a["current"]} → <b>{a["suggested"]}</b><br><small>{a["reason"]}</small></div>', unsafe_allow_html=True)
        st.warning("Apply: edit RISK dict in bot.py → push to GitHub.")
    else:
        st.info("No auto-adjust data. Actions → auto_adjust mode")

with tab6:
    log_path = DATA_DIR / "bot.log"
    if log_path.exists():
        lines = log_path.read_text().strip().split("\\n")[-60:]
        colored=[]
        for line in lines:
            if "ERROR"     in line: colored.append(f"🔴 {line}")
            elif "WARN"    in line: colored.append(f"🟡 {line}")
            elif "APPROVED"in line: colored.append(f"✅ {line}")
            elif "REJECTED"in line: colored.append(f"❌ {line}")
            elif "CLOSED"  in line: colored.append(f"💰 {line}")
            elif "EMERGENCY"in line:colored.append(f"🚨 {line}")
            elif "Trailing"in line: colored.append(f"📌 {line}")
            elif "EOD"     in line: colored.append(f"🔔 {line}")
            elif "Time exit"in line:colored.append(f"⏰ {line}")
            else:                   colored.append(f"   {line}")
        st.code("\\n".join(colored), language="text")
    else:
        st.info("No log file yet")
