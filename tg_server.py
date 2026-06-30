"""
tg_server.py — Boticus Telegram Command Server
Deploy to Render (free tier) for instant command responses.
Works alongside bot.py on GitHub Actions — server handles commands,
bot handles scanning and trading.

Setup:
1. Deploy this to Render as a Web Service
2. Set environment variables (same as bot.py secrets)
3. Visit https://your-render-url.onrender.com/set_webhook once to register
4. Done — commands respond in under 5 seconds
"""

import os, json, base64, requests
from datetime import datetime, date
from flask import Flask, request, jsonify

app = Flask(__name__)

# ── Environment ────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO      = os.environ.get("GITHUB_REPOSITORY", "")
ALPACA_KEY       = os.environ.get("ALPACA_API_KEY", "")
ALPACA_SECRET    = os.environ.get("ALPACA_SECRET_KEY", "")
ALPACA_BASE      = os.environ.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")

ALPACA_HEADERS = {
    "APCA-API-KEY-ID":     ALPACA_KEY,
    "APCA-API-SECRET-KEY": ALPACA_SECRET,
}
GITHUB_HEADERS = {
    "Authorization":        f"token {GITHUB_TOKEN}",
    "Accept":               "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


# ── GitHub state helpers ───────────────────────────────────────────────────────

def read_state(filename: str):
    """Read a JSON file from bot_state/ in the GitHub repo."""
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/bot_state/{filename}"
        r   = requests.get(url, headers=GITHUB_HEADERS, timeout=8)
        if r.ok:
            content = base64.b64decode(r.json()["content"]).decode()
            return json.loads(content)
    except Exception as e:
        print(f"State read error {filename}: {e}")
    return None


def write_state(filename: str, data) -> bool:
    """Write a JSON file to bot_state/ in the GitHub repo."""
    try:
        url     = f"https://api.github.com/repos/{GITHUB_REPO}/contents/bot_state/{filename}"
        content = base64.b64encode(json.dumps(data, indent=2).encode()).decode()
        sha     = None
        r = requests.get(url, headers=GITHUB_HEADERS, timeout=8)
        if r.ok:
            sha = r.json().get("sha")
        payload = {
            "message": f"tg-server: update {filename}",
            "content": content,
            "branch":  "main",
        }
        if sha:
            payload["sha"] = sha
        r2 = requests.put(url, headers=GITHUB_HEADERS, json=payload, timeout=15)
        return r2.status_code in (200, 201)
    except Exception as e:
        print(f"State write error {filename}: {e}")
        return False


def trigger_workflow(mode: str, days: int = 180) -> bool:
    """Trigger a GitHub Actions workflow_dispatch."""
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/bot.yml/dispatches"
        r   = requests.post(url, headers=GITHUB_HEADERS, json={
            "ref":    "main",
            "inputs": {"run_mode": mode, "backtest_days": str(days)},
        }, timeout=10)
        print(f"[trigger_workflow] mode={mode} status={r.status_code} body={r.text[:300]}")
        print(f"[trigger_workflow] repo={GITHUB_REPO!r} token_set={bool(GITHUB_TOKEN)} token_len={len(GITHUB_TOKEN) if GITHUB_TOKEN else 0}")
        return r.status_code in (200, 201, 204)
    except Exception as e:
        print(f"[trigger_workflow] EXCEPTION: {e}")
        return False


# ── Alpaca helpers ─────────────────────────────────────────────────────────────

def alpaca_account() -> dict:
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/account", headers=ALPACA_HEADERS, timeout=8)
        return r.json() if r.ok else {}
    except:
        return {}


def alpaca_positions() -> list:
    try:
        r = requests.get(f"{ALPACA_BASE}/v2/positions", headers=ALPACA_HEADERS, timeout=8)
        return r.json() if r.ok else []
    except:
        return []


# ── Telegram helpers ───────────────────────────────────────────────────────────

def send(chat_id: str, text: str):
    """Send a Telegram message — plain text, no formatting issues."""
    if not TELEGRAM_TOKEN:
        return
    MAX = 4000
    chunks = [text[i:i+MAX] for i in range(0, len(text), MAX)]
    for chunk in chunks:
        try:
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": chat_id, "text": chunk},
                timeout=10
            )
        except Exception as e:
            print(f"Send error: {e}")


# ── Command handler ────────────────────────────────────────────────────────────

def handle(text: str, chat_id: str):
    """Process a Telegram command and reply instantly."""
    if str(chat_id) != str(TELEGRAM_CHAT_ID):
        return  # Security gate — only respond to authorized chat

    parts = text.strip().split(None, 1)
    cmd   = parts[0].lstrip("/").lower()
    args  = parts[1].strip() if len(parts) > 1 else ""

    # ── /help ────────────────────────────────────────────────────────────────
    if cmd in ("help", "h", "?"):
        send(chat_id,
            "Boticus Commands\n\n"
            "Scanning\n"
            "/scan — trigger scan now\n"
            "/nearmiss — what almost fired last scan\n"
            "/debug — what is blocking signals\n\n"
            "Info\n"
            "/status — equity + open positions\n"
            "/positions — detailed open trades\n"
            "/regime — regime + VIX + breadth\n"
            "/pnl — today P&L\n\n"
            "Control\n"
            "/pause — halt new trades\n"
            "/resume — resume trading\n"
            "/eod — close all positions now\n\n"
            "Intelligence\n"
            "/backtest — run 180-day backtest\n"
            "/research — run research digest\n"
            "/feed <text> — inject text into AI brain\n"
        )

    # ── /status ──────────────────────────────────────────────────────────────
    elif cmd == "status":
        acc = alpaca_account()
        pos = alpaca_positions()
        lr  = read_state("last_run.json") or {}
        equity = float(acc.get("equity", 0))
        bp     = float(acc.get("buying_power", 0))
        pos_lines = "\n".join([
            f"  {p.get('symbol')} {p.get('side','').upper()} x{p.get('qty',0)} "
            f"P&L: ${float(p.get('unrealized_pl',0)):+.2f}"
            for p in pos[:8]
        ]) or "  None"
        ts = lr.get("timestamp","")[:16].replace("T"," ")
        send(chat_id,
            f"Boticus Status\n\n"
            f"Equity: ${equity:,.2f}\n"
            f"Buying power: ${bp:,.2f}\n"
            f"Open positions: {len(pos)}/6\n\n"
            f"Positions:\n{pos_lines}\n\n"
            f"Last bot run: {ts} ET\n"
            f"Regime: {lr.get('regime','unknown')} | VIX: {lr.get('vix',0):.1f} | "
            f"Risk: {lr.get('risk_score',0):+d}"
        )

    # ── /positions ───────────────────────────────────────────────────────────
    elif cmd == "positions":
        pos = alpaca_positions()
        if not pos:
            send(chat_id, "No open positions.")
            return
        lines = []
        for p in pos:
            sym       = p.get("symbol","")
            side      = p.get("side","").upper()
            qty       = p.get("qty",0)
            entry     = float(p.get("avg_entry_price",0))
            curr      = float(p.get("current_price",0))
            unreal    = float(p.get("unrealized_pl",0))
            unreal_pct = float(p.get("unrealized_plpc",0)) * 100
            icon = "+" if unreal > 0 else "-"
            lines.append(
                f"{icon} {sym} {side} x{qty}\n"
                f"   Entry: ${entry:.2f} -> ${curr:.2f} "
                f"({unreal_pct:+.1f}% / ${unreal:+.2f})"
            )
        send(chat_id, "Open Positions\n\n" + "\n".join(lines))

    # ── /nearmiss ────────────────────────────────────────────────────────────
    elif cmd == "nearmiss":
        nm = read_state("near_miss.json")
        if not nm or not nm.get("near_misses"):
            send(chat_id, "No near-miss data yet — run a scan first.")
            return
        nms     = nm["near_misses"]
        updated = nm.get("updated_at","")[:16].replace("T"," ")
        lines   = [f"Near-Misses as of {updated} ET\n"]
        for n in nms[:8]:
            gap = 58 - n["criteria"]
            b   = n["blockers"][0] if n.get("blockers") else "below threshold"
            lines.append(
                f"{n['symbol']} score:{n['criteria']:.0f}/58 gap:{gap:.0f} "
                f"RSI:{n.get('rsi',0):.0f} Vol:{n.get('vol',0):.1f}x | {b}"
            )
        send(chat_id, "\n".join(lines))

    # ── /regime ──────────────────────────────────────────────────────────────
    elif cmd == "regime":
        lr     = read_state("last_run.json") or {}
        digest = read_state("research_digest.json") or {}
        fg     = digest.get("fear_greed", {})
        risk   = lr.get("risk_score", 0)
        risk_str = {
            2:"RISK-ON", 1:"MILD-ON", 0:"NEUTRAL",
            -1:"MILD-OFF", -2:"RISK-OFF", -3:"EXTREME-OFF"
        }.get(risk, str(risk))
        send(chat_id,
            f"Market Regime\n\n"
            f"Regime: {lr.get('regime','unknown')}\n"
            f"VIX: {lr.get('vix',0):.1f}\n"
            f"Risk score: {risk:+d} ({risk_str})\n"
            f"F&G: {fg.get('score',50):.0f} ({fg.get('rating','neutral')})\n"
            f"Last updated: {lr.get('timestamp','')[:16].replace('T',' ')} ET"
        )

    # ── /debug ───────────────────────────────────────────────────────────────
    elif cmd == "debug":
        lr  = read_state("last_run.json") or {}
        nm  = read_state("near_miss.json") or {}
        nms = nm.get("near_misses", [])
        risk = lr.get("risk_score", 0)
        risk_str = {
            2:"RISK-ON", 1:"MILD-ON", 0:"NEUTRAL",
            -1:"MILD-OFF", -2:"RISK-OFF", -3:"EXTREME-OFF"
        }.get(risk, str(risk))
        nm_lines = ""
        if nms:
            nm_lines = "\n\nTop near-misses:\n" + "\n".join([
                f"  {n['symbol']} {n['criteria']:.0f}/58 — "
                f"{n['blockers'][0] if n.get('blockers') else 'criteria'}"
                for n in nms[:5]
            ])
        send(chat_id,
            f"Debug\n\n"
            f"Regime: {lr.get('regime','unknown')}\n"
            f"VIX: {lr.get('vix',0):.1f}\n"
            f"Risk score: {risk:+d} ({risk_str})\n"
            f"Open positions: {lr.get('open_positions',0)}/6\n"
            f"Last run: {lr.get('timestamp','')[:16].replace('T',' ')} ET"
            + nm_lines
        )

    # ── /pnl ─────────────────────────────────────────────────────────────────
    elif cmd == "pnl":
        feedback = read_state("feedback.json") or []
        today    = date.today().isoformat()
        today_t  = [t for t in feedback if t.get("date","") == today]
        total    = sum(t.get("pnl_dollar",0) for t in today_t)
        wins     = [t for t in today_t if t.get("result") == "win"]
        lines    = [
            "Today P&L\n",
            f"Trades: {len(today_t)} | Wins: {len(wins)} | Losses: {len(today_t)-len(wins)}",
            f"Total: ${total:+.2f}\n",
        ]
        for t in today_t:
            icon = "W" if t.get("result") == "win" else "L"
            lines.append(
                f"{icon} {t.get('symbol','')} "
                f"{t.get('pnl_pct',0):+.1f}% "
                f"${t.get('pnl_dollar',0):+.0f}"
            )
        if not today_t:
            lines.append("No closed trades today.")
        send(chat_id, "\n".join(lines))

    # ── /scan ────────────────────────────────────────────────────────────────
    elif cmd == "scan":
        triggered = trigger_workflow("scan")
        if triggered:
            send(chat_id,
                "Scan triggered on GitHub Actions.\n"
                "Results arrive in ~2 minutes via scan summary message."
            )
        else:
            send(chat_id, "Failed to trigger scan — check GitHub Actions.")

    # ── /backtest ────────────────────────────────────────────────────────────
    elif cmd == "backtest":
        triggered = trigger_workflow("backtest", 180)
        send(chat_id,
            "Backtest triggered (~10 min)." if triggered
            else "Trigger failed — check GitHub Actions."
        )

    # ── /research ────────────────────────────────────────────────────────────
    elif cmd == "research":
        triggered = trigger_workflow("research")
        send(chat_id,
            "Research digest triggered (~2 min)." if triggered
            else "Trigger failed."
        )

    # ── /pause ───────────────────────────────────────────────────────────────
    elif cmd == "pause":
        reason = args or "Manual pause via Telegram"
        ok = write_state("paused.json", {
            "paused":    True,
            "reason":    reason,
            "timestamp": datetime.utcnow().isoformat(),
        })
        send(chat_id,
            f"Trading PAUSED\nReason: {reason}\n"
            f"{'Written to repo — takes effect immediately.' if ok else 'Write failed — takes effect on next bot run.'}"
        )

    # ── /resume ──────────────────────────────────────────────────────────────
    elif cmd == "resume":
        ok = write_state("paused.json", {"paused": False, "reason": "", "timestamp": datetime.utcnow().isoformat()})
        send(chat_id,
            "Trading RESUMED.\n"
            f"{'Written to repo — takes effect immediately.' if ok else 'Write failed — takes effect on next bot run.'}"
        )

    # ── /eod ─────────────────────────────────────────────────────────────────
    elif cmd == "eod":
        triggered = trigger_workflow("scan")
        send(chat_id,
            "EOD close triggered — bot will close all positions on next run." if triggered
            else "Trigger failed."
        )

    # ── /feed ────────────────────────────────────────────────────────────────
    elif cmd == "feed":
        if not args:
            send(chat_id, "Usage: /feed <paste any text, article, analysis>")
            return
        existing = read_state("fed_insights.json") or []
        existing.append({
            "finding":    args[:500],
            "tickers":    [],
            "impact":     "neutral",
            "confidence": "medium",
            "actionable": "review before trading",
            "added_at":   datetime.utcnow().isoformat()[:10],
        })
        existing = existing[-50:]
        ok = write_state("fed_insights.json", existing)
        send(chat_id,
            "Insight saved to knowledge base.\n"
            "Opus will use this in next signal score.\n\n"
            f"Preview: {args[:150]}..."
            if ok else "Write failed — try again."
        )

    # ── Unknown ──────────────────────────────────────────────────────────────
    else:
        send(chat_id, "Unknown command. Send /help for the full list.")


# ── Flask routes ───────────────────────────────────────────────────────────────

@app.route("/webhook", methods=["POST"])
def webhook():
    data    = request.get_json(silent=True) or {}
    msg     = data.get("message", {})
    text    = msg.get("text", "")
    chat_id = str(msg.get("chat", {}).get("id", ""))
    print(f"[webhook] received text={text!r} chat_id={chat_id!r} expected={TELEGRAM_CHAT_ID!r}")
    if text and text.startswith("/") and chat_id:
        if str(chat_id) != str(TELEGRAM_CHAT_ID):
            print(f"[webhook] BLOCKED — chat_id mismatch: got {chat_id!r}, expected {TELEGRAM_CHAT_ID!r}")
        try:
            handle(text, chat_id)
        except Exception as e:
            print(f"[webhook] Command error: {e}")
            send(chat_id, f"Error: {e}")
    return jsonify({"ok": True})


@app.route("/", methods=["GET"])
def health():
    return "Boticus TG Server — OK"


@app.route("/cron_scan", methods=["GET", "POST"])
def cron_scan():
    """
    Reliable external trigger for bot.py scans.
    Call this from cron-job.org every 5 minutes during market hours.
    Only triggers during actual market hours (9:30 AM - 4:00 PM ET, Mon-Fri)
    to avoid wasting GitHub Actions minutes outside trading hours.
    """
    from zoneinfo import ZoneInfo
    now_et = datetime.now(ZoneInfo("America/New_York"))
    is_weekday = now_et.weekday() < 5
    mins = now_et.hour * 60 + now_et.minute
    is_market_hours = (9 * 60 + 25) <= mins <= (16 * 60 + 5)  # small buffer

    if not (is_weekday and is_market_hours):
        return jsonify({
            "triggered": False,
            "reason": "outside market hours",
            "time_et": now_et.strftime("%Y-%m-%d %H:%M:%S"),
        })

    ok = trigger_workflow("scan")
    return jsonify({
        "triggered": ok,
        "time_et": now_et.strftime("%Y-%m-%d %H:%M:%S"),
    })


@app.route("/set_webhook", methods=["GET"])
def set_webhook():
    """
    Visit this URL once after deploying to Render to register the webhook.
    Example: https://boticus-tg.onrender.com/set_webhook
    """
    host = request.host_url.rstrip("/")
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setWebhook",
        json={"url": f"{host}/webhook", "drop_pending_updates": True},
        timeout=10
    )
    return jsonify(r.json())


@app.route("/delete_webhook", methods=["GET"])
def delete_webhook():
    """Visit to remove webhook and go back to polling mode."""
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteWebhook",
        timeout=10
    )
    return jsonify(r.json())


# ── Keep-alive — runs at module level so gunicorn picks it up ─────────────────
import threading as _threading
import time as _time

def _keep_alive_worker():
    """Ping self every 8 minutes to prevent Render free tier spin-down."""
    _time.sleep(90)  # Wait well past Render's health check window first
    while True:
        try:
            url = os.environ.get("RENDER_EXTERNAL_URL", "")
            if url:
                requests.get(url.rstrip("/") + "/", timeout=10)
        except Exception:
            pass
        _time.sleep(480)  # 8 minutes

try:
    _ka_thread = _threading.Thread(target=_keep_alive_worker, daemon=True)
    _ka_thread.start()
except Exception:
    pass  # Never let keep-alive setup block server startup


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
