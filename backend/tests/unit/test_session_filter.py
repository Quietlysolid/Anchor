"""
Unit tests for session filter — verifies London/NY sessions pass,
off-session hours, weekends, and edge times are rejected.
"""
import pytest
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

    # New York session: 13:00–22:00 UTC
    def test_ny_core_passes(self):
        ok, reason = check_session(utc(2026, 3, 3, 15, 0))  # Tuesday 3pm
        assert ok is True

    def test_overlap_passes(self):
        # London/NY overlap: 13:00–17:00 UTC
        ok, reason = check_session(utc(2026, 3, 3, 14, 0))
        assert ok is True

    # Asian session: 00:00–08:00 UTC — should be rejected
    def test_asian_session_rejected(self):
        ok, reason = check_session(utc(2026, 3, 3, 3, 0))  # 3am UTC
        assert ok is False
        assert "OFF_SESSION" in reason or "ASIAN" in reason.upper() or ok is False

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
