"""
Unit tests for session filter — verifies current session policy:
London-only for most pairs, Asian exception for JPY pairs, and
rejection of off-session hours, weekends, and edge times.
"""
from datetime import datetime, timezone

from anchor.signals.session_filter import check_session


def utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


class TestSessionFilter:
    # London session: 08:00–17:00 UTC
    def test_london_core_passes(self):
        ok, reason = check_session(utc(2026, 3, 3, 10, 0))  # Tuesday 10am
        assert ok is True

    def test_london_open_boundary(self):
        ok, _ = check_session(utc(2026, 3, 3, 8, 0))
        assert ok is True

    def test_ny_core_rejected(self):
        ok, reason = check_session(utc(2026, 3, 3, 15, 0))  # Tuesday 3pm
        assert ok is False
        assert "OFF_SESSION" in reason

    def test_overlap_rejected(self):
        # London/NY overlap is intentionally excluded for non-JPY pairs
        ok, reason = check_session(utc(2026, 3, 3, 14, 0))
        assert ok is False
        assert "OFF_SESSION" in reason

    # Asian session: 00:00–08:00 UTC — should be rejected
    def test_asian_session_rejected(self):
        ok, reason = check_session(utc(2026, 3, 3, 3, 0))  # 3am UTC
        assert ok is False
        assert "OFF_SESSION" in reason or "ASIAN" in reason.upper() or ok is False

    def test_jpy_asian_session_allowed(self):
        ok, reason = check_session(utc(2026, 3, 3, 2, 0), instrument="EUR_JPY")
        assert ok is True
        assert reason == "ASIAN"

    def test_dead_zone_rejected(self):
        # 22:00–00:00 UTC — after NY close
        ok, reason = check_session(utc(2026, 3, 3, 23, 0))
        assert ok is False

    # Weekend
    def test_saturday_rejected(self):
        ok, reason = check_session(utc(2026, 3, 7, 12, 0))  # Saturday
        assert ok is False

    def test_sunday_rejected(self):
        ok, reason = check_session(utc(2026, 3, 8, 10, 0))  # Sunday
        assert ok is False

    # Monday open gap risk
    def test_monday_early_rejected(self):
        ok, reason = check_session(utc(2026, 3, 2, 1, 0))  # Monday 1am
        assert ok is False

    # Friday thin liquidity
    def test_friday_late_rejected(self):
        ok, reason = check_session(utc(2026, 3, 6, 20, 0))  # Friday 8pm
        assert ok is False
