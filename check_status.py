from trading.paper_engine import create_engine
from trading.signal_profiler import create_profiler
from trading.learning.db import get_connection, get_open_decisions

engine = create_engine()
profiler = create_profiler()

print('=== OPEN POSITIONS ===')
open_positions = engine.get_open_positions()
for pos in open_positions:
    print(f'  {pos.symbol} {pos.direction} Entry={pos.entry_price:.2f} Size={pos.position_size} Stop={pos.stop_loss} Target={pos.take_profit} Expiry={pos.expiry_hours}h UnrealizedPnL={pos.unrealized_pnl:.2f}')

print()
print('=== SIGNAL METRICS (30d) ===')
metrics = profiler.get_all_signal_metrics(30)
for m in metrics:
    sharpe_str = f"{m.sharpe_like:.2f}" if m.sharpe_like is not None else "N/A"
    print(f'  {m.signal_source}: score={m.consistency_score:.1f} wr={m.win_rate:.1f}% dd={m.max_drawdown_pct:.1f}% trades={m.executed_signals} avg={m.avg_pnl_pct:.2f}% sharpe={sharpe_str}')