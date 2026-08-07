#!/usr/bin/env python3
"""
test_options.py — offline tests for the options-only execution logic.

No network. Exercises the PURE helpers (OCC parsing, expiry/strike selection,
contract sizing, synthetic exit decisions). The API-touching functions
(select_option_contract, execute_option_signal, monitor_option_positions) are
integration-tested live on the deployed bot, not here.

Run:  python test_options.py   (exit 0 = pass)
"""
from datetime import date, datetime
import bot

PASS = "\033[92mPASS\033[0m"; FAIL = "\033[91mFAIL\033[0m"
_fail = 0


def check(label, cond, detail=""):
    global _fail
    if not cond:
        _fail += 1
    print(f"  [{PASS if cond else FAIL}] {label}" + (f"  — {detail}" if detail else ""))


print("\nCase 1: parse_occ_symbol")
p = bot.parse_occ_symbol("SPY260725C00550000")
check("underlying SPY", p and p["underlying"] == "SPY", str(p))
check("expiry 2026-07-25", p and p["expiry"] == date(2026, 7, 25), str(p and p["expiry"]))
check("type call", p and p["type"] == "call")
check("strike 550.0", p and p["strike"] == 550.0, str(p and p["strike"]))
p2 = bot.parse_occ_symbol("TSLA260117P00250500")
check("put strike 250.5", p2 and p2["type"] == "put" and p2["strike"] == 250.5, str(p2))
check("garbage -> None", bot.parse_occ_symbol("not-an-occ") is None)
check("empty -> None", bot.parse_occ_symbol("") is None)

print("\nCase 2: _pick_expiry (window 7-21 DTE)")
today = date(2026, 7, 20)
exps = [date(2026, 7, 22), date(2026, 7, 25), date(2026, 8, 1),
        date(2026, 8, 21), date(2026, 9, 18)]
picked = bot._pick_expiry(exps, today, 7, 21)
check("nearest in-window (Aug 1, 12 DTE)", picked == date(2026, 8, 1), str(picked))
# nothing in window -> closest future to min_dte
near = bot._pick_expiry([date(2026, 7, 21), date(2026, 9, 18)], today, 7, 14)
check("no in-window -> closest to min_dte", near == date(2026, 7, 21), str(near))
check("all past -> None", bot._pick_expiry([date(2026, 7, 1)], today, 7, 21) is None)

print("\nCase 3: _pick_strike")
strikes = [540, 545, 550, 555, 560]
check("call ITM = highest <= spot (551 -> 550)",
      bot._pick_strike(strikes, 551, "call", "itm") == 550)
check("put ITM = lowest >= spot (551 -> 555)",
      bot._pick_strike(strikes, 551, "put", "itm") == 555)
check("atm = nearest (552 -> 550)",
      bot._pick_strike(strikes, 552, "call", "atm") == 550)
check("call spot below all -> min strike",
      bot._pick_strike(strikes, 500, "call", "itm") == 540)
check("empty -> None", bot._pick_strike([], 550, "call", "itm") is None)

print("\nCase 4: _size_contracts (cap / (ask*100))")
check("$680 cap, $3.20 ask -> 2 contracts",
      bot._size_contracts(680, 3.20) == 2, str(bot._size_contracts(680, 3.20)))
check("$680 cap, $9.00 ask -> 0 (unaffordable)",
      bot._size_contracts(680, 9.00) == 0)
check("zero ask -> 0", bot._size_contracts(680, 0) == 0)

print("\nCase 5: option_exit_decision (synthetic underlying exits)")
# long call: stop 95 < entry 100 < target 110
check("long: at target", bot.option_exit_decision("long", 110, 95, 110) == "target")
check("long: above target", bot.option_exit_decision("long", 112, 95, 110) == "target")
check("long: at stop", bot.option_exit_decision("long", 95, 95, 110) == "stop")
check("long: mid -> hold", bot.option_exit_decision("long", 102, 95, 110) is None)
# short put: target 90 < entry 100 < stop 105
check("short: at target (down)", bot.option_exit_decision("short", 90, 105, 90) == "target")
check("short: at stop (up)", bot.option_exit_decision("short", 105, 105, 90) == "stop")
check("short: mid -> hold", bot.option_exit_decision("short", 98, 105, 90) is None)
check("no price -> hold", bot.option_exit_decision("long", 0, 95, 110) is None)

print("\nCase 6: market_hours_between (option hold clock)")
# The clock must count ONLY 09:30-16:00 ET on weekdays. Counting wall-clock time
# was the original defect: it made a 6h stop fire at the next morning's open, so
# every exit landed on the overnight gap instead of on the signal.
_ET = bot.ET
def _t(s):
    return datetime.fromisoformat(s).astimezone(_ET)

def _near(a, b):
    return abs(a - b) < 0.02

# 2026-07-24 is a Friday, 2026-07-27 the following Monday.
check("full session 09:30-16:00 = 6.5h",
      _near(bot.market_hours_between(_t("2026-08-04T09:30:00-04:00"),
                                     _t("2026-08-04T16:00:00-04:00")), 6.5))
check("overnight gap is not counted",
      _near(bot.market_hours_between(_t("2026-07-30T13:08:00-04:00"),
                                     _t("2026-07-31T09:50:00-04:00")), 3.2),
      "20.7h wall clock -> 3.2h market")
check("weekend is not counted",
      _near(bot.market_hours_between(_t("2026-07-24T11:01:00-04:00"),
                                     _t("2026-07-27T09:50:00-04:00")), 5.32),
      "71.3h wall clock -> 5.3h market")
check("after-hours only -> 0h",
      bot.market_hours_between(_t("2026-08-04T16:30:00-04:00"),
                               _t("2026-08-04T20:00:00-04:00")) == 0.0)
check("Fri open -> Mon close = 2 sessions",
      _near(bot.market_hours_between(_t("2026-07-24T09:30:00-04:00"),
                                     _t("2026-07-27T16:00:00-04:00")), 13.0))
check("end before start -> 0h",
      bot.market_hours_between(_t("2026-08-04T16:00:00-04:00"),
                               _t("2026-08-04T09:30:00-04:00")) == 0.0)
check("hold clock is options-specific, not the 6h equity intraday one",
      bot.OPTION_MAX_HOLD_HOURS > bot.RISK["max_hold_hours"],
      f"option={bot.OPTION_MAX_HOLD_HOURS}h equity={bot.RISK['max_hold_hours']}h")
check("options exempt from EOD flatten by default", bot.OPTION_EOD_CLOSE is False)

print("\nCase 8: option close bookkeeping (fakes, no network)")
# Booking a close on order ACCEPTANCE orphaned the contract whenever the order
# didn't fill (any run outside 09:30-16:00), and re-sending the sell each cycle
# would oversell into a naked short. Both are only reachable now that options are
# held across sessions.


class _Resp:
    def __init__(self, code, payload):
        self.status_code, self._p = code, payload
        self.ok = 200 <= code < 300

    def json(self):
        return self._p

    @property
    def text(self):
        return str(self._p)


_posted = []


class _FakeRequests:
    def post(self, url, **kw):
        _posted.append(kw.get("json"))
        return _Resp(200, {"id": "order-1"})

    def get(self, url, **kw):
        return _Resp(404, {})


bot.requests          = _FakeRequests()
bot._tg               = lambda *a, **k: None
bot.log_trade_outcome = lambda *a, **k: None
bot.get_option_quote  = lambda occ: (0.0, 0.0, 0.0)

_trade = {"symbol": "XLF", "option_symbol": "XLF260812C00058000", "is_option": True,
          "contracts": 2, "entry_premium": 1.00, "status": "open",
          "option_type": "call", "expiry": "2026-08-12"}

bot.get_market_session = lambda: "closed"
_r = bot.close_option_position(_trade, "TIME_EXIT")
check("market closed -> no order sent, row stays open",
      _r is False and not _posted and _trade["status"] == "open")

bot.get_market_session = lambda: "open"
bot._order_fill_state  = lambda oid, tries=3, delay=1.5: (0.0, "new")
_r = bot.close_option_position(_trade, "TIME_EXIT")
check("submitted but unfilled -> not booked closed",
      _r is False and _trade["status"] == "open")
check("pending order id is recorded on the row",
      _trade.get("close_order_id") == "order-1")
_n = len(_posted)
_r = bot.close_option_position(_trade, "TIME_EXIT")
check("pending close is polled, never resubmitted (would oversell)",
      len(_posted) == _n and _r is False)

bot._order_fill_state = lambda oid, tries=3, delay=1.5: (1.50, "filled")
_r = bot.close_option_position(_trade, "TIME_EXIT")
check("fill books the trade", _r is True and _trade["status"] == "closed")
check("P&L comes from the fill, not a quote",
      _trade["pnl_pct"] == 50.0 and _trade["pnl"] == 100.0,
      f"{_trade['pnl_pct']}% ${_trade['pnl']}")
check("pending markers cleared", "close_order_id" not in _trade)

print("\nCase 9: reconcile_option_ledger (contract left the account)")
_old = (datetime.now(bot.ET) - bot.timedelta(days=1)).isoformat()
_held = {"symbol": "XLF", "option_symbol": "XLF260812C00058000", "is_option": True,
         "contracts": 2, "entry_premium": 1.00, "status": "open",
         "opened_at": _old, "expiry": "2026-08-12"}
check("still held -> no change",
      bot.reconcile_option_ledger(_held, {"XLF260812C00058000"}) is False
      and _held["status"] == "open")

_fresh = dict(_held, opened_at=datetime.now(bot.ET).isoformat())
check("just-opened row is inside the grace period (entry may be unfilled)",
      bot.reconcile_option_ledger(_fresh, set()) is False
      and _fresh["status"] == "open")

bot._order_fill_state = lambda oid, tries=3, delay=1.5: (2.00, "filled")
_pending = dict(_held, close_order_id="order-9", close_pending_reason="STOP")
check("a close that filled after the fact is booked at its fill",
      bot.reconcile_option_ledger(_pending, set()) is True
      and _pending["status"] == "closed" and _pending["pnl_pct"] == 100.0)

_gone = dict(_held)
check("gone, but no fill and no quote -> NOT booked at a made-up price",
      bot.reconcile_option_ledger(_gone, set()) is True
      and _gone["status"] == "open" and _gone.get("reconcile_alerted") is True)
check("mismatch alert fires once, not every cycle",
      bot.reconcile_option_ledger(_gone, set()) is False)

bot.get_option_quote = lambda occ: (1.25, 1.35, 1.30)
_gone2 = dict(_held)
check("gone with a live quote -> booked at the quote",
      bot.reconcile_option_ledger(_gone2, set()) is True
      and _gone2["status"] == "closed" and _gone2["pnl_pct"] == 25.0,
      _gone2.get("close_reason", ""))

bot.get_option_quote = lambda occ: (0.0, 0.0, 0.0)
_exp = dict(_held, expiry="2026-01-16")
check("past expiry with no quote -> booked worthless",
      bot.reconcile_option_ledger(_exp, set()) is True
      and _exp["status"] == "closed" and _exp["pnl_pct"] == -100.0)

print("\nCase 10: a failed broker fetch must not look like a flat account")
# sync_positions_from_alpaca treats "symbol absent from the broker" as proof the
# position closed. get_open_positions() returns [] on a timeout exactly as it does
# when flat, so without the success flag one bad request books every open trade as
# closed — equities at a fabricated exit price, options via reconcile_option_ledger.
_saved = (bot.get_open_positions_checked, bot.load_trades, bot.save_trades)
_rows  = [{"symbol": "XLF", "option_symbol": "XLF260812C00058000", "is_option": True,
           "contracts": 2, "entry_premium": 1.00, "status": "open",
           "opened_at": _old, "expiry": "2026-08-12"},
          {"symbol": "IWM", "status": "open", "direction": "long",
           "entry_price": 300.0, "shares": 1, "opened_at": _old}]
_saves = []
bot.load_trades = lambda: _rows
bot.save_trades = lambda t: _saves.append(t)
try:
    bot.get_open_positions_checked = lambda: ([], False)
    out = bot.sync_positions_from_alpaca()
    check("fetch failed -> no row is touched",
          all(r["status"] == "open" for r in _rows) and not _saves)
    check("fetch failed -> returns no positions (monitor exits quietly)", out == [])

    bot.get_open_positions_checked = lambda: ([], True)
    bot.get_option_quote = lambda occ: (1.25, 1.35, 1.30)
    bot.sync_positions_from_alpaca()
    check("fetch succeeded and account really is flat -> rows do close",
          all(r["status"] == "closed" for r in _rows),
          str([r["status"] for r in _rows]))
finally:
    (bot.get_open_positions_checked, bot.load_trades, bot.save_trades) = _saved

print("\n" + "=" * 60)
if _fail == 0:
    print(f"{PASS}: all options tests passed")
else:
    print(f"{FAIL}: {_fail} check(s) failed")
    raise SystemExit(1)
