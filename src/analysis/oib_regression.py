"""OIB and pricing-error panel; PanelOLS with two-way FE and clustered SE."""

from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from linearmodels.panel import PanelOLS

from src.analysis.event_grouping import register_event_views
from src.analysis.partition import register_order_flow_views
from src.lib.debug import debug_step
from src.lib.duck import get_duck, r2_uri


def build_panel(con) -> pd.DataFrame:
    labels_uri = r2_uri("labels", "account_classification.parquet")
    con.execute(
        f"""
        CREATE OR REPLACE VIEW account_labels AS
        SELECT user_id, trader_group FROM read_parquet('{labels_uri}');
        """
    )
    con.execute(
        """
        CREATE OR REPLACE VIEW fills_enriched AS
        SELECT
          t.user_id,
          CAST(t.asset AS VARCHAR) AS asset,
          t.amount_usdc,
          t.price,
          upper(t.side) AS side,
          CAST(t.block_timestamp AS DOUBLE) AS ts,
          m.market_id,
          CAST(m.yes_token_id AS VARCHAR) AS yes_token_id,
          COALESCE(l.trader_group, 'Excluded') AS trader_group,
          CASE WHEN CAST(t.asset AS VARCHAR) = CAST(m.yes_token_id AS VARCHAR) THEN 1 ELSE 0 END AS is_yes_asset,
          CASE
            WHEN CAST(t.asset AS VARCHAR) = CAST(m.yes_token_id AS VARCHAR) THEN t.price
            ELSE (1.0 - t.price)
          END AS yes_implied_price
        FROM takers t
        JOIN outcome_tokens ot ON CAST(ot.token_id AS VARCHAR) = CAST(t.asset AS VARCHAR)
        JOIN markets m ON m.market_id = ot.market_id
        LEFT JOIN account_labels l ON l.user_id = t.user_id;
        """
    )

    span = con.execute(
        """
        SELECT market_id, MIN(ts) AS t0, MAX(ts) AS t1
        FROM fills_enriched
        GROUP BY 1
        HAVING MAX(ts) > MIN(ts)
        """
    ).df()
    if span.empty:
        return pd.DataFrame()

    con.register("_span", span)
    con.execute(
        """
        CREATE OR REPLACE VIEW fills_binned AS
        SELECT f.*,
          LEAST(49, GREATEST(0,
            CAST(floor((f.ts - s.t0) / (s.t1 - s.t0) * 50.0) AS INT)
          )) AS bin_id
        FROM fills_enriched f
        JOIN _span s USING (market_id);
        """
    )

    con.execute(
        """
        CREATE OR REPLACE VIEW market_yes_payout AS
        SELECT m.market_id,
          MAX(CASE WHEN CAST(ot.token_id AS VARCHAR) = CAST(m.yes_token_id AS VARCHAR)
              THEN ot.resolved_payout ELSE 0 END) AS yes_payout
        FROM markets m
        JOIN outcome_tokens ot ON ot.market_id = m.market_id
        GROUP BY 1;
        """
    )

    bin_tot = con.execute(
        """
        SELECT f.market_id, f.bin_id,
          SUM(f.amount_usdc) AS vol,
          SUM(f.yes_implied_price * f.amount_usdc) / NULLIF(SUM(f.amount_usdc), 0) AS mid_yes
        FROM fills_binned f
        GROUP BY 1, 2;
        """
    ).df()
    pay = con.execute("SELECT * FROM market_yes_payout").df()
    mid = bin_tot.merge(pay, on="market_id", how="inner")
    mid["abs_err"] = (mid["mid_yes"] - mid["yes_payout"]).abs()

    grp = con.execute(
        """
        WITH bin_vol AS (
          SELECT market_id, bin_id, SUM(amount_usdc) AS vol_bin
          FROM fills_binned GROUP BY 1, 2
        )
        SELECT f.market_id, f.bin_id, f.trader_group,
          SUM(CASE WHEN f.is_yes_asset = 1 AND f.side = 'BUY' THEN f.amount_usdc
                   WHEN f.is_yes_asset = 1 AND f.side = 'SELL' THEN -f.amount_usdc
                   ELSE 0 END) / NULLIF(MAX(b.vol_bin), 0) AS oib_yes,
          SUM(f.amount_usdc) / NULLIF(MAX(b.vol_bin), 0) AS vol_share
        FROM fills_binned f
        JOIN bin_vol b USING (market_id, bin_id)
        GROUP BY f.market_id, f.bin_id, f.trader_group;
        """
    ).df()

    gcols = ["Skilled_Winner", "Lucky_Winner", "Unlucky_Loser", "Unskilled_Loser", "Excluded"]
    pivot_oib = grp.pivot_table(
        index=["market_id", "bin_id"], columns="trader_group", values="oib_yes", aggfunc="sum"
    ).reindex(columns=gcols, fill_value=0.0)

    pivot_vol = grp.pivot_table(
        index=["market_id", "bin_id"], columns="trader_group", values="vol_share", aggfunc="sum"
    ).reindex(columns=gcols, fill_value=0.0)

    df = mid.merge(pivot_oib, on=["market_id", "bin_id"], how="left").merge(
        pivot_vol.rename(columns={c: f"volshare_{c}" for c in gcols}),
        on=["market_id", "bin_id"],
        how="left",
    )
    df = df.fillna(0.0)

    df = df.sort_values(["market_id", "bin_id"])
    df["abs_err_next"] = df.groupby("market_id")["abs_err"].shift(-1)
    df["d_err"] = df["abs_err_next"] - df["abs_err"]
    df = df[df["d_err"].notna()].copy()

    df.rename(
        columns={
            "Skilled_Winner": "OIB_skilled",
            "Lucky_Winner": "OIB_lucky",
            "Unlucky_Loser": "OIB_unlucky",
            "Unskilled_Loser": "OIB_unskilled",
        },
        inplace=True,
    )
    vs = "volshare_Skilled_Winner"
    if vs in df.columns:
        df.rename(columns={vs: "volume_share_skilled"}, inplace=True)
    else:
        df["volume_share_skilled"] = 0.0

    for c in ["OIB_skilled", "OIB_lucky", "OIB_unlucky", "OIB_unskilled", "volume_share_skilled"]:
        if c not in df.columns:
            df[c] = 0.0

    return df[
        [
            "market_id",
            "bin_id",
            "d_err",
            "OIB_skilled",
            "OIB_lucky",
            "OIB_unlucky",
            "OIB_unskilled",
            "volume_share_skilled",
            "vol",
        ]
    ]


def run_regressions(panel: pd.DataFrame, dbg) -> None:
    if panel.empty or len(panel) < 50:
        dbg.assertion("panel_size", "red", "Panel too small for regression")
        return

    p = panel.copy()
    p["bin_id"] = p["bin_id"].astype(int)
    y_raw = p["d_err"].astype(float)
    X_raw = p[
        ["OIB_skilled", "OIB_lucky", "OIB_unlucky", "OIB_unskilled", "volume_share_skilled"]
    ].astype(float)
    mask = np.isfinite(y_raw) & np.isfinite(X_raw).all(axis=1)
    p_fit = p.loc[mask].set_index(["market_id", "bin_id"])
    y = p_fit["d_err"].astype(float)
    X = p_fit[
        ["OIB_skilled", "OIB_lucky", "OIB_unlucky", "OIB_unskilled", "volume_share_skilled"]
    ].astype(float)

    specs = [
        ("full_FE", True, True),
        ("no_bin_FE", True, False),
        ("no_market_FE", False, True),
    ]
    for name, ent, time in specs:
        try:
            mod = PanelOLS(y, X, entity_effects=ent, time_effects=time)
            res = mod.fit(cov_type="clustered", cluster_entity=True)
            tbl = pd.DataFrame(
                {
                    "param": res.params,
                    "stderr": res.std_errors,
                    "tstat": res.tstats,
                    "pvalue": res.pvalues,
                }
            )
            dbg.save_diagnostic_df(f"regression_{name}", tbl.reset_index().rename(columns={"index": "term"}))
            dbg.log("regression", f"{name}_r2", float(res.rsquared))
            dbg.log("regression", f"{name}_nobs", int(res.nobs))
        except Exception as e:
            dbg.log("regression", f"fit_failed_{name}", repr(e))

    plot_dir = Path("logs/oib_regression")
    plot_dir.mkdir(parents=True, exist_ok=True)
    scatter_base = p.loc[mask]
    for col in ["OIB_skilled", "OIB_lucky", "OIB_unlucky", "OIB_unskilled"]:
        if col not in scatter_base.columns:
            continue
        fig, ax = plt.subplots(figsize=(5, 4))
        ax.scatter(scatter_base[col], scatter_base["d_err"], s=3, alpha=0.35)
        ax.set_xlabel(col)
        ax.set_ylabel("d_err")
        ax.axhline(0, color="gray", lw=0.5)
        try:
            from statsmodels.nonparametric.smoothers_lowess import lowess

            sub = scatter_base[[col, "d_err"]].dropna()
            if len(sub) > 20:
                z = lowess(sub["d_err"], sub[col], frac=0.35)
                ax.plot(z[:, 0], z[:, 1], color="red", lw=2)
        except Exception:
            pass
        pth = plot_dir / f"scatter_{col}.png"
        fig.savefig(pth, dpi=120, bbox_inches="tight")
        plt.close(fig)
        dbg.log("plot", "saved", str(pth))


def main() -> None:
    with debug_step("oib_regression") as dbg:
        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)
        panel = build_panel(con)
        dbg.log("ingest", "panel_rows", len(panel))
        if len(panel):
            dbg.save_diagnostic_df("panel_head", panel.head(5000))
            cov = panel.groupby("market_id").size()
            dbg.log("quality", "avg_bins_per_market", float(cov.mean()))
            dbg.log("quality", "markets_in_panel", int(panel["market_id"].nunique()))
            dbg.log("quality", "d_err_mean", float(panel["d_err"].mean()))
        run_regressions(panel, dbg)


if __name__ == "__main__":
    main()
