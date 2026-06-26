import streamlit as st
import json
from pathlib import Path
from datetime import datetime, date
import pandas as pd

st.set_page_config(page_title="Boticus", page_icon="bot", layout="wide")
DATA_DIR = Path("bot_state")
def load(f):
    p = DATA_DIR / f
    return json.loads(p.read_text()) if p.exists() else None

trades   = load("trades.json")   or []
feedback = load("feedback.json") or []
bt       = load("backtest_latest.json")
adj      = load("auto_adjust_latest.json")
wl_data  = load("watchlist.json")

open_t    = [t for t in trades if t.get("status") == "open"]
today_str = date.today().isoformat()
wins      = [t for t in feedback if t.get("result") == "win"]
losses    = [t for t in feedback if t.get("result") == "loss"]
total_pnl = sum(t.get("pnl_dollar", 0) for t in feedback)
wr        = len(wins)/len(feedback)*100 if feedback else 0
avg_win   = sum(t.get("pnl_pct",0) for t in wins)/len(wins) if wins else 0
avg_loss  = sum(t.get("pnl_pct",0) for t in losses)/len(losses) if losses else 0

st.title("Boticus")
st.caption(f"Updated {datetime.now().strftime('%b %d %H:%M ET')}")
r1a,r1b = st.columns(2); r2a,r2b = st.columns(2); r3a,r3b = st.columns(2)
r1a.metric("Open Positions", len(open_t))
r1b.metric("Win Rate", f"{wr:.1f}%")
r2a.metric("Total P&L", f"${total_pnl:+,.0f}")
r2b.metric("Avg Win", f"{avg_win:+.1f}%")
r3a.metric("Trades", len(feedback))
r3b.metric("Avg Loss", f"{avg_loss:+.1f}%")
st.divider()
tab1,tab2,tab3,tab4,tab5,tab6 = st.tabs(["Positions","P&L","Backtest","Adjust","Log","Watchlist"])

with tab1:
    if open_t:
        for t in open_t:
            unp = t.get("unrealized_pct", 0)
            st.write(f"{t.get('symbol')} {t.get('direction').upper()} {unp:+.1f}% entry=${t.get('entry_price',0):.2f}")
    else:
        st.info("No open positions.")
    st.subheader("Today Closed")
    for t in [x for x in feedback if x.get("date","") == today_str]:
        st.write(f"{t['symbol']} {t.get('direction','').upper()} {t.get('pnl_pct',0):+.1f}% {t.get('close_reason','')}")

with tab2:
    if feedback:
        df = pd.DataFrame(feedback)
        df["date"] = pd.to_datetime(df["date"])
        df = df.sort_values("date")
        df["cum_pnl"] = df["pnl_dollar"].cumsum()
        st.line_chart(df.set_index("date")["cum_pnl"])
    else:
        st.info("No trade history yet.")

with tab3:
    if bt:
        st.metric("Win Rate", f"{bt.get('win_rate',0):.1f}%")
        st.metric("Expectancy", f"{bt.get('expectancy_pct',0):+.2f}%")
        st.metric("Long WR", f"{bt.get('long_win_rate',0):.1f}%")
        st.metric("Short WR", f"{bt.get('short_win_rate',0):.1f}%")
    else:
        st.info("No backtest data yet.")

with tab4:
    if adj:
        st.write(f"Confidence: {adj.get('confidence','').upper()}")
        st.write(adj.get("summary",""))
        for a in adj.get("adjustments",[]):
            st.write(f"{a['param']}: {a['current']} -> {a['suggested']} — {a['reason']}")
    else:
        st.info("No auto-adjust data yet.")

with tab5:
    log_path = DATA_DIR / "bot.log"
    if log_path.exists():
        st.code("\n".join(log_path.read_text().strip().split("\n")[-80:]))
    else:
        st.info("No log yet.")

with tab6:
    if wl_data:
        core = wl_data.get("core",[])
        active = wl_data.get("active",[])
        dynamic = [t for t in active if t not in core]
        st.write(f"Total: {len(active)} ({len(core)} core + {len(dynamic)} dynamic)")
        st.write("Core: " + ", ".join(core))
        if dynamic: st.write("Dynamic: " + ", ".join(dynamic))
    else:
        st.info("No watchlist data yet.")
