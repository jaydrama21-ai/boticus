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

print("\n" + "=" * 60)
if _fail == 0:
    print(f"{PASS}: all options tests passed")
else:
    print(f"{FAIL}: {_fail} check(s) failed")
    raise SystemExit(1)
