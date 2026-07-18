import pandas as pd
import os

data_dir = '/home/hermes/.trading/data'
symbols = ['RELIANCE', 'TCS', 'INFY', 'HDFC', 'EABL', 'ABSA', 'SCBK', 'SCOM', 'KCB', 'EQTY']

for sym in symbols:
    # Check both naming conventions
    for fname in [f'nse_{sym}.csv', f'{sym}.csv']:
        path = os.path.join(data_dir, fname)
        if os.path.exists(path):
            df = pd.read_csv(path, index_col='date', parse_dates=True)
            if not df.empty:
                last_row = df.iloc[-1]
                print(f"{sym} ({fname}): close={last_row['close']:.2f} date={df.index[-1].date()}")
            else:
                print(f"{sym} ({fname}): EMPTY")
            break
    else:
        print(f"{sym}: NO FILE FOUND")
