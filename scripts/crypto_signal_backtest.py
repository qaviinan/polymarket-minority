"""Copy-trade crypto price bets of Skilled Polymarket accounts on spot/perp prices.

Each fill on a crypto *price* market (e.g. "Bitcoin above $68,000 on November 1?",
"Will Solana dip to $130 in October?") is mapped to a direction on the underlying coin:
bullish market + YES buy / NO sell -> long; bearish market (dip/below) -> short; and the
mirror cases. Fills are bucketed per (cohort, market, 5-minute window). A bucket with
net directional flow >= MIN_SIGNAL_USDC opens a position at the open of the next 5m bar
(no look-ahead). One position per (cohort, market); an opposite signal flips it.

Exit variants:
  to_resolution  hold until the Polymarket market end timestamp (signals at/after it skipped)
  fixed_24h      hold 24 hours from entry

Portfolio: equal weight across open positions, gross exposure <= 1x, flat otherwise.
Costs: COST_BPS per side on each position's weight, plus perp funding of
FUNDING_BPS_8H every 8 hours (paid by longs, received by shorts).

Run with the reusable conda env:
  conda run -n pm-backtest python scripts/crypto_signal_backtest.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KEEP_DETERMINED = os.environ.get("CRYPTO_BT_KEEP_DETERMINED", "").strip().lower() in {"1", "true", "yes"}
OUT = ROOT / "logs" / ("crypto_backtest_naive" if KEEP_DETERMINED else "crypto_backtest")
CACHE = ROOT / "logs" / "crypto_backtest"
FILLS = ROOT / "logs" / "skilled_profile" / "fills_cache.parquet"
TOP = ROOT / "logs" / "skilled_profile" / "top_skilled.parquet"
LABELS = ROOT / "logs" / "multiple_testing" / "account_classification_primary_fdr.parquet"
META = CACHE / "market_meta.parquet"

START = "2024-10-01T00:00:00Z"
END = "2024-12-03T00:00:00Z"
BAR = "5m"
BAR_S = 300
EXCHANGE = os.environ.get("CRYPTO_BT_EXCHANGE", "coinbase")
COST_BPS = float(os.environ.get("CRYPTO_BT_COST_BPS", "12"))
FUNDING_BPS_8H = float(os.environ.get("CRYPTO_BT_FUNDING_BPS_8H", "1"))
MIN_SIGNAL_USDC = float(os.environ.get("CRYPTO_BT_MIN_SIGNAL_USDC", "100"))
TOP_N = int(os.environ.get("CRYPTO_BT_TOP_N", "10"))
PRIMARY = os.environ.get("CRYPTO_BT_PRIMARY", "Skilled_top10")

ASSETS = {
    "BTC": r"\b(bitcoin|btc)\b",
    "ETH": r"\b(ethereum|eth)\b",
    "SOL": r"\b(solana|sol)\b",
    "DOGE": r"\b(dogecoin|doge)\b",
    "XRP": r"\b(xrp|ripple)\b",
}
BULL = r"\b(above|reach|hit|all time high|ath)\b"
BEAR = r"\b(dip|below|drop|fall)\b"
EXCLUDE = r"dominance|market cap|fdv|etf|tesla|reserve|sells|strategic|microstrategy|up or down"


def _build_meta() -> pd.DataFrame:
    from src.analysis.event_grouping import register_event_views
    from src.lib.duck import get_duck

    con = get_duck()
    register_event_views(con)
    return con.execute(
        """
        SELECT m.market_id, m.question, e.title AS event_title, e.category
        FROM markets m LEFT JOIN events e ON e.event_id = m.event_id
        """
    ).df()


def _strike(q: str) -> float:
    m = re.search(r"\$\s*([\d,]*\.?\d+)\s*(k)?", q)
    if not m:
        return np.nan
    v = float(m.group(1).replace(",", ""))
    return v * 1000 if m.group(2) else v


def parse_price_market(question: str | None) -> tuple[str | None, int, str | None, float]:
    """Return (asset, sign, kind, strike).

    kind="terminal": price above strike at a date; YES is bullish (sign=+1).
    kind="touch": price touches strike in a window; YES direction depends on strike vs spot,
      resolved per signal. sign is the fallback when spot is unavailable.
    kind="ath": all-time high; YES bullish, "No ... ATH" bearish.
    """
    q = (question or "").lower()
    if re.search(EXCLUDE, q):
        return None, 0, None, np.nan
    asset = next((a for a, pat in ASSETS.items() if re.search(pat, q)), None)
    if asset is None:
        return None, 0, None, np.nan
    strike = _strike(q)
    if "all time high" in q or re.search(r"\bath\b", q):
        return asset, (-1 if q.startswith("no ") else 1), "ath", np.nan
    if re.search(r"\babove\b", q) and not re.search(BEAR, q):
        return asset, 1, "terminal", strike
    if re.search(BEAR, q):
        return asset, -1, "touch", strike
    if re.search(BULL, q):
        return asset, 1, "touch", strike
    return None, 0, None, np.nan


def crypto_markets(meta: pd.DataFrame) -> pd.DataFrame:
    parsed = [parse_price_market(q) for q in meta["question"]]
    meta = meta.assign(asset=[p[0] for p in parsed], market_sign=[p[1] for p in parsed],
                       kind=[p[2] for p in parsed], strike=[p[3] for p in parsed])
    return meta[meta["asset"].notna()].copy()


def fetch_ohlcv(asset: str) -> pd.DataFrame:
    path = CACHE / f"ohlcv_{EXCHANGE}_{asset}_{BAR}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    import ccxt

    ex = getattr(ccxt, EXCHANGE)({"enableRateLimit": True})
    symbol = f"{asset}/USD" if EXCHANGE in {"coinbase", "kraken"} else f"{asset}/USDT"
    since, end = ex.parse8601(START), ex.parse8601(END)
    rows: list[list[float]] = []
    while since < end:
        batch = ex.fetch_ohlcv(symbol, BAR, since=since, limit=300)
        if not batch:
            since += 300 * BAR_S * 1000
            continue
        rows.extend(batch)
        since = batch[-1][0] + BAR_S * 1000
        time.sleep(ex.rateLimit / 1000)
    df = pd.DataFrame(rows, columns=["ts_ms", "open", "high", "low", "close", "volume"]).drop_duplicates("ts_ms")
    df = df[df["ts_ms"] < end]
    df["ts"] = (df["ts_ms"] // 1000).astype("int64")
    df.to_parquet(path, index=False)
    return df


def cohorts(fills_users: pd.Series) -> dict[str, set[str]]:
    lab = pd.read_parquet(LABELS)
    top = pd.read_parquet(TOP).sort_values("taker_pnl", ascending=False)["user_id"].head(TOP_N)
    g = lab.set_index("user_id")["trader_group_fdr_q05"]
    skilled = set(g[g == "Skilled_Winner"].index)
    out = {
        "Skilled_top10": set(top),
        "Skilled_all": skilled,
        "Skilled_rest": skilled - set(top),
        "Lucky_Winner": set(g[g == "Lucky_Winner"].index),
        "Unlucky_Loser": set(g[g == "Unlucky_Loser"].index),
        "Unskilled_Loser": set(g[g == "Unskilled_Loser"].index),
    }
    present = set(fills_users)
    return {k: v & present for k, v in out.items()}


MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august",
     "september", "october", "november", "december"], start=1)}


def _window_start(question: str, first_fill_ts: int) -> int:
    q = question.lower()
    for name, num in MONTHS.items():
        if f"in {name}" in q:
            return int(pd.Timestamp(year=2024, month=num, day=1, tz="UTC").timestamp())
    return int(first_fill_ts)


def resolve_direction(f: pd.DataFrame, bars: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Add yes_sign and a `determined` flag per fill.

    Touch markets: the barrier is "up" if the strike is above the open at window start
    (Polymarket's reach/dip ladder), so YES is bullish for up barriers and bearish for down.
    A fill is `determined` if the barrier was already hit between window start and the fill,
    or if the fill is at/after the market end timestamp (terminal reference already set).
    """
    f = f.copy()
    f["yes_sign"] = f["market_sign"].astype(float)
    f["determined"] = f["end_ts"].notna() & (f["ts"] >= f["end_ts"])
    first_ts = f.groupby("market_id")["ts"].transform("min")
    for mid, idx in f[(f["kind"] == "touch") & f["strike"].notna()].groupby("market_id").groups.items():
        g = f.loc[idx]
        b = bars[g["asset"].iloc[0]]
        strike = float(g["strike"].iloc[0])
        w0 = _window_start(str(g["question"].iloc[0]), int(first_ts.loc[idx].iloc[0]))
        i0 = min(b.index.searchsorted(w0), len(b) - 1)
        up = strike > float(b["open"].iloc[i0])
        ext = b["high"].iloc[i0:].cummax() if up else b["low"].iloc[i0:].cummin()
        j = np.clip(ext.index.searchsorted(g["ts"].values) - 1, 0, len(ext) - 1)
        hit = ext.values[j] >= strike if up else ext.values[j] <= strike
        hit &= g["ts"].values >= w0
        f.loc[idx, "yes_sign"] = 1.0 if up else -1.0
        f.loc[idx, "determined"] = f.loc[idx, "determined"].values | hit
    return f


def build_signals(f: pd.DataFrame, members: set[str]) -> pd.DataFrame:
    x = f[f["user_id"].isin(members)].copy()
    if x.empty:
        return pd.DataFrame()
    buy = np.where(x["side"] == "BUY", 1, -1)
    yes = np.where(x["is_yes"] == 1, 1, -1)
    x["dir_usdc"] = buy * yes * x["yes_sign"] * x["amount_usdc"]
    x["bucket"] = (x["ts"] // BAR_S) * BAR_S
    s = (
        x.groupby(["market_id", "asset", "bucket"], as_index=False)
        .agg(net_usdc=("dir_usdc", "sum"), gross_usdc=("amount_usdc", "sum"),
             n_users=("user_id", "nunique"), end_ts=("end_ts", "first"), question=("question", "first"))
    )
    s = s[s["net_usdc"].abs() >= MIN_SIGNAL_USDC].copy()
    s["direction"] = np.sign(s["net_usdc"]).astype(int)
    s["entry_ts"] = s["bucket"] + BAR_S
    return s.sort_values(["market_id", "entry_ts"]).reset_index(drop=True)


def build_trades(sig: pd.DataFrame, exit_mode: str) -> pd.DataFrame:
    trades = []
    for mid, g in sig.groupby("market_id"):
        pos = None
        for r in g.itertuples(index=False):
            end = int(r.end_ts) if pd.notna(r.end_ts) else None
            if exit_mode == "to_resolution":
                if end is None or r.entry_ts >= end:
                    continue
                exit_ts = end
            else:
                exit_ts = r.entry_ts + 86_400
            if pos is not None and r.entry_ts < pos["exit_ts"]:
                if r.direction == pos["direction"]:
                    continue
                pos["exit_ts"] = r.entry_ts
                trades.append(pos)
            pos = {"market_id": mid, "asset": r.asset, "question": r.question, "direction": r.direction,
                   "entry_ts": int(r.entry_ts), "exit_ts": int(exit_ts), "signal_usdc": float(r.net_usdc),
                   "n_users": int(r.n_users)}
        if pos is not None:
            trades.append(pos)
    return pd.DataFrame(trades)


def price_at(px: dict[str, pd.Series], asset: str, ts: int) -> float:
    s = px[asset]
    i = s.index.searchsorted(ts)
    return float(s.iloc[min(i, len(s) - 1)])


def backtest(trades: pd.DataFrame, opens: dict[str, pd.Series], grid: pd.Index) -> tuple[pd.Series, pd.DataFrame]:
    if trades.empty:
        return pd.Series(0.0, index=grid), trades
    t = trades.copy()
    t["entry_px"] = [price_at(opens, a, e) for a, e in zip(t["asset"], t["entry_ts"])]
    t["exit_px"] = [price_at(opens, a, x) for a, x in zip(t["asset"], t["exit_ts"])]
    hold_h = (t["exit_ts"] - t["entry_ts"]) / 3600
    funding = FUNDING_BPS_8H / 1e4 * hold_h / 8
    t["gross_ret"] = t["direction"] * (t["exit_px"] / t["entry_px"] - 1)
    t["net_ret"] = t["gross_ret"] - 2 * COST_BPS / 1e4 - t["direction"] * funding
    t["hold_hours"] = hold_h

    bar_ret = {a: (s.shift(-1) / s - 1).reindex(grid).fillna(0.0) for a, s in opens.items()}
    n = len(grid)
    open_count = np.zeros(n)
    spans = []
    for r in t.itertuples(index=False):
        i0 = grid.searchsorted(r.entry_ts)
        i1 = grid.searchsorted(r.exit_ts)
        if i1 <= i0:
            continue
        open_count[i0:i1] += 1
        spans.append((r, i0, i1))
    port = np.zeros(n)
    w_all = np.where(open_count > 0, 1.0 / np.maximum(open_count, 1), 0.0)
    fund_bar = FUNDING_BPS_8H / 1e4 * BAR_S / (8 * 3600)
    for r, i0, i1 in spans:
        w = w_all[i0:i1]
        port[i0:i1] += w * r.direction * (bar_ret[r.asset].values[i0:i1] - fund_bar)
        port[i0] -= w[0] * COST_BPS / 1e4
        port[i1 - 1] -= w[-1] * COST_BPS / 1e4
    return pd.Series(port, index=grid), t


def to_daily(bar_returns: pd.Series) -> pd.Series:
    idx = pd.to_datetime(bar_returns.index, unit="s", utc=True)
    s = pd.Series(bar_returns.values, index=idx)
    d = (1 + s).resample("1D").prod() - 1
    d.index = d.index.tz_localize(None)
    return d


def trade_stats(t: pd.DataFrame) -> dict:
    if t.empty:
        return {"n_trades": 0}
    r = t["net_ret"]
    se = r.std(ddof=1) / np.sqrt(len(r)) if len(r) > 1 else np.nan
    return {
        "n_trades": int(len(t)),
        "n_markets": int(t["market_id"].nunique()),
        "assets": ",".join(sorted(t["asset"].unique())),
        "long_share": float((t["direction"] > 0).mean()),
        "hit_rate_net": float((r > 0).mean()),
        "mean_net_ret": float(r.mean()),
        "median_net_ret": float(r.median()),
        "t_stat_mean": float(r.mean() / se) if se and se > 0 else np.nan,
        "mean_gross_ret": float(t["gross_ret"].mean()),
        "median_hold_hours": float(t["hold_hours"].median()),
        "signal_usdc": float(t["signal_usdc"].abs().sum()),
    }


def main() -> None:
    import quantstats as qs

    OUT.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    if not META.exists():
        _build_meta().to_parquet(META, index=False)
    meta = crypto_markets(pd.read_parquet(META))
    mcols = ["market_id", "question", "asset", "market_sign", "kind", "strike"]
    meta[mcols].to_csv(OUT / "price_markets.csv", index=False)

    f = pd.read_parquet(FILLS).drop(columns=["asset"])
    f = f.merge(meta[mcols], on="market_id", how="inner")
    groups = cohorts(f["user_id"])

    assets = sorted(f["asset"].unique())
    bars = {a: fetch_ohlcv(a).set_index("ts").sort_index() for a in assets}
    opens = {a: b["open"] for a, b in bars.items()}
    f = resolve_direction(f, bars)
    det = f.assign(top=f["user_id"].isin(groups["Skilled_top10"]), sk=f["user_id"].isin(groups["Skilled_all"]))
    print("Fills on price markets:", len(f), "| already-determined share of USDC:",
          round(float(f.loc[f["determined"], "amount_usdc"].sum() / f["amount_usdc"].sum()), 4),
          "| top10:", round(float(det[det.top & det.determined].amount_usdc.sum() / max(det[det.top].amount_usdc.sum(), 1)), 4),
          "| Skilled:", round(float(det[det.sk & det.determined].amount_usdc.sum() / max(det[det.sk].amount_usdc.sum(), 1)), 4))
    if not KEEP_DETERMINED:
        f = f[~f["determined"]].copy()
    grid = opens["BTC"].index if "BTC" in opens else next(iter(opens.values())).index
    grid = grid[(grid >= int(pd.Timestamp(START).timestamp())) & (grid < int(pd.Timestamp("2024-12-01", tz="UTC").timestamp()))]
    opens = {a: s.reindex(grid).ffill().bfill() for a, s in opens.items()}
    btc_daily = to_daily((opens["BTC"].shift(-1) / opens["BTC"] - 1).fillna(0.0))

    summary, daily = [], {}
    all_trades = []
    for exit_mode in ("to_resolution", "fixed_24h"):
        for name, members in groups.items():
            sig = build_signals(f, members)
            trades = build_trades(sig, exit_mode) if len(sig) else pd.DataFrame()
            bar, t = backtest(trades, opens, grid)
            d = to_daily(bar)
            daily[(exit_mode, name)] = d
            row = {"exit_mode": exit_mode, "cohort": name, "n_accounts_with_price_bets":
                   int(f[f["user_id"].isin(members)]["user_id"].nunique()), **trade_stats(t)}
            active = d[d != 0]
            row.update({
                "total_return": float((1 + d).prod() - 1),
                "cagr": float(qs.stats.cagr(d, periods=365)) if len(active) else 0.0,
                "sharpe": float(qs.stats.sharpe(d, periods=365)) if len(active) > 1 else np.nan,
                "sortino": float(qs.stats.sortino(d, periods=365)) if len(active) > 1 else np.nan,
                "max_drawdown": float(qs.stats.max_drawdown(d)) if len(active) else 0.0,
                "vol_ann": float(d.std() * np.sqrt(365)),
                "days_invested": int((d != 0).sum()),
            })
            summary.append(row)
            if len(t):
                all_trades.append(t.assign(exit_mode=exit_mode, cohort=name))

    summ = pd.DataFrame(summary)
    trades_all = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame()
    summ.to_csv(OUT / "summary.csv", index=False)
    trades_all.to_csv(OUT / "trades.csv", index=False)
    pd.DataFrame({f"{k[0]}|{k[1]}": v for k, v in daily.items()}).assign(BTC_buy_hold=btc_daily).to_csv(OUT / "daily_returns.csv")

    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    pd.set_option("display.max_colwidth", 70)
    print("\n=== Price markets parsed:", len(meta), "| assets:", assets)
    print(f"Costs: {COST_BPS} bps/side, funding {FUNDING_BPS_8H} bps/8h, min signal ${MIN_SIGNAL_USDC:.0f}, bars {EXCHANGE} {BAR}")
    cols = ["exit_mode", "cohort", "n_accounts_with_price_bets", "n_trades", "n_markets", "long_share", "hit_rate_net",
            "mean_gross_ret", "mean_net_ret", "t_stat_mean", "total_return", "sharpe", "sortino", "max_drawdown", "days_invested"]
    print(summ[cols].round(4).to_string(index=False))

    prim = trades_all[(trades_all["cohort"] == PRIMARY)] if len(trades_all) else pd.DataFrame()
    if len(prim):
        show = prim.assign(entry=pd.to_datetime(prim["entry_ts"], unit="s"), exit=pd.to_datetime(prim["exit_ts"], unit="s"))
        print(f"\n=== {PRIMARY} trades")
        print(show[["exit_mode", "asset", "question", "direction", "entry", "exit", "signal_usdc", "entry_px", "exit_px",
                    "gross_ret", "net_ret"]].round(4).to_string(index=False))

    meta_out = {"exchange": EXCHANGE, "bar": BAR, "cost_bps_side": COST_BPS, "funding_bps_8h": FUNDING_BPS_8H,
                "min_signal_usdc": MIN_SIGNAL_USDC, "primary": PRIMARY, "keep_determined": KEEP_DETERMINED,
                "yfinance_note": "yfinance intraday limited to last 730 days; daily only for Oct-Nov 2024"}
    (OUT / "run_meta.json").write_text(json.dumps(meta_out, indent=2))

    for cohort in dict.fromkeys([PRIMARY, "Skilled_all"]):
        for exit_mode in ("to_resolution", "fixed_24h"):
            d = daily[(exit_mode, cohort)]
            if (d != 0).sum() < 2:
                print(f"\n[{exit_mode}] {cohort}: fewer than 2 invested days; skipping tearsheet")
                continue
            print(f"\n=== QuantStats tearsheet: {cohort} | exit={exit_mode} vs BTC buy-and-hold ===")
            qs.reports.metrics(d, benchmark=btc_daily, mode="full", periods_per_year=365, display=True)
            qs.reports.html(d, benchmark=btc_daily, periods_per_year=365,
                            title=f"{cohort} crypto copy-trade ({exit_mode})",
                            output=str(OUT / f"tearsheet_{cohort}_{exit_mode}.html"))
            qs.plots.snapshot(d, title=f"{cohort} copy-trade ({exit_mode})", show=False,
                              savefig=str(OUT / f"snapshot_{cohort}_{exit_mode}.png"))


if __name__ == "__main__":
    main()
