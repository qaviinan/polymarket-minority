"""Concentrated winners: few-bet, single-category accounts that made large profits.

The main skill test needs n_trades >= 10 and K >= 5 events, which excludes these accounts
by construction. This module scores them on the full market universe, valuing each
in-window position at its final payout (markets resolved after the fill cutoff, including
the 2024 US election, are included).

PnL per account and outcome token (taker and maker fills in the window):
    pnl = (buy_shares - sell_shares) * payout - (buy_usdc - sell_usdc) - fees
Sells of inventory bought before the window are scored against holding to resolution,
so uncovered sellers can show negative PnL.

Selection (env-overridable):
    CONC_MAX_EVENTS (3)         at most this many distinct events
    CONC_MAX_FILLS (50)         at most this many fills
    CONC_MIN_THEME_SHARE (0.9)  at least this share of volume in one theme
    CONC_MIN_PNL (1000)         at least this much resolution PnL

The price-calibrated null runs on every few-bet, single-category account, not only the
winners, so the test is not conditioned on having won.

Run: python -m src.analysis.concentrated_winners   (CONC_REFRESH=1 re-pulls from R2)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from src.analysis.event_grouping import register_event_views
from src.analysis.multiple_testing import benjamini_hochberg
from src.analysis.partition import register_order_flow_views
from src.analysis.skilled_profile import classify_theme
from src.lib.debug import debug_step
from src.lib.duck import get_duck

OUT_DIR = Path(os.environ.get("CONC_OUT_DIR", "logs/concentrated_winners"))
POS_CACHE = OUT_DIR / "positions_cache.parquet"
FILLS_CACHE = OUT_DIR / "selected_fills.parquet"
META_CACHE = OUT_DIR / "market_meta.parquet"
HOURS_CACHE = OUT_DIR / "token_hours.parquet"
LABEL_PATH = Path("logs/multiple_testing/account_classification_primary_fdr.parquet")

MAX_EVENTS = int(os.environ.get("CONC_MAX_EVENTS", "3"))
MAX_FILLS = int(os.environ.get("CONC_MAX_FILLS", "50"))
MIN_THEME_SHARE = float(os.environ.get("CONC_MIN_THEME_SHARE", "0.9"))
MIN_PNL = float(os.environ.get("CONC_MIN_PNL", "1000"))
NULL_ITER = int(os.environ.get("CONC_NULL_ITER", "20000"))
SEED = int(os.environ.get("CONC_SEED", "42"))
ELECTION_BLOC = {"Presidential Election Winner 2024", "Popular Vote Winner 2024",
                 "Which party wins 2024 US Presidential Election?"}
PRICE_BINS = [0, 0.1, 0.3, 0.5, 0.7, 0.9, 1.01]
PRICE_LABELS = ["<10c", "10-30c", "30-50c", "50-70c", "70-90c", ">=90c"]


def _refresh() -> bool:
    return os.environ.get("CONC_REFRESH", "").strip().lower() in {"1", "true", "yes"}


def pull_positions(con) -> pd.DataFrame:
    if POS_CACHE.exists() and not _refresh():
        return pd.read_parquet(POS_CACHE)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pos = con.execute(
        """
        SELECT o.user_id, CAST(o.asset AS VARCHAR) AS token, ot.market_id, m.event_id,
          CASE WHEN CAST(o.asset AS VARCHAR) = CAST(m.yes_token_id AS VARCHAR) THEN 1 ELSE 0 END AS is_yes,
          ANY_VALUE(ot.resolved_payout) AS payout,
          ANY_VALUE(mr.scheduled_end_ts) AS end_ts,
          COUNT(*) AS n_fills,
          SUM(CASE WHEN lower(o.order_type) = 'taker' THEN 1 ELSE 0 END) AS n_taker_fills,
          SUM(CASE WHEN upper(o.side) = 'BUY' THEN o.amount_usdc ELSE 0 END) AS buy_usdc,
          SUM(CASE WHEN upper(o.side) = 'BUY' THEN o.amount_shares ELSE 0 END) AS buy_shares,
          SUM(CASE WHEN upper(o.side) = 'SELL' THEN o.amount_usdc ELSE 0 END) AS sell_usdc,
          SUM(CASE WHEN upper(o.side) = 'SELL' THEN o.amount_shares ELSE 0 END) AS sell_shares,
          SUM(COALESCE(o.fee, 0)) AS fees,
          MIN(CAST(o.block_timestamp AS BIGINT)) AS first_ts,
          MAX(CAST(o.block_timestamp AS BIGINT)) AS last_ts,
          SUM(CAST(o.block_timestamp AS DOUBLE) * o.amount_usdc) / NULLIF(SUM(o.amount_usdc), 0) AS wavg_ts
        FROM order_filled o
        JOIN outcome_tokens ot ON CAST(ot.token_id AS VARCHAR) = CAST(o.asset AS VARCHAR)
        JOIN markets m ON m.market_id = ot.market_id
        LEFT JOIN market_resolution mr ON mr.market_id = m.market_id
        GROUP BY 1, 2, 3, 4, 5
        """
    ).df()
    pos.to_parquet(POS_CACHE, index=False)
    return pos


def pull_meta(con) -> pd.DataFrame:
    if META_CACHE.exists() and not _refresh():
        return pd.read_parquet(META_CACHE)
    meta = con.execute(
        """
        SELECT m.market_id, m.question, e.title AS event_title, e.category
        FROM markets m LEFT JOIN events e ON e.event_id = m.event_id
        """
    ).df()
    meta["theme"] = [classify_theme(c, t, q) for c, t, q in zip(meta["category"], meta["event_title"], meta["question"])]
    meta.to_parquet(META_CACHE, index=False)
    return meta


def pull_selected_fills(con, users: list[str]) -> pd.DataFrame:
    if FILLS_CACHE.exists() and not _refresh():
        cached = pd.read_parquet(FILLS_CACHE)
        if set(users) <= set(cached["user_id"]):
            return cached[cached["user_id"].isin(users)]
    con.register("_sel", pd.DataFrame({"user_id": users}))
    fills = con.execute(
        """
        SELECT o.user_id, CAST(o.block_timestamp AS BIGINT) AS ts, lower(o.order_type) AS order_type,
          upper(o.side) AS side, o.price, o.amount_usdc, o.amount_shares, COALESCE(o.fee, 0) AS fee,
          CAST(o.asset AS VARCHAR) AS token, ot.market_id, o.counterparty_id
        FROM order_filled o
        JOIN _sel s ON s.user_id = o.user_id
        JOIN outcome_tokens ot ON CAST(ot.token_id AS VARCHAR) = CAST(o.asset AS VARCHAR)
        """
    ).df()
    con.unregister("_sel")
    fills.to_parquet(FILLS_CACHE, index=False)
    return fills


def pull_token_hours(con, tokens: list[str]) -> pd.DataFrame:
    """Hourly VWAP and taker buy/sell flow per outcome token (for markouts)."""
    if HOURS_CACHE.exists() and not _refresh():
        cached = pd.read_parquet(HOURS_CACHE)
        if set(tokens) <= set(cached["token"]):
            return cached
    con.register("_tok", pd.DataFrame({"token": tokens}))
    h = con.execute(
        """
        SELECT CAST(o.asset AS VARCHAR) AS token,
          CAST(FLOOR(CAST(o.block_timestamp AS BIGINT) / 3600) AS BIGINT) AS hour,
          SUM(o.amount_usdc) AS usdc, SUM(o.amount_shares) AS shares,
          SUM(CASE WHEN lower(o.order_type)='taker' AND upper(o.side)='BUY' THEN o.amount_shares ELSE 0 END) AS tb_sh,
          SUM(CASE WHEN lower(o.order_type)='taker' AND upper(o.side)='BUY' THEN o.amount_usdc ELSE 0 END) AS tb_usdc,
          SUM(CASE WHEN lower(o.order_type)='taker' AND upper(o.side)='SELL' THEN o.amount_shares ELSE 0 END) AS ts_sh,
          SUM(CASE WHEN lower(o.order_type)='taker' AND upper(o.side)='SELL' THEN o.amount_usdc ELSE 0 END) AS ts_usdc
        FROM order_filled o JOIN _tok t ON t.token = CAST(o.asset AS VARCHAR)
        GROUP BY 1, 2
        """
    ).df()
    con.unregister("_tok")
    h["vwap"] = h["usdc"] / h["shares"].replace(0, np.nan)
    h.to_parquet(HOURS_CACHE, index=False)
    return h


def _vwap_lookup(hours: pd.DataFrame) -> dict[str, pd.Series]:
    """Per token, forward-filled hourly VWAP on a contiguous hour grid."""
    out = {}
    for tok, g in hours.groupby("token"):
        s = g.set_index("hour")["vwap"].sort_index()
        grid = np.arange(s.index.min(), s.index.max() + 1)
        out[tok] = s.reindex(grid).ffill()
    return out


def markouts(fills: pd.DataFrame, hours: pd.DataFrame, horizons=(1, 6, 24, 72)) -> pd.DataFrame:
    """Signed price move after each fill: dir * (VWAP in hour(ts)+h - fill price)."""
    look = _vwap_lookup(hours)
    f = fills.copy()
    f["dir"] = np.where(f["side"] == "BUY", 1.0, -1.0)
    f["hour"] = f["ts"] // 3600
    for h in horizons:
        vals = np.full(len(f), np.nan)
        for tok, idx in f.groupby("token").groups.items():
            s = look.get(tok)
            if s is None:
                continue
            target = f.loc[idx, "hour"].values + h
            ok = (target >= s.index[0]) & (target <= s.index[-1])
            v = np.full(len(idx), np.nan)
            v[ok] = s.reindex(target[ok]).values
            vals[f.index.get_indexer(idx)] = v
        f[f"mo_{h}h"] = f["dir"] * (vals - f["price"])
    return f


def timing_excess(fills: pd.DataFrame, hours: pd.DataFrame, horizons=(1, 6, 24, 72)) -> pd.DataFrame:
    """Drift after each fill minus the mean drift of same-side taker flow in that token.

    drift_h = dir * (VWAP(hour+h) - VWAP(hour)). The BUY benchmark weights each hour of the
    token's life by taker buy shares, the SELL benchmark by taker sell shares. Both terms
    share the token's outcome, so a positive excess means good timing, not just a winning side.
    """
    look = _vwap_lookup(hours)
    f = fills.copy()
    f["dir"] = np.where(f["side"] == "BUY", 1.0, -1.0)
    f["hour"] = f["ts"] // 3600
    for h in horizons:
        bench_buy, bench_sell = {}, {}
        for tok, g in hours.groupby("token"):
            s = look[tok]
            now = s.reindex(g["hour"].values).values
            fut = s.reindex(g["hour"].values + h).values
            d = fut - now
            ok = ~np.isnan(d)
            wb, ws = g["tb_sh"].values[ok], g["ts_sh"].values[ok]
            bench_buy[tok] = float(np.sum(wb * d[ok]) / wb.sum()) if wb.sum() > 0 else np.nan
            bench_sell[tok] = float(-np.sum(ws * d[ok]) / ws.sum()) if ws.sum() > 0 else np.nan
        drift = np.full(len(f), np.nan)
        for tok, idx in f.groupby("token").groups.items():
            s = look.get(tok)
            if s is None:
                continue
            hr = f.loc[idx, "hour"].values
            now = s.reindex(hr).values
            fut = s.reindex(hr + h).values
            drift[f.index.get_indexer(idx)] = fut - now
        f[f"drift_{h}h"] = f["dir"] * drift
        bench = np.where(f["dir"] > 0, f["token"].map(bench_buy), f["token"].map(bench_sell))
        f[f"excess_{h}h"] = f[f"drift_{h}h"] - bench
    return f


def baseline_markouts(hours: pd.DataFrame, horizons=(1, 6, 24, 72)) -> dict[str, float]:
    """Share-weighted markout of all taker flow in the same tokens (net of buy/sell sides)."""
    look = _vwap_lookup(hours)
    res = {}
    for h in horizons:
        num = den = 0.0
        for tok, g in hours.groupby("token"):
            s = look[tok]
            tgt = g["hour"].values + h
            ok = tgt <= s.index[-1]
            if not ok.any():
                continue
            fut = s.reindex(tgt[ok]).values
            gb = g[ok]
            buy_px = gb["tb_usdc"] / gb["tb_sh"].replace(0, np.nan)
            sell_px = gb["ts_usdc"] / gb["ts_sh"].replace(0, np.nan)
            mb = np.nan_to_num((fut - buy_px.values) * gb["tb_sh"].values)
            ms = np.nan_to_num((sell_px.values - fut) * gb["ts_sh"].values)
            valid = ~np.isnan(fut)
            num += float((mb + ms)[valid].sum())
            den += float((gb["tb_sh"].values + gb["ts_sh"].values)[valid].sum())
        res[f"mo_{h}h"] = num / den if den else np.nan
    return res


def calibration(p: pd.DataFrame, users: pd.Index, exclude_events: set, n_boot: int = 2000, seed: int = SEED) -> pd.DataFrame:
    """Realized win rate vs share-weighted entry price for net-long positions, event-bootstrap CIs."""
    q = p[p["user_id"].isin(users) & (p["net_shares"] > 0) & ~p["event_title"].isin(exclude_events)].copy()
    q["entry"] = (q["net_cost"] / q["net_shares"]).clip(0, 1)
    q["won_sh"] = q["net_shares"] * (q["payout"] > 0.5)
    q["px_sh"] = q["net_shares"] * q["entry"]
    q["bucket"] = pd.cut(q["entry"], PRICE_BINS, labels=PRICE_LABELS, right=False)
    rng = np.random.default_rng(seed)
    rows = []
    for b, g in q.groupby("bucket", observed=True):
        ev = g.groupby("event_id")[["won_sh", "px_sh", "net_shares"]].sum()
        pt = (ev["won_sh"].sum() - ev["px_sh"].sum()) / ev["net_shares"].sum()
        idx = rng.integers(0, len(ev), (n_boot, len(ev)))
        w, pxs, sh = ev["won_sh"].values, ev["px_sh"].values, ev["net_shares"].values
        boots = (w[idx].sum(1) - pxs[idx].sum(1)) / sh[idx].sum(1)
        rows.append({"bucket": b, "positions": len(g), "events": len(ev),
                     "avg_entry": ev["px_sh"].sum() / ev["net_shares"].sum(),
                     "realized_win_rate": ev["won_sh"].sum() / ev["net_shares"].sum(),
                     "edge_per_share": pt, "ci_lo": float(np.quantile(boots, 0.025)),
                     "ci_hi": float(np.quantile(boots, 0.975)), "pnl": float(g["pnl"].sum())})
    return pd.DataFrame(rows)


def clusters(fills: pd.DataFrame, users: set, window_s: int = 60, min_cotimed: int = 2) -> pd.DataFrame:
    """Union-find clusters of wallets trading the same token and side within window_s seconds."""
    f = fills[fills["user_id"].isin(users)].copy()
    f["b"] = f["ts"] // window_s
    keys = f.groupby(["token", "side", "b"])["user_id"].apply(lambda s: sorted(set(s)))
    keys = keys[keys.apply(len) > 1]
    pair_counts: dict[tuple[str, str], int] = {}
    for us in keys:
        for i in range(len(us)):
            for j in range(i + 1, len(us)):
                pair_counts[(us[i], us[j])] = pair_counts.get((us[i], us[j]), 0) + 1
    parent: dict[str, str] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for (a, b), n in pair_counts.items():
        if n >= min_cotimed:
            parent[find(a)] = find(b)
    members = pd.Series({u: find(u) for u in parent})
    if members.empty:
        return pd.DataFrame(columns=["user_id", "cluster"])
    return members.rename("cluster").rename_axis("user_id").reset_index()


def watchlist(sel: pd.DataFrame, mo: pd.DataFrame, positions: pd.DataFrame) -> pd.DataFrame:
    """Rank non-election concentrated winners on timing, entry price and calibrated p.

    score = rank(excess_24h) + rank(-avg_buy_price) + rank(-p_calibrated), as percentile
    ranks with missing values at 0.5. Higher is stronger.
    """
    ne = sel[sel["top_theme"] != "US election outcome"].copy()
    m = mo[mo["user_id"].isin(ne["user_id"])]

    def wavg(g, c):
        g = g[g[c].notna()]
        return np.average(g[c], weights=g["amount_shares"]) if len(g) else np.nan

    ex = m.groupby("user_id").apply(lambda g: pd.Series({"excess_6h": wavg(g, "excess_6h"),
                                                        "excess_24h": wavg(g, "excess_24h")}), include_groups=False)
    pos = positions[positions["user_id"].isin(ne["user_id"]) & (positions["buy_shares"] > 0)]
    entry = (pos.groupby("user_id")["buy_usdc"].sum() / pos.groupby("user_id")["buy_shares"].sum()).rename("avg_buy_price")
    main_ev = (positions[positions["user_id"].isin(ne["user_id"])].sort_values("pnl", ascending=False)
               .drop_duplicates("user_id").set_index("user_id")["event_title"].rename("main_event"))
    ne = ne.set_index("user_id").join(ex).join(entry).join(main_ev)
    ne["score"] = (ne["excess_24h"].rank(pct=True).fillna(0.5) + (-ne["avg_buy_price"]).rank(pct=True).fillna(0.5)
                   + (-ne["p_calibrated"]).rank(pct=True).fillna(0.5))
    return ne.sort_values("score", ascending=False).reset_index()


def enrich_positions(pos: pd.DataFrame, meta: pd.DataFrame) -> pd.DataFrame:
    p = pos.merge(meta[["market_id", "question", "event_title", "theme"]], on="market_id", how="left")
    p["net_shares"] = p["buy_shares"] - p["sell_shares"]
    p["net_cost"] = p["buy_usdc"] - p["sell_usdc"]
    p["volume"] = p["buy_usdc"] + p["sell_usdc"]
    p["pnl"] = p["net_shares"] * p["payout"] - p["net_cost"] - p["fees"]
    p["avg_buy_price"] = p["buy_usdc"] / p["buy_shares"].replace(0, np.nan)
    p["hours_before_end"] = (p["end_ts"] - p["wavg_ts"]) / 3600.0
    return p


def account_table(p: pd.DataFrame) -> pd.DataFrame:
    theme_vol = p.groupby(["user_id", "theme"])["volume"].sum().reset_index()
    top_theme = theme_vol.sort_values("volume", ascending=False).drop_duplicates("user_id")
    tot = theme_vol.groupby("user_id")["volume"].sum()
    top_theme = top_theme.set_index("user_id")
    acc = p.groupby("user_id").agg(
        pnl=("pnl", "sum"), volume=("volume", "sum"), buy_usdc=("buy_usdc", "sum"),
        sell_usdc=("sell_usdc", "sum"), n_fills=("n_fills", "sum"), n_taker_fills=("n_taker_fills", "sum"),
        n_markets=("market_id", "nunique"), n_events=("event_id", "nunique"),
        first_ts=("first_ts", "min"), last_ts=("last_ts", "max"),
    )
    acc["top_theme"] = top_theme["theme"]
    acc["top_theme_share"] = top_theme["volume"] / tot
    acc["roi"] = acc["pnl"] / acc["buy_usdc"].replace(0, np.nan)
    acc["buy_share"] = acc["buy_usdc"] / acc["volume"].replace(0, np.nan)
    return acc.reset_index()


def calibrated_null(p: pd.DataFrame, users: pd.Index, n_iter: int, seed: int) -> pd.DataFrame:
    """Per-account p-value of resolution PnL under price-calibrated outcomes.

    Each (account, market) gets one draw. YES pays 1 with probability equal to the account's
    average entry price, read as a YES price and clipped to [0.005, 0.995]; NO pays the
    complement. Sell-only tokens use the average sell price.
    """
    rng = np.random.default_rng(seed)
    q = p[p["user_id"].isin(users)].copy()
    px = np.where(q["buy_shares"] > 0, q["buy_usdc"] / q["buy_shares"].replace(0, np.nan),
                  q["sell_usdc"] / q["sell_shares"].replace(0, np.nan))
    q["p_yes"] = np.clip(np.where(q["is_yes"] == 1, px, 1 - px), 0.005, 0.995)
    mk = q.groupby(["user_id", "market_id"]).agg(p_yes=("p_yes", "mean")).reset_index()
    yes_sh = q.assign(s=np.where(q["is_yes"] == 1, q["net_shares"], 0.0)).groupby(["user_id", "market_id"])["s"].sum()
    no_sh = q.assign(s=np.where(q["is_yes"] == 0, q["net_shares"], 0.0)).groupby(["user_id", "market_id"])["s"].sum()
    cost = q.assign(c=q["net_cost"] + q["fees"]).groupby(["user_id", "market_id"])["c"].sum()
    mk = mk.set_index(["user_id", "market_id"]).join(yes_sh.rename("yes_sh")).join(no_sh.rename("no_sh")).join(cost.rename("cost")).reset_index()
    real = q.groupby("user_id")["pnl"].sum()

    codes, uniq = pd.factorize(mk["user_id"])
    n_users = len(uniq)
    exceed = np.zeros(n_users)
    sims_sum = np.zeros(n_users)
    sims_sq = np.zeros(n_users)
    real_arr = real.reindex(uniq).values
    chunk = 100
    pyes = mk["p_yes"].values
    ys, ns, cs = mk["yes_sh"].values, mk["no_sh"].values, mk["cost"].values
    done = 0
    while done < n_iter:
        k = min(chunk, n_iter - done)
        win = rng.random((k, len(mk))) < pyes
        pnl = np.where(win, ys, ns) - cs
        tot = np.zeros((k, n_users))
        for i in range(k):
            tot[i] = np.bincount(codes, weights=pnl[i], minlength=n_users)
        exceed += (tot >= real_arr - 1e-9).sum(axis=0)
        sims_sum += tot.sum(axis=0)
        sims_sq += (tot ** 2).sum(axis=0)
        done += k
    mean = sims_sum / n_iter
    sd = np.sqrt(np.maximum(sims_sq / n_iter - mean ** 2, 0))
    return pd.DataFrame({
        "user_id": uniq, "real_pnl": real_arr, "null_mean_pnl": mean, "null_sd_pnl": sd,
        "z": (real_arr - mean) / np.where(sd > 0, sd, np.nan),
        "p_calibrated": (exceed + 1) / (n_iter + 1),
    })


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with debug_step("concentrated_winners") as dbg:
        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)
        pos = pull_positions(con)
        meta = pull_meta(con)
        dbg.log("ingest", "positions_rows", len(pos))
        p = enrich_positions(pos, meta)
        dbg.assertion("payout_coverage", float(p["payout"].notna().mean()) > 0.99,
                      {"share_with_payout": float(p["payout"].notna().mean())})
        p = p[p["payout"].notna()]
        acc = account_table(p)
        labels = pd.read_parquet(LABEL_PATH)[["user_id", "trader_group_fdr_q05", "n_trades", "n_events"]]
        acc = acc.merge(labels.rename(columns={"trader_group_fdr_q05": "stage1_group", "n_trades": "stage1_n_trades",
                                               "n_events": "stage1_n_events"}), on="user_id", how="left")
        acc["stage1_group"] = acc["stage1_group"].fillna("Not_in_Stage1_universe")
        acc.to_parquet(OUT_DIR / "accounts_all.parquet", index=False)

        few = acc[(acc["n_events"] <= MAX_EVENTS) & (acc["n_fills"] <= MAX_FILLS)]
        within = few[few["top_theme_share"] >= MIN_THEME_SHARE]
        sel = within[within["pnl"] >= MIN_PNL].sort_values("pnl", ascending=False)
        dbg.log("select", "n_accounts_all", len(acc))
        dbg.log("select", "n_few_bet", len(few))
        dbg.log("select", "n_few_within_category", len(within))
        dbg.log("select", "n_concentrated_winners", len(sel))

        null_path = OUT_DIR / "calibrated_null_few_within.parquet"
        if null_path.exists() and not _refresh():
            nul = pd.read_parquet(null_path)
        else:
            nul = calibrated_null(p, pd.Index(within["user_id"]), NULL_ITER, SEED)
            nul["bh_q05"] = benjamini_hochberg(nul["p_calibrated"].values, 0.05)
            nul.to_parquet(null_path, index=False)
        within = within.merge(nul, on="user_id", how="left")
        sel = sel.merge(nul, on="user_id", how="left")
        losers = within[within["pnl"] <= -MIN_PNL].sort_values("pnl")
        within.to_parquet(OUT_DIR / "few_bet_within_category.parquet", index=False)
        sel.to_parquet(OUT_DIR / "concentrated_winners.parquet", index=False)
        losers.to_parquet(OUT_DIR / "concentrated_losers.parquet", index=False)
        dbg.log("select", "n_concentrated_losers", len(losers))

        sp = p[p["user_id"].isin(sel["user_id"])].copy()
        sp.to_parquet(OUT_DIR / "concentrated_positions.parquet", index=False)

        wp = p[p["user_id"].isin(within["user_id"])]
        wp = wp.assign(side_group=np.select([wp["user_id"].isin(sel["user_id"]), wp["user_id"].isin(losers["user_id"])],
                                            ["winner", "loser"], "other"))
        sym = (wp.groupby(["event_title", "side_group"])
               .agg(accounts=("user_id", "nunique"), pnl=("pnl", "sum"), volume=("volume", "sum"))
               .unstack("side_group", fill_value=0))
        sym.columns = [f"{a}_{b}" for a, b in sym.columns]
        sym["net_pnl_few_within"] = wp.groupby("event_title")["pnl"].sum()
        sym = sym.sort_values("pnl_winner", ascending=False)
        sym.to_csv(OUT_DIR / "event_symmetry.csv")

        users = sel["user_id"].tolist() + losers["user_id"].tolist()
        fills = pull_selected_fills(con, users)
        fills = fills.merge(meta[["market_id", "event_title", "question", "theme"]], on="market_id", how="left")
        fills["group"] = np.where(fills["user_id"].isin(sel["user_id"]), "winner", "loser")
        fills["election_bloc"] = fills["event_title"].isin(ELECTION_BLOC)
        markets = set(fills["market_id"])
        tokens = sorted(set(p.loc[p["market_id"].isin(markets), "token"]))
        hours = pull_token_hours(con, tokens)
        mo = timing_excess(markouts(fills, hours), hours)
        mo.to_parquet(OUT_DIR / "selected_fills_markouts.parquet", index=False)
        mcols = [c for c in mo.columns if c.startswith(("mo_", "excess_"))]
        mo_tab = (mo.groupby(["election_bloc", "group"])
                  .apply(lambda g: pd.Series({**{c: np.average(g[c].dropna(), weights=g.loc[g[c].notna(), "amount_shares"])
                                                  if g[c].notna().any() else np.nan for c in mcols},
                                              "fills": len(g), "accounts": g["user_id"].nunique()}), include_groups=False)
                  .reset_index())
        base = baseline_markouts(hours)
        mo_tab.to_csv(OUT_DIR / "markouts.csv", index=False)
        dbg.log("markouts", "baseline_all_taker_flow", json.dumps(base))

        cal_all = calibration(p, pd.Index(within["user_id"]), set())
        cal_ex = calibration(p, pd.Index(within["user_id"]), ELECTION_BLOC)
        cal_all.to_csv(OUT_DIR / "calibration_few_within_all.csv", index=False)
        cal_ex.to_csv(OUT_DIR / "calibration_few_within_ex_election.csv", index=False)

        wl = watchlist(sel, mo, sp)
        wl.to_csv(OUT_DIR / "watchlist_non_election.csv", index=False)

        cl = clusters(fills, set(sel["user_id"]))
        if len(cl):
            cl = cl.merge(sel[["user_id", "pnl", "top_theme"]], on="user_id", how="left")
            sizes = cl.groupby("cluster")["user_id"].transform("size")
            cl = cl[sizes >= 2]
        cl.to_csv(OUT_DIR / "winner_clusters.csv", index=False)

        summary = {
            "params": {"max_events": MAX_EVENTS, "max_fills": MAX_FILLS, "min_theme_share": MIN_THEME_SHARE,
                       "min_pnl": MIN_PNL, "null_iter": NULL_ITER},
            "n_accounts_all": int(len(acc)), "n_few_bet": int(len(few)),
            "n_few_within_category": int(len(within)), "n_concentrated_winners": int(len(sel)),
            "few_within_total_pnl": float(within["pnl"].sum()),
            "few_within_null_mean_total": float(within["null_mean_pnl"].sum()),
            "conc_winners_total_pnl": float(sel["pnl"].sum()),
            "conc_winners_bh_q05": int(sel["bh_q05"].sum()),
            "few_within_bh_q05": int(within["bh_q05"].sum()),
            "n_concentrated_losers": int(len(losers)),
            "conc_losers_total_pnl": float(losers["pnl"].sum()),
            "baseline_markouts_all_taker_flow": base,
            "n_winner_cluster_wallets": int(len(cl)),
            "n_winner_clusters": int(cl["cluster"].nunique()) if len(cl) else 0,
        }
        (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
        dbg.log("summary", "summary", json.dumps(summary))


if __name__ == "__main__":
    main()
