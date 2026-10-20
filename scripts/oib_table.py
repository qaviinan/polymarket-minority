"""Print OIB regression coefficients from a snapshot directory as one table."""

import sys
from pathlib import Path

import pandas as pd

root = Path(sys.argv[1] if len(sys.argv) > 1 else "logs/oib_regression")
rows = []
for p in sorted(root.glob("regression_*_*.parquet")):
    name = p.stem.removeprefix("regression_")
    if name in {"full_FE", "no_bin_FE", "no_market_FE"}:
        continue
    df = pd.read_parquet(p)
    for _, r in df.iterrows():
        rows.append({"model": name, **r.to_dict()})
pd.set_option("display.width", 220)
pd.set_option("display.float_format", lambda x: f"{x:.4g}")
print(pd.DataFrame(rows).to_string(index=False))
