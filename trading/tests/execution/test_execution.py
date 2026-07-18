"""Tests for execution engine."""
import os
import tempfile
from datetime import datetime, timedelta
from unittest import TestCase

from trading.execution import (
    ExecutionEngine,
    SafetyEngine,
    PaperBroker,
    OrderRequest,
    OrderResult,
    AccountInfo,
)


class TestExecutionEngine(TestCase):
    """Test the full execution pipeline."""

    def setUp(self):
        """Set up a test portfolio with fresh state."""
        self.temp_dir = tempfile.mkdtemp()
        os.environ["HERMES_TESTING"] = "1"
        # Init a paper portfolio in the temp dir
        from trading.portfolio.engine import init_portfolio
        init_portfolio(capital=1_000_000.0, dir_path=self.temp_dir)

        # Ensure safety state dir exists
        safety_dir = os.path.join(self.temp_dir, "safety")
        os.makedirs(safety_dir, exist_ok=True)

        self.broker = PaperBroker(portfolio_dir=self.temp_dir)
        self.safety = SafetyEngine(
            {
                "max_trade_size_kes": 500_000.0,
                "max_daily_loss_kes": 100_000.0,
                "max_daily_loss_pct": 5.0,
                "max_single_exposure_pct": 25.0,
                "max_position_count": 20,
                "state_dir": safety_dir,
                "emergency_stop_path": os.path.join(safety_dir, "EMERGENCY_STOP"),
            }
        )
        self.engine = ExecutionEngine(self.broker, self.safety)

    def test_safety_engine_init(self):
        """Config defaults are correct."""
        self.assertEqual(self.safety.config["max_trade_size_kes"], 500_000.0)
        self.assertEqual(self.safety.config["max_daily_loss_kes"], 100_000.0)
        self.assertEqual(self.safety.config["max_daily_loss_pct"], 5.0)
        self.assertEqual(self.safety.config["max_single_exposure_pct"], 25.0)
        self.assertEqual(self.safety.config["max_position_count"], 20)

    def test_safety_max_trade_size(self):
        """Rejects order over limit."""
        request = OrderRequest(
            symbol="SCOM",
            side="BUY",
            quantity=10_000,
            price=60.0,  # 600,000 KES > 500,000 limit
        )
        verdict = self.safety.check_order(
            request,
            {"cash": 1_000_000.0, "positions": {}, "total_value": 1_000_000.0},
            AccountInfo(
                cash=1_000_000.0,
                equity=1_000_000.0,
                buying_power=1_000_000.0,
                positions_count=0,
                daily_pnl=0.0,
                daily_pnl_pct=0.0,
            ),
        )
        self.assertFalse(verdict.allowed)
        self.assertIn("max_trade_size", verdict.violations)

    def test_safety_max_daily_loss(self):
        """Rejects when realised daily loss limit already hit."""
        self.safety.state["daily_realised_pnl"] = -90_000.0
        self.safety.state["daily_gross_loss"] = 20_000.0  # total loss 110k > 100k
        request = OrderRequest(
            symbol="SCOM", side="BUY", quantity=100, price=20.0
        )
        verdict = self.safety.check_order(
            request,
            {"cash": 1_000_000.0, "positions": {}, "total_value": 1_000_000.0},
            AccountInfo(
                cash=1_000_000.0,
                equity=1_000_000.0,
                buying_power=1_000_000.0,
                positions_count=0,
                daily_pnl=0.0,
                daily_pnl_pct=0.0,
            ),
        )
        self.assertFalse(verdict.allowed)
        self.assertIn("max_daily_loss_kes", verdict.violations)

    def test_safety_emergency_stop(self):
        """Rejects all orders when kill switch active."""
        self.safety.emergency_stop()
        request = OrderRequest(symbol="SCOM", side="BUY", quantity=100, price=10.0)
        verdict = self.safety.check_order(
            request,
            {"cash": 1_000_000.0, "positions": {}, "total_value": 1_000_000.0},
            AccountInfo(
                cash=1_000_000.0,
                equity=1_000_000.0,
                buying_power=1_000_000.0,
                positions_count=0,
                daily_pnl=0.0,
                daily_pnl_pct=0.0,
            ),
        )
        self.assertFalse(verdict.allowed)
        self.assertIn("emergency_stop_active", verdict.violations)

    def test_safety_manual_override_block(self):
        """Rejects trades for blocked symbol."""
        self.safety.set_manual_override("SCOM", "block")
        request = OrderRequest(symbol="SCOM", side="BUY", quantity=100, price=10.0)
        verdict = self.safety.check_order(
            request,
            {"cash": 1_000_000.0, "positions": {}, "total_value": 1_000_000.0},
            AccountInfo(
                cash=1_000_000.0,
                equity=1_000_000.0,
                buying_power=1_000_000.0,
                positions_count=0,
                daily_pnl=0.0,
                daily_pnl_pct=0.0,
            ),
        )
        self.assertFalse(verdict.allowed)
        self.assertIn("manual_block", verdict.violations)

    def test_safety_allows_valid_trade(self):
        """Allows trade under all limits."""
        request = OrderRequest(symbol="SCOM", side="BUY", quantity=100, price=10.0)
        verdict = self.safety.check_order(
            request,
            {"cash": 1_000_000.0, "positions": {}, "total_value": 1_000_000.0},
            AccountInfo(
                cash=1_000_000.0,
                equity=1_000_000.0,
                buying_power=1_000_000.0,
                positions_count=0,
                daily_pnl=0.0,
                daily_pnl_pct=0.0,
            ),
        )
        self.assertTrue(verdict.allowed)
        self.assertEqual(verdict.violations, [])

    def test_safety_reset_daily(self):
        """Counters reset correctly."""
        self.safety.state["daily_realised_pnl"] = -50_000.0
        self.safety.state["daily_trade_count"] = 5
        self.safety.state["daily_gross_loss"] = 60_000.0
        self.safety.state["last_reset_date"] = (
            datetime.now() - timedelta(days=1)
        ).strftime("%Y-%m-%d")
        self.safety.reset_daily()
        self.assertEqual(self.safety.state["daily_realised_pnl"], 0.0)
        self.assertEqual(self.safety.state["daily_trade_count"], 0)
        self.assertEqual(self.safety.state["daily_gross_loss"], 0.0)
        self.assertEqual(
            self.safety.state["last_reset_date"], datetime.now().strftime("%Y-%m-%d")
        )

    def test_safety_persistence(self):
        """State saves and loads from disk."""
        self.safety.state["daily_realised_pnl"] = -50_000.0
        self.safety._save_state()
        loaded = self.safety._load_state()
        self.assertEqual(loaded["daily_realised_pnl"], -50_000.0)

    def test_paper_broker_connect(self):
        """PaperBroker connects successfully."""
        self.assertTrue(self.broker.connect())
        self.assertTrue(self.broker.is_connected())

    def test_paper_broker_place_buy(self):
        """Places BUY through portfolio engine."""
        request = OrderRequest(symbol="SCOM", side="BUY", quantity=100, price=10.0)
        result = self.broker.place_order(request)
        self.assertTrue(result.success)
        self.assertEqual(result.symbol, "SCOM")
        self.assertEqual(result.side, "BUY")
        self.assertEqual(result.quantity, 100)

    def test_paper_broker_place_sell(self):
        """Places SELL through portfolio engine."""
        # First buy
        buy_request = OrderRequest(symbol="SCOM", side="BUY", quantity=100, price=10.0)
        self.broker.place_order(buy_request)
        # Then sell
        sell_request = OrderRequest(
            symbol="SCOM", side="SELL", quantity=50, price=12.0
        )
        result = self.broker.place_order(sell_request)
        self.assertTrue(result.success)
        self.assertEqual(result.symbol, "SCOM")
        self.assertEqual(result.side, "SELL")
        self.assertEqual(result.quantity, 50)

    def test_execution_engine_full_flow(self):
        """End-to-end: safety check → broker → record."""
        request = OrderRequest(symbol="SCOM", side="BUY", quantity=100, price=10.0)
        report = self.engine.execute(request)
        self.assertTrue(report.success)
        self.assertTrue(report.order.success)
        self.assertEqual(report.order.symbol, "SCOM")
        self.assertEqual(report.safety.allowed, True)

    def test_execution_engine_rejects_violation(self):
        """Safety gate stops unsafe order."""
        request = OrderRequest(
            symbol="SCOM", side="BUY", quantity=10_000, price=60.0
        )  # 600,000 KES > 500,000 limit
        report = self.engine.execute(request)
        self.assertFalse(report.success)
        self.assertIsNone(report.order)
        self.assertFalse(report.safety.allowed)
        self.assertIn("max_trade_size", report.safety.violations)

    def test_daily_loss_percentage(self):
        """max_daily_loss_pct check works on realised losses only."""
        # 6% of 1M already realised → over 5% cap
        self.safety.state["daily_realised_pnl"] = -60_000.0
        self.safety.state["daily_gross_loss"] = 0.0
        request = OrderRequest(
            symbol="SCOM", side="BUY", quantity=100, price=20.0
        )
        verdict = self.safety.check_order(
            request,
            {"cash": 1_000_000.0, "positions": {}, "total_value": 1_000_000.0},
            AccountInfo(
                cash=1_000_000.0,
                equity=1_000_000.0,
                buying_power=1_000_000.0,
                positions_count=0,
                daily_pnl=0.0,
                daily_pnl_pct=0.0,
            ),
        )
        self.assertFalse(verdict.allowed)
        self.assertIn("max_daily_loss_pct", verdict.violations)

    def test_max_position_count(self):
        """BUY rejected when position count exceeds limit."""
        # Mock positions
        portfolio_state = {
            "cash": 1_000_000.0,
            "positions": {f"SYM{i}": {"shares": 100, "value": 1000.0} for i in range(20)},
            "total_value": 1_000_000.0,
        }
        request = OrderRequest(symbol="SCOM", side="BUY", quantity=100, price=10.0)
        verdict = self.safety.check_order(
            request,
            portfolio_state,
            AccountInfo(
                cash=1_000_000.0,
                equity=1_000_000.0,
                buying_power=1_000_000.0,
                positions_count=20,
                daily_pnl=0.0,
                daily_pnl_pct=0.0,
            ),
        )
        self.assertFalse(verdict.allowed)
        self.assertIn("max_position_count", verdict.violations)

    def test_safe_rounding(self):
        """No floating point issues in safety calculations."""
        request = OrderRequest(symbol="SCOM", side="BUY", quantity=100, price=10.123456)
        verdict = self.safety.check_order(
            request,
            {"cash": 1_000_000.0, "positions": {}, "total_value": 1_000_000.0},
            AccountInfo(
                cash=1_000_000.0,
                equity=1_000_000.0,
                buying_power=1_000_000.0,
                positions_count=0,
                daily_pnl=0.0,
                daily_pnl_pct=0.0,
            ),
        )
        self.assertTrue(verdict.allowed)