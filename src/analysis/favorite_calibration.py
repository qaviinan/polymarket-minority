"""Strategy v1 validation step 1: is buying favorites profitable, and if not, how did Skilled win?

Every taker fill is restated as "bought token X at effective price q" (a SELL at p backs the
complement at 1-p), then calibrated against the realized payout. Three questions:

1. Calibration: do q>=0.90 fills win more often than q, after fees, by bucket and horizon?
2. Strategy test: buying the favorite in every resolved market at a fixed horizon before resolution.
3. Selection: how much of each label group's favorite PnL is explained by *where* it bought
   (price, time to resolution, theme, live) versus residual pick-skill, using leave-group-out
   benchmarks; and whether the Skilled label itself selects on outcome.

Confidence intervals resample whole events (Poisson bootstrap) because outcomes within an event
are mutually exclusive or strongly correlated.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd

from src.analysis.event_grouping import register_event_views, resolved_by_cutoff_sql
from src.analysis.partition import register_order_flow_views
from src.analysis.skilled_profile import classify_theme
from src.lib.duck import get_duck
from src.lib.http import PoliteClient

OUT = Path(os.environ.get("FAV_CAL_OUT_DIR", "logs/favorite_calibration"))
FILLS_PATH = OUT / "taker_fills.parquet"
RES_PATH = OUT / "market_resolution_times.parquet"
LABEL_PATH = Path("logs/multiple_testing/account_classification_primary_fdr.parquet")
LABEL_COL = "trader_group_fdr_q05"
CAL_NULL_PATH = Path("logs/skilled_profile/calibrated_null_accounts.parquet")
N_BOOT = int(os.environ.get("FAV_CAL_BOOT", "1000"))
FEE_RATE_2026 = float(os.environ.get("FAV_CAL_FEE_RATE", "0.05"))
FAV = 0.90

Q_EDGES = [0, .02, .05, .10, .20, .30, .40, .50, .60, .70, .80, .90, .95, .98, .99, 1.0001]
Q_LABELS = ["<2", "2-5", "5-10", "10-20", "20-30", "30-40", "40-50", "50-60", "60-70",
            "70-80", "80-90", "90-95", "95-98", "98-99", "99+"]
H_EDGES = [-np.inf, 0, 1, 6, 24, 72, 168, 720, np.inf]
H_LABELS = ["after_res", "<1h", "1-6h", "6-24h", "1-3d", "3-7d", "7-30d", ">30d"]
SIZE_EDGES = [0, 10, 100, 1_000, 10_000, np.inf]
SIZE_LABELS = ["<$10", "$10-100", "$100-1k", "$1k-10k", ">$10k"]


def load_fills(refresh: bool = False) -> pd.DataFrame:
    if FILLS_PATH.exists() and not refresh:
        return pd.read_parquet(FILLS_PATH)
    OUT.mkdir(parents=True, exist_ok=True)
    con = get_duck()
    register_order_flow_views(con)
    register_event_views(con)
    cutoff = int(con.execute("SELECT MAX(CAST(block_timestamp AS BIGINT)) FROM takers").fetchone()[0])
    f = con.execute(
        f"""
        SELECT t.user_id, CAST(t.block_timestamp AS BIGINT) AS ts, t.transaction_hash,
          CAST(t.asset AS VARCHAR) AS asset, m.market_id, m.event_id,
          CASE WHEN CAST(t.asset AS VARCHAR) = CAST(m.yes_token_id AS VARCHAR) THEN 1 ELSE 0 END AS is_yes,
          upper(t.side) AS side, t.price, t.amount_usdc, t.amount_shares, COALESCE(t.fee, 0) AS fee,
          ot.resolved_payout AS payout,
          epoch(TRY_CAST(m.end_date AS TIMESTAMPTZ)) AS market_end_ts,
          mr.scheduled_end_ts, e.category, e.title AS event_title, m.question
        FROM takers t
        JOIN outcome_tokens ot ON CAST(ot.token_id AS VARCHAR) = CAST(t.asset AS VARCHAR)
        JOIN markets m ON m.market_id = ot.market_id
        JOIN market_resolution mr ON mr.market_id = m.market_id
        LEFT JOIN events e ON e.event_id = m.event_id
        WHERE {resolved_by_cutoff_sql("m", str(cutoff))}
        """
    ).df()
    f.to_parquet(FILLS_PATH, index=False)
    return f


def load_resolution_times(market_ids: list[str], refresh: bool = False) -> pd.DataFrame:
    """Gamma closedTime / umaEndDate per market (metadata on R2 has only scheduled end dates)."""
    if RES_PATH.exists() and not refresh:
        return pd.read_parquet(RES_PATH)
    rows = []
    with PoliteClient("https://gamma-api.polymarket.com", min_interval_s=0.2) as cli:
        for i in range(0, len(market_ids), 20):
            chunk = market_ids[i : i + 20]
            params = [("id", x) for x in chunk] + [("closed", "true"), ("limit", "50")]
            for m in cli.get_json("/markets", params=params):
                rows.append({k: m.get(k) for k in ("id", "closedTime", "umaEndDate", "endDate")})
    r = pd.DataFrame(rows).rename(columns={"id": "market_id"})
    r["market_id"] = r["market_id"].astype(str)
    for c in ("closedTime", "umaEndDate"):
        r[c + "_ts"] = pd.to_datetime(r[c], utc=True, errors="coerce", format="mixed").astype("int64") // 10**9
        r.loc[r[c].isna(), c + "_ts"] = np.nan
    r["res_ts"] = r["umaEndDate_ts"].fillna(r["closedTime_ts"])
    r.to_parquet(RES_PATH, index=False)
    return r


def load_labels() -> pd.DataFrame:
    lab = pd.read_parquet(LABEL_PATH)[["user_id", LABEL_COL]].rename(columns={LABEL_COL: "group"})
    cal = pd.read_parquet(CAL_NULL_PATH)[["user_id", "cohort", "bh_q05_calibrated"]]
    lab = lab.merge(cal, on="user_id", how="left")
    lab["calibrated_survivor"] = lab["bh_q05_calibrated"].fillna(False).astype(bool)
    return lab[["user_id", "group", "cohort", "calibrated_survivor"]]


def enrich(f: pd.DataFrame, res: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    f = f.copy()
    f["market_id"] = f["market_id"].astype(str)
    f = f.merge(res[["market_id", "res_ts"]], on="market_id", how="left")
    buy = f["side"].eq("BUY")
    f["q"] = np.where(buy, f["price"], 1.0 - f["price"])
    f["won"] = np.where(buy, f["payout"], 1.0 - f["payout"])
    f["cost"] = f["amount_shares"] * f["q"]
    f["pnl"] = f["amount_shares"] * (f["won"] - f["q"]) - f["fee"]
    f["fee_2026"] = f["amount_shares"] * FEE_RATE_2026 * f["q"] * (1 - f["q"])
    f["pnl_2026"] = f["pnl"] + f["fee"] - f["fee_2026"]
    f["q_bucket"] = pd.cut(f["q"], Q_EDGES, labels=Q_LABELS, right=False)
    f["q_cent"] = np.clip(np.floor(f["q"] * 100), 0, 99).astype(int)
    f["h_to_res"] = (f["res_ts"] - f["ts"]) / 3600.0
    f["h_bucket"] = pd.cut(f["h_to_res"], H_EDGES, labels=H_LABELS, right=False)
    f["live"] = f["ts"] > f["market_end_ts"]
    f["size_bucket"] = pd.cut(f["cost"], SIZE_EDGES, labels=SIZE_LABELS, right=False)
    themes = (
        f[["market_id", "category", "event_title", "question"]].drop_duplicates("market_id")
        .assign(theme=lambda d: [classify_theme(c, t, q) for c, t, q in zip(d.category, d.event_title, d.question)])
    )
    f = f.merge(themes[["market_id", "theme"]], on="market_id", how="left")
    f = f.merge(labels, on="user_id", how="left")
    f["group"] = f["group"].fillna("Unlabeled")
    return f


def _boot_ratio(df: pd.DataFrame, by: list[str], num: str, den: str, seed: int = 7) -> pd.DataFrame:
    """Event-cluster Poisson bootstrap CI for sum(num)/sum(den) within each `by` cell."""
    g = df.groupby(by + ["event_id"], observed=True)[[num, den]].sum().reset_index()
    events = g["event_id"].unique()
    rng = np.random.default_rng(seed)
    W = rng.poisson(1.0, size=(len(events), N_BOOT)).astype(float)
    idx = {e: i for i, e in enumerate(events)}
    out = []
    for key, c in g.groupby(by, observed=True):
        rows = c["event_id"].map(idx).to_numpy()
        n = c[num].to_numpy() @ W[rows]
        d = c[den].to_numpy() @ W[rows]
        stat = np.where(d > 0, n / np.where(d > 0, d, 1), np.nan)
        key = key if isinstance(key, tuple) else (key,)
        out.append(dict(zip(by, key)) | {
            f"{num}_ratio": c[num].sum() / c[den].sum() if c[den].sum() else np.nan,
            "ci_lo": np.nanpercentile(stat, 2.5), "ci_hi": np.nanpercentile(stat, 97.5),
            "n_events": int(c["event_id"].nunique()),
        })
    return pd.DataFrame(out)


def calibration_table(f: pd.DataFrame, by: list[str]) -> pd.DataFrame:
    f = f.assign(sh_won=f["amount_shares"] * f["won"], sh_q=f["cost"])
    agg = f.groupby(by, observed=True).agg(
        fills=("q", "size"), markets=("market_id", "nunique"), cost=("cost", "sum"),
        shares=("amount_shares", "sum"), sh_won=("sh_won", "sum"), sh_q=("sh_q", "sum"),
        pnl=("pnl", "sum"), pnl_2026=("pnl_2026", "sum"),
    ).reset_index()
    agg["avg_q"] = agg["sh_q"] / agg["shares"]
    agg["win_rate"] = agg["sh_won"] / agg["shares"]
    agg["excess_pp"] = 100 * (agg["win_rate"] - agg["avg_q"])
    agg["roi"] = agg["pnl"] / agg["cost"]
    agg["roi_2026_fee"] = agg["pnl_2026"] / agg["cost"]
    ci = _boot_ratio(f, by, "pnl", "cost").rename(columns={"ci_lo": "roi_ci_lo", "ci_hi": "roi_ci_hi"})
    agg = agg.merge(ci.drop(columns=["pnl_ratio"]), on=by, how="left")
    # each market votes once per cell: share-weighted win minus q within the market, averaged
    mk = f.groupby(by + ["market_id"], observed=True)[["sh_won", "sh_q", "amount_shares"]].sum()
    mk = (mk["sh_won"] - mk["sh_q"]) / mk["amount_shares"]
    agg = agg.merge(
        (100 * mk.groupby(level=list(range(len(by))), observed=True).mean()).rename("excess_pp_mkt_eq").reset_index(),
        on=by, how="left",
    )
    return agg.drop(columns=["sh_won", "sh_q"])


def horizon_strategy(f: pd.DataFrame, horizons_h=(1, 6, 24, 72, 168)) -> pd.DataFrame:
    """Buy the favorite in every market at last trade before (resolution - h); equal weight per market."""
    y = f.dropna(subset=["res_ts"]).copy()
    y["p_yes"] = np.where(y["is_yes"].eq(1), y["price"], 1.0 - y["price"])
    y["yes_won"] = np.where(y["is_yes"].eq(1), y["payout"], 1.0 - y["payout"])
    y = y.sort_values("ts")
    rows = []
    for h in horizons_h:
        cut = y[(y["ts"] <= y["res_ts"] - h * 3600) & (y["ts"] >= y["res_ts"] - (h + 24) * 3600)]
        last = cut.groupby("market_id").tail(1)
        fav_q = np.maximum(last["p_yes"], 1 - last["p_yes"])
        fav_won = np.where(last["p_yes"] >= 0.5, last["yes_won"], 1 - last["yes_won"])
        rows.append(pd.DataFrame({
            "horizon_h": h, "market_id": last["market_id"], "event_id": last["event_id"],
            "q": fav_q.to_numpy(), "won": fav_won, "pnl": fav_won - fav_q.to_numpy(), "cost": fav_q.to_numpy(),
        }))
    m = pd.concat(rows, ignore_index=True)
    m["q_bucket"] = pd.cut(m["q"], [0.5, 0.7, 0.8, 0.9, 0.95, 0.98, 0.99, 1.0001],
                           labels=["50-70", "70-80", "80-90", "90-95", "95-98", "98-99", "99+"], right=False)
    agg = m.groupby(["horizon_h", "q_bucket"], observed=True).agg(
        markets=("q", "size"), avg_q=("q", "mean"), win_rate=("won", "mean"),
        losses=("won", lambda s: int((s < 0.5).sum())), pnl=("pnl", "sum"), cost=("cost", "sum"),
    ).reset_index()
    agg["excess_pp"] = 100 * (agg["win_rate"] - agg["avg_q"])
    agg["roi"] = agg["pnl"] / agg["cost"]
    ci = _boot_ratio(m, ["horizon_h", "q_bucket"], "pnl", "cost")
    agg = agg.merge(ci.drop(columns=["pnl_ratio"]), on=["horizon_h", "q_bucket"], how="left")
    tot = m.groupby("horizon_h").agg(markets=("q", "size"), roi=("pnl", "sum"), cost=("cost", "sum"))
    tot["roi"] = tot["roi"] / tot["cost"]
    return agg, tot.reset_index(), m


def _cell_roi(f: pd.DataFrame, keys: list[str], exclude: pd.Series) -> pd.Series:
    """Leave-group-out ROI per cell, falling back to coarser cells when the cell is empty."""
    base = f[~exclude]
    c = base.groupby(keys, observed=True)[["pnl", "cost"]].sum()
    return (c["pnl"] / c["cost"]).rename("cell_roi")


def decomposition(f: pd.DataFrame) -> pd.DataFrame:
    fav = f[f["q"] >= FAV].copy()
    fav["h_coarse"] = fav["h_bucket"].astype(str)
    specs = {
        "E1_price": ["q_cent"],
        "E2_price_horizon": ["q_cent", "h_coarse"],
        "E3_+theme": ["q_cent", "h_coarse", "theme"],
        "E4_+live": ["q_cent", "h_coarse", "theme", "live"],
    }
    sets = {
        "Skilled_Winner (FDR, 484)": fav["group"].eq("Skilled_Winner"),
        "Calibrated survivors (39)": fav["calibrated_survivor"].fillna(False).astype(bool),
        "Lucky_Winner": fav["group"].eq("Lucky_Winner"),
        "Unlucky_Loser": fav["group"].eq("Unlucky_Loser"),
        "Unskilled_Loser": fav["group"].eq("Unskilled_Loser"),
        "Excluded / unlabeled": fav["group"].isin(["Excluded", "Unlabeled"]),
        "All takers": pd.Series(False, index=fav.index),
    }
    rows = []
    for name, mask in sets.items():
        sub = fav[mask] if mask.any() else fav
        row = {"set": name, "accounts": sub["user_id"].nunique(), "fav_cost": sub["cost"].sum(),
               "fav_pnl": sub["pnl"].sum(), "fav_roi": sub["pnl"].sum() / sub["cost"].sum(),
               "share_cost_lt24h": sub.loc[sub["h_to_res"] < 24, "cost"].sum() / sub["cost"].sum(),
               "share_cost_live": sub.loc[sub["live"], "cost"].sum() / sub["cost"].sum()}
        for ename, keys in specs.items():
            roi = _cell_roi(fav, keys, mask)
            exp = sub.join(roi, on=keys)["cell_roi"]
            fallback = sub.join(_cell_roi(fav, ["q_cent"], mask), on=["q_cent"])["cell_roi"]
            exp = exp.fillna(fallback).fillna(0.0)
            e = float((sub["cost"] * exp).sum())
            row[f"{ename}_expected"] = e
            row[f"{ename}_residual"] = row["fav_pnl"] - e
        rows.append(row)
    return pd.DataFrame(rows)


def favorite_specialists(f: pd.DataFrame, min_positions: int = 5, min_share: float = 0.8) -> pd.DataFrame:
    """Accounts whose taker cost is >= min_share at q>=0.90: outcome distribution and labels."""
    acc = f.groupby("user_id").agg(
        cost=("cost", "sum"), pnl=("pnl", "sum"), group=("group", "first"),
        positions=("asset", "nunique"), events=("event_id", "nunique"),
    )
    acc["fav_share"] = f[f["q"] >= FAV].groupby("user_id")["cost"].sum().reindex(acc.index).fillna(0) / acc["cost"]
    spec = acc[(acc["fav_share"] >= min_share) & (acc["positions"] >= min_positions)].copy()
    spec["roi"] = spec["pnl"] / spec["cost"]
    return spec


def specialist_summary(spec: pd.DataFrame) -> pd.DataFrame:
    def s(d: pd.DataFrame) -> pd.Series:
        return pd.Series({
            "accounts": len(d), "share_profitable": (d["pnl"] > 0).mean(),
            "median_pnl": d["pnl"].median(), "mean_pnl": d["pnl"].mean(), "total_pnl": d["pnl"].sum(),
            "total_cost": d["cost"].sum(), "pooled_roi": d["pnl"].sum() / d["cost"].sum(),
            "median_roi": d["roi"].median(), "p5_roi": d["roi"].quantile(0.05),
            "worst_pnl": d["pnl"].min(), "best_pnl": d["pnl"].max(),
        })
    by = spec.groupby("group").apply(s, include_groups=False).reset_index()
    allrow = s(spec).to_frame().T.assign(group="ALL favorite specialists")
    return pd.concat([allrow, by], ignore_index=True)


def plot(cal: pd.DataFrame, hz: pd.DataFrame, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(1, 2, figsize=(12, 4.6))
    c = cal.copy()
    ax[0].errorbar(c["avg_q"], c["roi"] * 100, yerr=[(c["roi"] - c["roi_ci_lo"]) * 100, (c["roi_ci_hi"] - c["roi"]) * 100],
                   fmt="o-", capsize=3)
    ax[0].axhline(0, color="k", lw=0.8)
    ax[0].set_xlabel("effective price paid (q)")
    ax[0].set_ylabel("ROI after fees, % (95% event-bootstrap CI)")
    ax[0].set_title("All taker fills, resolved markets")
    ax[0].set_ylim(-60, 60)
    for h, d in hz.groupby("horizon_h"):
        ax[1].plot(d["avg_q"], d["excess_pp"], "o-", label=f"{h}h before resolution")
    ax[1].axhline(0, color="k", lw=0.8)
    ax[1].set_xlabel("favorite price (last trade)")
    ax[1].set_ylabel("win rate − price, pp")
    ax[1].set_title("Buy the favorite in every market")
    ax[1].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path, dpi=130)


def main() -> None:
    t0 = time.time()
    OUT.mkdir(parents=True, exist_ok=True)
    refresh = os.environ.get("FAV_CAL_REFRESH", "") == "1"
    raw = load_fills(refresh)
    res = load_resolution_times(sorted(raw["market_id"].astype(str).unique()), refresh)
    f = enrich(raw, res, load_labels())
    f = f[f["payout"].isin([0.0, 1.0])]
    summary = {"fills": len(f), "markets": int(f["market_id"].nunique()), "events": int(f["event_id"].nunique()),
               "cost_usdc": float(f["cost"].sum()), "res_ts_coverage": float(f["res_ts"].notna().mean()),
               "fee_paid_usdc": float(f["fee"].sum())}

    cal = calibration_table(f, ["q_bucket"])
    cal_h = calibration_table(f[f["q"] >= FAV], ["h_bucket"])
    cal_qh = calibration_table(f[f["q"] >= FAV], ["q_bucket", "h_bucket"])
    cal_theme = calibration_table(f[f["q"] >= FAV], ["theme"])
    cal_live = calibration_table(f[f["q"] >= FAV], ["live"])
    cal_size = calibration_table(f[f["q"] >= FAV], ["size_bucket"])
    cal_group = calibration_table(f[f["q"] >= FAV], ["group"])
    hz, hz_tot, hz_m = horizon_strategy(f)
    dec = decomposition(f)
    spec = favorite_specialists(f)
    spec_sum = specialist_summary(spec)

    # Skilled favorite PnL concentration: share from top markets
    sk = f[(f["group"] == "Skilled_Winner") & (f["q"] >= FAV)]
    by_m = sk.groupby(["market_id", "question"])["pnl"].sum().sort_values(ascending=False)
    summary["skilled_fav_pnl"] = float(by_m.sum())
    summary["skilled_fav_pnl_top10_markets_share"] = float(by_m.head(10).sum() / by_m.sum())
    losers = f[(f["q"] >= FAV) & (f["won"] < 0.5)].groupby(["market_id", "question"]).agg(
        q=("q", "mean"), cost_lost=("cost", "sum"), fills=("q", "size")).sort_values("cost_lost", ascending=False)

    for name, df in {
        "calibration_by_price": cal, "fav_by_horizon": cal_h, "fav_by_price_horizon": cal_qh,
        "fav_by_theme": cal_theme, "fav_by_live": cal_live, "fav_by_size": cal_size, "fav_by_group": cal_group,
        "horizon_strategy": hz, "horizon_strategy_total": hz_tot, "decomposition": dec,
        "specialist_summary": spec_sum,
    }.items():
        df.to_csv(OUT / f"{name}.csv", index=False)
    hz_m.to_parquet(OUT / "horizon_strategy_markets.parquet", index=False)
    spec.to_parquet(OUT / "favorite_specialists.parquet")
    by_m.head(30).to_csv(OUT / "skilled_fav_top_markets.csv")
    losers.head(30).to_csv(OUT / "fav_upsets_top.csv")
    plot(cal, hz, OUT / "favorite_calibration.png")
    summary["runtime_s"] = round(time.time() - t0, 1)
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 30)
    print(json.dumps(summary, indent=2))
    for name, df in [("CALIBRATION", cal), ("FAV x HORIZON", cal_h), ("FAV x THEME", cal_theme),
                     ("FAV x LIVE", cal_live), ("FAV x SIZE", cal_size), ("FAV x GROUP", cal_group),
                     ("HORIZON STRATEGY", hz), ("HORIZON TOTAL", hz_tot), ("DECOMPOSITION", dec),
                     ("SPECIALISTS", spec_sum)]:
        print(f"\n== {name}\n{df.round(4).to_string(index=False)}")
    print("\n== SKILLED FAV TOP MARKETS\n", by_m.head(12).round(0).to_string())
    print("\n== BIGGEST FAVORITE UPSETS\n", losers.head(12).round(3).to_string())


if __name__ == "__main__":
    main()
