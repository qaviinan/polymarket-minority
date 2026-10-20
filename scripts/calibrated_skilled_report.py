"""Trade-level dossier for the 39 accounts that beat their entry prices (calibrated-null BH survivors).

Inputs (local, produced by src.analysis.skilled_profile):
  logs/skilled_profile/calibrated_null_accounts.parquet
  logs/skilled_profile/fills_cache.parquet   (all classified accounts' fills; used as price path)
  logs/calibrated_skilled/markets_gamma.parquet  (Gamma metadata incl. resolution times)

Outputs in logs/calibrated_skilled/:
  wallet_summary.csv, positions.csv, fills_enriched.parquet
"""

from __future__ import annotations

import numpy as np
import pandas as pd

OUT = "logs/calibrated_skilled"
HORIZONS_H = (1, 6, 24)

acc = pd.read_parquet("logs/skilled_profile/calibrated_null_accounts.parquet")
surv = acc[acc.bh_q05_calibrated].set_index("user_id")
allf = pd.read_parquet("logs/skilled_profile/fills_cache.parquet")
meta = pd.read_parquet("logs/concentrated_winners/market_meta.parquet")
gam = pd.read_parquet(f"{OUT}/markets_gamma.parquet").rename(columns={"id": "market_id"})
gam["market_id"] = gam.market_id.astype(str)
meta["market_id"] = meta.market_id.astype(str)
for col in ("closedTime", "umaEndDate", "endDate"):
    gam[col + "_ts"] = pd.to_datetime(gam[col], utc=True, errors="coerce").astype("int64") // 10**9
    gam.loc[gam[col].isna(), col + "_ts"] = np.nan

f = allf[allf.user_id.isin(surv.index)].copy()
f["market_id"] = f.market_id.astype(str)
f = f.merge(meta[["market_id", "question", "event_title", "theme"]], on="market_id", how="left")
f = f.merge(gam[["market_id", "closedTime_ts", "umaEndDate_ts"]], on="market_id", how="left")
sgn = np.where(f.side.eq("BUY"), 1.0, -1.0)
f["signed_shares"] = sgn * f.amount_shares
f["pnl"] = f.signed_shares * f.payout - sgn * f.amount_usdc - f.fee
# Effective probability paid for the side the fill backs (SELL at p backs the other side at 1-p).
f["eff_price"] = np.where(sgn > 0, f.price, 1 - f.price)
f["eff_won"] = np.where(sgn > 0, f.payout, 1 - f.payout)
f["edge_per_share"] = f.eff_won - f.eff_price
f["h_to_listed_end"] = (f.end_ts - f.ts) / 3600
f["h_to_resolution"] = (f.umaEndDate_ts - f.ts) / 3600

# Markouts: VWAP of all classified fills on the same token in [t+h-0.5h, t+h+0.5h], else last trade before t+h.
px = allf[["asset", "ts", "price", "amount_shares"]].sort_values("ts")
for h in HORIZONS_H:
    tgt = f[["asset", "ts"]].assign(t=f.ts + h * 3600).sort_values("t")
    m = pd.merge_asof(tgt, px.rename(columns={"ts": "t", "price": f"px_{h}h"})[["asset", "t", f"px_{h}h"]],
                      on="t", by="asset", direction="backward")
    f = f.merge(m[["asset", "ts", f"px_{h}h"]].drop_duplicates(["asset", "ts"]), on=["asset", "ts"], how="left")
    move = f[f"px_{h}h"] - f.price
    f[f"markout_{h}h"] = np.where(sgn > 0, move, -move)  # per share, in favour of the fill
f.to_parquet(f"{OUT}/fills_enriched.parquet")

pos = (f.groupby(["user_id", "event_id", "market_id", "asset"])
       .agg(question=("question", "first"), event_title=("event_title", "first"), theme=("theme", "first"),
            first_ts=("ts", "min"), last_ts=("ts", "max"), n_fills=("ts", "size"),
            buy_usdc=("amount_usdc", lambda s: s[f.loc[s.index, "side"].eq("BUY")].sum()),
            gross_usdc=("amount_usdc", "sum"), net_shares=("signed_shares", "sum"), pnl=("pnl", "sum"),
            vwap_eff=("eff_price", lambda s: np.average(s, weights=f.loc[s.index, "amount_shares"])),
            won=("eff_won", "last"), payout=("payout", "first"),
            taker_share=("order_type", lambda s: (s == "taker").mean()),
            h_to_listed_end=("h_to_listed_end", "median"), h_to_resolution=("h_to_resolution", "median"),
            markout_1h=("markout_1h", "mean"), markout_24h=("markout_24h", "mean"),
            uma_end=("umaEndDate_ts", "first"))
       .reset_index())
pos["roi"] = pos.pnl / pos.gross_usdc.where(pos.gross_usdc > 0)
pos.to_csv(f"{OUT}/positions.csv", index=False)

# Daily mark-to-market PnL: hold net shares, mark at last classified trade each UTC day, payout after resolution.
f["day"] = pd.to_datetime(f.ts, unit="s").dt.floor("D")
days = pd.date_range(f.day.min(), pd.to_datetime(f.umaEndDate_ts.max(), unit="s").floor("D"), freq="D")
lastpx = (allf.assign(day=pd.to_datetime(allf.ts, unit="s").dt.floor("D"))
          .sort_values("ts").groupby(["asset", "day"]).price.last())


def daily_sharpe(uf: pd.DataFrame) -> tuple[float, float, int]:
    cash = uf.groupby("day").apply(lambda d: (-np.where(d.side.eq("BUY"), 1, -1) * d.amount_usdc - d.fee).sum())
    eq = []
    for a, g in uf.groupby("asset"):
        sh = g.groupby("day").signed_shares.sum().reindex(days, fill_value=0).cumsum()
        p = lastpx.get(a, pd.Series(dtype=float)).reindex(days).ffill()
        res = g.umaEndDate_ts.iloc[0]
        if pd.notna(res):
            p[days >= pd.to_datetime(res, unit="s").floor("D")] = g.payout.iloc[0]
        p = p.fillna(g.price.iloc[0])
        eq.append(sh * p)
    val = pd.concat(eq, axis=1).sum(axis=1) + cash.reindex(days, fill_value=0).cumsum()
    first, last = uf.day.min(), pd.to_datetime(uf.umaEndDate_ts.max(), unit="s").floor("D")
    d = val.diff().loc[first:last].dropna()
    cap = uf.loc[uf.side.eq("BUY"), "amount_usdc"].groupby(uf.day).sum().cumsum().max() or 1.0
    r = d / cap
    sr = r.mean() / r.std() * np.sqrt(365) if r.std() > 0 else np.nan
    dd = (val.loc[first:last].cummax() - val.loc[first:last]).max() / cap
    return sr, dd, len(d)


rows = []
for u, uf in f.groupby("user_id"):
    p = pos[pos.user_id == u]
    w = p.gross_usdc
    sr, mdd, nd = daily_sharpe(uf)
    bet_r = p.roi.dropna()
    after_res = uf.h_to_resolution < 0
    after_end = uf.h_to_listed_end < 0
    rows.append({
        "user_id": u, "group": surv.loc[u, "group"], "cohort": surv.loc[u, "cohort"],
        "pnl": p.pnl.sum(), "gross_usdc": w.sum(), "roi": p.pnl.sum() / w.sum(),
        "positions": len(p), "events": p.event_id.nunique(), "fills": len(uf),
        "win_rate_pos": (p.pnl > 0).mean(), "vwap_eff_price": np.average(p.vwap_eff, weights=w),
        "edge_per_share": np.average(uf.edge_per_share, weights=uf.amount_shares),
        "bet_sharpe": bet_r.mean() / bet_r.std() if len(bet_r) > 1 and bet_r.std() > 0 else np.nan,
        "bet_tstat": bet_r.mean() / bet_r.std() * np.sqrt(len(bet_r)) if len(bet_r) > 1 and bet_r.std() > 0 else np.nan,
        "daily_mtm_sharpe_ann": sr, "max_dd_frac_capital": mdd, "active_days": nd,
        "taker_share_usdc": uf.loc[uf.order_type.eq("taker"), "amount_usdc"].sum() / uf.amount_usdc.sum(),
        "usdc_after_listed_end": uf.loc[after_end, "amount_usdc"].sum() / uf.amount_usdc.sum(),
        "pnl_after_listed_end": uf.loc[after_end, "pnl"].sum(),
        "usdc_after_resolution": uf.loc[after_res, "amount_usdc"].sum() / uf.amount_usdc.sum(),
        "median_h_to_resolution": np.average(uf.h_to_resolution.fillna(0), weights=uf.amount_usdc),
        "markout_1h_vw": np.nansum(uf.markout_1h * uf.amount_shares) / uf.amount_shares[uf.markout_1h.notna()].sum(),
        "markout_24h_vw": np.nansum(uf.markout_24h * uf.amount_shares) / uf.amount_shares[uf.markout_24h.notna()].sum(),
        "top_theme": uf.groupby("theme").amount_usdc.sum().idxmax() if uf.theme.notna().any() else None,
        "p_calibrated": surv.loc[u, "p_calibrated"],
    })
ws = pd.DataFrame(rows).sort_values("pnl", ascending=False)
ws.to_csv(f"{OUT}/wallet_summary.csv", index=False)
pd.set_option("display.width", 250)
print(ws.drop(columns=["user_id"]).round(3).to_string())
