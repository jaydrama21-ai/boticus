import streamlit as st
import json
from pathlib import Path
from datetime import date

st.set_page_config(page_title="Boticus", page_icon="🤖", layout="wide")
st.title("🤖 Boticus Dashboard")

DATA_DIR = Path("bot_state")

def load(f):
    p = DATA_DIR / f
    return json.loads(p.read_text()) if p.exists() else None

trades   = load("trades.json")   or []
feedback = load("feedback.json") or []

open_t = [t for t in trades if t.get("status") == "open"]
wins   = [t for t in feedback if t.get("result") == "win"]
pnl    = sum(t.get("pnl_dollar", 0) for t in feedback)
wr     = len(wins) / len(feedback) * 100 if feedback else 0

c1,c2,c3,c4 = st.columns(4)
c1.metric("Open Positions", len(open_t))
c2.metric("Total Trades",   len(feedback))
c3.metric("Win Rate",       f"{wr:.1f}%")
c4.metric("Total P&L",      f"${pnl:+,.2f}")

st.divider()
st.subheader("Open Positions")
for t in open_t:
    st.write(f"**{t['symbol']}** {t.get('direction','').upper()} | Entry: ${t.get('entry_price',0):.2f} | Stop: ${t.get('stop_loss',0):.2f} | Target: ${t.get('take_profit',0):.2f}")

st.divider()
st.subheader("Recent Trades")
for t in sorted(feedback, key=lambda x: x.get("date",""), reverse=True)[:20]:
    e = "🟢" if t.get("pnl_pct",0) > 0 else "🔴"
    st.write(f"{e} **{t['symbol']}** {t.get('pnl_pct',0):+.1f}% | {t.get('close_reason','—')} | {t.get('date','')}")
