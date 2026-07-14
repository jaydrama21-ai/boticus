#!/usr/bin/env python3
"""
test_overrides.py — offline tests for the backtest→params overlay loop.

No network: _evaluate_overlay (which runs a backtest) and the Telegram reply
POST are monkeypatched. Verifies clamping/safety, overlay loading, validation
staging (pass + fail), and the /apply → /revert file transitions.

Run:  python test_overrides.py   (exit 0 = pass)
"""
import json
import bot

PASS = "\033[92mPASS\033[0m"; FAIL = "\033[91mFAIL\033[0m"
_fail = 0


def check(label, cond, detail=""):
    global _fail
    if not cond: _fail += 1
    print(f"  [{PASS if cond else FAIL}] {label}" + (f"  — {detail}" if detail else ""))


def _clean_files():
    for f in (bot.RISK_OVERRIDE_FILE, bot.RISK_PENDING_FILE, bot.RISK_PREV_FILE):
        try: f.unlink()
        except FileNotFoundError: pass


def run():
    # snapshot RISK defaults so tests can't leak into each other
    defaults = dict(bot.RISK)
    # neutralise network: telegram reply + github commit
    bot.requests.post = lambda *a, **k: None
    bot.commit_state_to_github = lambda *a, **k: None
    bot.TELEGRAM_CHAT_ID = "1"
    CHAT = "1"

    print("Case 1: _clamp_overlay safety (drop risk params, clamp, invert-guard)")
    clean, notes = bot._clamp_overlay({
        "max_risk_per_trade_pct": 0.9,   # risk control — must be dropped
        "max_daily_loss_pct": 0.5,       # risk control — must be dropped
        "rsi_min": 200,                  # clamp to 68
        "take_profit_atr_mult": 3.6,     # in range
    })
    check("risk-control params dropped", "max_risk_per_trade_pct" not in clean
          and "max_daily_loss_pct" not in clean, str(clean))
    check("rsi_min clamped to bound 68", clean.get("rsi_min") == 68, str(clean))
    check("take_profit passed through", clean.get("take_profit_atr_mult") == 3.6, str(clean))
    inv, _ = bot._clamp_overlay({"rsi_min": 68, "rsi_max": 68})  # equal after clamp -> inverted
    check("inverted RSI band rejected", "rsi_min" not in inv and "rsi_max" not in inv, str(inv))
    print()

    print("Case 2: apply_risk_overrides loads file and re-clamps onto RISK")
    _clean_files()
    bot.RISK.update(defaults)
    bot.RISK_OVERRIDE_FILE.write_text(json.dumps({"params": {
        "rsi_min": 61, "take_profit_atr_mult": 99,  # 99 clamps to 5.0
        "max_risk_per_trade_pct": 0.9,              # ignored
    }}))
    applied = bot.apply_risk_overrides()
    check("rsi_min applied to RISK", bot.RISK["rsi_min"] == 61, str(bot.RISK["rsi_min"]))
    check("take_profit clamped to 5.0 on load", bot.RISK["take_profit_atr_mult"] == 5.0,
          str(bot.RISK["take_profit_atr_mult"]))
    check("risk control untouched by overlay",
          bot.RISK["max_risk_per_trade_pct"] == defaults["max_risk_per_trade_pct"],
          str(bot.RISK["max_risk_per_trade_pct"]))
    bot.RISK.update(defaults); _clean_files()
    print()

    print("Case 3: stage_pending_overlay — PASS (candidate beats base by margin)")
    adjustments = [
        {"param": "take_profit_atr_mult", "current": 3.5, "suggested": 4.0},
        {"param": "max_daily_loss_pct",   "current": 0.02, "suggested": 0.01},  # dropped
    ]
    seq = iter([0.30, 0.60])  # base, cand
    bot._evaluate_overlay = lambda overlay, days, syms: next(seq)
    prop = bot.stage_pending_overlay(adjustments)
    check("proposal validated as passed", prop["validation"]["passed"] is True, str(prop["validation"]))
    check("risk param excluded from proposal", "max_daily_loss_pct" not in prop["params"], str(prop["params"]))
    check("pending file written with params", bool(bot._overlay_params(bot.RISK_PENDING_FILE)),
          str(bot._overlay_params(bot.RISK_PENDING_FILE)))
    print()

    print("Case 4: stage_pending_overlay — FAIL (candidate does not beat base)")
    seq2 = iter([0.60, 0.61])  # cand 0.61 < base+margin(0.65)
    bot._evaluate_overlay = lambda overlay, days, syms: next(seq2)
    prop2 = bot.stage_pending_overlay([{"param": "rsi_min", "current": 58, "suggested": 60}])
    check("proposal marked failed", prop2["validation"]["passed"] is False, str(prop2["validation"]))
    check("pending cleared on fail", not bot._overlay_params(bot.RISK_PENDING_FILE),
          str(bot._overlay_params(bot.RISK_PENDING_FILE)))
    print()

    print("Case 5: /apply then /revert file transitions")
    _clean_files(); bot.RISK.update(defaults)
    # stage a fresh passing proposal
    seq3 = iter([0.30, 0.90])
    bot._evaluate_overlay = lambda overlay, days, syms: next(seq3)
    bot.stage_pending_overlay([{"param": "rsi_min", "current": 58, "suggested": 62}])
    check("pending staged before apply", bot._overlay_params(bot.RISK_PENDING_FILE).get("rsi_min") == 62)
    bot.handle_tg_command("/apply", CHAT)
    check("active override has rsi_min 62", bot._overlay_params(bot.RISK_OVERRIDE_FILE).get("rsi_min") == 62,
          str(bot._overlay_params(bot.RISK_OVERRIDE_FILE)))
    check("pending cleared after apply", not bot._overlay_params(bot.RISK_PENDING_FILE))
    check("RISK live-patched by apply", bot.RISK["rsi_min"] == 62, str(bot.RISK["rsi_min"]))
    bot.handle_tg_command("/revert", CHAT)
    check("override cleared after revert", not bot._overlay_params(bot.RISK_OVERRIDE_FILE),
          str(bot._overlay_params(bot.RISK_OVERRIDE_FILE)))
    _clean_files(); bot.RISK.update(defaults)
    print()

    print("=" * 60)
    if _fail == 0:
        print(f"{PASS}: all override tests passed"); return 0
    print(f"{FAIL}: {_fail} assertion(s) failed"); return 1


if __name__ == "__main__":
    import sys
    sys.exit(run())
