"""
Unit tests for risk gates: DrawdownMonitor and DailyLimiter.
"""
from unittest.mock import MagicMock, patch

from anchor.risk.drawdown_monitor import DrawdownMonitor
from anchor.risk.daily_limiter import DailyLimiter


class TestDrawdownMonitor:
    def setup_method(self):
        mock_cfg = MagicMock()
        mock_cfg.drawdown_reduce_pct = 0.08
        mock_cfg.drawdown_halt_pct = 0.15
        mock_cfg.monthly_halt_pct = 1.0
        self._settings_patcher = patch("anchor.risk.drawdown_monitor.settings", mock_cfg)
        self._settings_patcher.start()
        self.monitor = DrawdownMonitor()

    def teardown_method(self):
        self._settings_patcher.stop()

    def test_no_drawdown_at_start(self):
        self.monitor.update(1000.0)
        allowed, reason = self.monitor.check()
        assert allowed is True
        assert reason is None

    def test_scale_factor_is_1_when_healthy(self):
        self.monitor.update(1000.0)
        self.monitor.update(1050.0)  # new peak
        assert self.monitor.scale_factor == 1.0

    def test_reduce_triggered_at_8_pct(self):
        self.monitor.update(1000.0)
        self.monitor.update(920.0)  # 8% drawdown exactly
        assert self.monitor.scale_factor == 0.5
        allowed, reason = self.monitor.check()
        assert allowed is True  # trade allowed, just reduced

    def test_halt_triggered_at_15_pct(self):
        self.monitor.update(1000.0)
        self.monitor.update(850.0)  # 15% drawdown
        allowed, reason = self.monitor.check()
        assert allowed is False
        assert "DRAWDOWN_HALT" in reason

    def test_halt_still_active_at_16_pct(self):
        self.monitor.update(1000.0)
        self.monitor.update(840.0)  # 16% drawdown
        allowed, _ = self.monitor.check()
        assert allowed is False

    def test_recovery_clears_halt(self):
        self.monitor.update(1000.0)
        self.monitor.update(840.0)  # halt
        self.monitor.update(940.0)  # recover to <8% drawdown from peak
        allowed, _ = self.monitor.check()
        assert allowed is True
        assert self.monitor.scale_factor == 1.0

    def test_new_equity_peak_resets_drawdown(self):
        self.monitor.update(1000.0)
        self.monitor.update(950.0)   # 5% drawdown
        self.monitor.update(1100.0)  # new peak
        assert self.monitor.current_drawdown == 0.0
        assert self.monitor.scale_factor == 1.0

    def test_zero_equity_edge_case(self):
        self.monitor.update(0.0)
        assert self.monitor.current_drawdown == 0.0

    def test_current_drawdown_precision(self):
        self.monitor.update(1000.0)
        self.monitor.update(900.0)
        assert abs(self.monitor.current_drawdown - 0.10) < 1e-9


class TestDailyLimiter:
    def setup_method(self):
        self.limiter = DailyLimiter()
        self.balance = 1000.0

    def test_profitable_day_never_halts(self):
        self.limiter.record_trade(+50.0)
        self.limiter.record_trade(+20.0)
        assert self.limiter.is_halted(self.balance) is False

    def test_no_trades_never_halts(self):
        assert self.limiter.is_halted(self.balance) is False

    def test_halt_at_3pct_loss(self):
        # 3% of 1000 = $30 loss
        self.limiter.record_trade(-30.0)
        assert self.limiter.is_halted(self.balance) is True

    def test_halt_exceeded(self):
        self.limiter.record_trade(-40.0)  # 4% loss
        assert self.limiter.is_halted(self.balance) is True

    def test_below_threshold_no_halt(self):
        self.limiter.record_trade(-20.0)  # 2% — below 3% threshold
        assert self.limiter.is_halted(self.balance) is False

    def test_cumulative_losses_halt(self):
        self.limiter.record_trade(-15.0)
        self.limiter.record_trade(-16.0)  # total 31 → 3.1%
        assert self.limiter.is_halted(self.balance) is True

    def test_mixed_trades_net_loss(self):
        self.limiter.record_trade(+50.0)
        self.limiter.record_trade(-80.0)  # net -30 = -3%
        assert self.limiter.is_halted(self.balance) is True

    def test_get_today_loss_tracks_correctly(self):
        self.limiter.record_trade(-10.0)
        self.limiter.record_trade(-5.0)
        assert self.limiter.get_today_loss() == -15.0

    def test_reset_clears_old_data(self):
        self.limiter.record_trade(-50.0)
        self.limiter.reset()
        # reset only removes entries > 7 days old — today's entry stays
        assert self.limiter.get_today_loss() == -50.0
