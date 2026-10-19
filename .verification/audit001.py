"""AUDIT-001 verification: safety gate + ExecutionEngine live path must not raise."""
import os
import sys
import tempfile

sys.path.insert(0, os.path.expanduser("~/.trading"))
sys.path.insert(0, os.path.join(os.path.expanduser("~/.trading"), "trading"))

from trading.execution.models import OrderRequest, AccountInfo
from trading.execution.safety import SafetyEngine
from trading.execution.engine import ExecutionEngine
from trading.execution.brokers.paper import PaperBroker

# 1. _default_config() must include the two path keys
cfg = SafetyEngine().config
has_paths = "emergency_stop_path" in cfg and "macro_state_path" in cfg
print(f"[1] _default_config has emergency_stop_path: {'emergency_stop_path' in cfg}")
print(f"[1] _default_config has macro_state_path: {'macro_state_path' in cfg}")
print(f"[1] cfg sample: emergency_stop_path={cfg.get('emergency_stop_path')}")
print(f"[1] cfg sample: macro_state_path={cfg.get('macro_state_path')}")

# 2. check_order must not raise KeyError with empty portfolio_state + AccountInfo
try:
    safety = SafetyEngine()
    # point state files somewhere safe
    tmp = tempfile.mkdtemp(prefix="audit001_")
    safety.config["state_dir"] = tmp
    safety.config["emergency_stop_path"] = os.path.join(tmp, "EMERGENCY_STOP")
    safety.config["macro_state_path"] = os.path.join(tmp, "macro_breaker.json")
    verdict = safety.check_order(
        OrderRequest(symbol="KCB", side="BUY", quantity=1, price=90.0),
        {},
        AccountInfo(cash=100000, equity=100000, buying_power=100000, positions_count=0, daily_pnl=0.0, daily_pnl_pct=0.0),
    )
    print(f"[2] check_order did NOT raise; verdict.allowed={verdict.allowed} reason={verdict.reason}")
except KeyError as e:
    print(f"[2] FAILED KeyError: {e}")
    sys.exit(1)

# 3. ExecutionEngine live-path execute must return ExecutionReport, never raise.
# Use a sandbox broker dir (do NOT touch live portfolio).
sandbox_portfolio = os.path.join(tempfile.mkdtemp(prefix="audit001_engine_"), "portfolio")
os.makedirs(sandbox_portfolio, exist_ok=True)
import json
with open(os.path.join(sandbox_portfolio, "state.json"), "w") as f:
    json.dump({"cash": 100000.0, "positions": [], "initial_capital": 100000.0}, f)

broker = PaperBroker(portfolio_dir=sandbox_portfolio)
engine = ExecutionEngine(
    broker=broker,
    safety=SafetyEngine({
        "state_dir": sandbox_portfolio,
        "emergency_stop_path": os.path.join(sandbox_portfolio, "EMERGENCY_STOP"),
        "macro_state_path": os.path.join(sandbox_portfolio, "macro_breaker.json"),
    }),
    production=True,
    alerts_path=os.path.join(sandbox_portfolio, "alerts.log"),
)
try:
    report = engine.execute(OrderRequest(symbol="KCB", side="BUY", quantity=1, price=90.0))
    print(f"[3] engine.execute did NOT raise; success={report.success} message={report.message}")
    # Should be either success or blocked, and a real ExecutionReport
    print(f"[3] report type={type(report).__name__}")
except Exception as e:
    print(f"[3] FAILED raised: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()
    sys.exit(1)

print("AUDIT-001: OK" if has_paths else "AUDIT-001: path keys missing")
