# Operations Guide

## How the bot behaves day to day

The bot runs fully on its own. Every 5 minutes it checks the market. If it sees a good setup during the
LCR window (5-8 PM Eastern), it places a trade without asking you anything. Disabled pairs are skipped
automatically. EUR/JPY is paper-only automatically. If you lose too much in a day, the month, or overall,
the bot stops trading on its own.

Every night after the session closes, it takes a snapshot of all pair health. If any pair is disabled,
it sends a Telegram message — every day that pair stays disabled, not just the first time.

## When you actually need to step in

- A pair hits the auto-disable rules (15 straight losses, or one pair accounting for 80%+ of all losses
  with zero wins). The bot already stopped trading it and Telegram already told you. The disable status is
  recomputed from cumulative trade data on every scan with no manual latch, so it clears on its own once
  the underlying metrics recover — you do not need to do anything unless you want to remove the pair
  entirely.

- You want to re-enable EUR/JPY after 20+ paper trades with PF > 1.0 — that is a one-line code change
  in `signal_tasks.py` (the hardcoded paper-only block around line 1060), not automatic.

- You want to change a strategy threshold, risk level, or pair list — edit `.env` and restart the
  container.

- The pair-status DB query fails during an LCR scan. After the fix applied 2026-03-23, the bot skips the
  entire LCR session rather than trading with no disable rules in effect. You will see
  `lcr_pair_status_eval_failed` at WARNING level in the logs. That is an ops issue to investigate, not
  something the bot resolves on its own.

- The drawdown peak fails to load from the database on a worker restart. The bot logs a warning and
  continues, but the drawdown counter resets from the current equity level, not the historical peak. If
  you see `drawdown_bootstrap_failed` in logs after a crash, verify the circuit breaker is still
  calibrated correctly before leaving the session unattended.

- The server crashes or Docker goes down — the bot cannot trade if it is not running.

## Enforcement reference

| Rule | Enforced in execution | Notes |
|---|---|---|
| Pair disabled — skip | Yes | `signal_tasks.py:832` hard `continue` |
| Pair watchlist — continue | Intentional | Logs at INFO only; trading proceeds |
| EUR/JPY paper-only | Yes | Hardcoded block `signal_tasks.py:1060` |
| EUR/JPY 60-day PF circuit | Yes | `signal_tasks.py:836` — needs 10+ trades |
| USD_CAD 0.5x risk | Yes | Hardcoded scale `signal_tasks.py:1072` |
| Drawdown halt (15%) | Yes | `drawdown_monitor.check()` |
| Drawdown reduce (8%) | Yes | 0.5x position size via `scale_factor` |
| Monthly halt (6% MTD) | Yes | Same `drawdown_monitor.check()` path |
| Daily loss limit (3%) | Yes | `daily_limiter.is_halted()` |
| Max 3 concurrent LCR positions | Yes | `signal_tasks.py:1047` |
| Correlation block | Yes | `signal_tasks.py:1040` |
| LCR overall status endpoint | UI/monitoring only | Read-only; does not gate execution |
| 50-trade review milestone | Not in code | Planning note only; bot runs straight through |
| Daily snapshot alert | Informational | Fires every day a pair stays disabled |
| fit_weights auto-apply at 200 trades | Automatic, no review | Patches `engine.py` without human step |
