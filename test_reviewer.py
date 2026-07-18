from trading.outcome_reviewer import OutcomeReviewer
reviewer = OutcomeReviewer()
result = reviewer.review_open_positions()
print(f'Positions checked: {result.positions_checked}')
print(f'Positions closed: {result.positions_closed}')
print(f'Total PnL: {result.total_realized_pnl}')
print(f'Closed details: {result.closed_details}')
print(f'Errors: {result.errors}')

perf = reviewer.calculate_signal_performance()
print(f'\nSignal Performance: {perf}')

report = reviewer.generate_report(result, perf)
print(f'\nReport:\n{report}')
