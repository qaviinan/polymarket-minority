"""OIB and pricing-error panel; PanelOLS with two-way FE and clustered SE."""

from __future__ import annotations

import os
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from linearmodels.panel import PanelOLS

from src.analysis.event_grouping import register_event_views, resolved_by_cutoff_sql
from src.analysis.partition import register_order_flow_views
from src.lib.debug import debug_step
from src.lib.duck import get_duck, r2_uri
from src.lib.period import logs_path


def _oib_eval_week_predicate_sql() -> str:
    """Restrict fills to train weeks (0–2), test week (3), or all takers in the analysis window."""
    w = os.environ.get("OIB_EVAL_WEEK", "all").strip().lower()
    if w in ("", "all"):
        return "TRUE"
    if w == "train":
        return "t.week_index < 3"
    if w == "test":
        return "t.week_index = 3"
    raise RuntimeError(f"Invalid OIB_EVAL_WEEK={w!r}; use all, train, or test")


def build_panel(con) -> pd.DataFrame:
    labels_uri = r2_uri("labels", "account_classification.parquet")
    con.execute(
        f"""
        CREATE OR REPLACE VIEW account_labels AS
        SELECT user_id, trader_group, label_scope FROM read_parquet('{labels_uri}');
        """
    )
    eval_week = os.environ.get("OIB_EVAL_WEEK", "all").strip().lower() or "all"
    label_scopes = {
        str(r[0]) for r in con.execute("SELECT DISTINCT label_scope FROM account_labels").fetchall()
    }
    if eval_week == "test" and label_scopes != {"train_weeks_0_2"}:
        raise RuntimeError(
            "OIB_EVAL_WEEK=test requires labels built only on weeks 0–2, but found "
            f"{sorted(label_scopes)}. Rerun sign_randomization then classification with "
            "MINORITY_LABEL_SPLIT=train_3w_test_1w."
        )
    week_pred = _oib_eval_week_predicate_sql()
    universe = os.environ.get("OIB_MARKET_UNIVERSE", "all").strip().lower() or "all"
    if universe not in {"all", "resolved_by_fill_cutoff"}:
        raise RuntimeError("OIB_MARKET_UNIVERSE must be all or resolved_by_fill_cutoff")
    universe_pred = (
        resolved_by_cutoff_sql("m", "(SELECT MAX(CAST(block_timestamp AS BIGINT)) FROM takers)")
        if universe == "resolved_by_fill_cutoff"
        else "TRUE"
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW fills_all_enriched AS
        SELECT
          t.user_id,
          CAST(t.asset AS VARCHAR) AS asset,
          t.amount_usdc,
          t.amount_shares,
          t.price,
          upper(t.side) AS side,
          CAST(t.block_timestamp AS DOUBLE) AS ts,
          t.week_index,
          m.market_id,
          CAST(m.yes_token_id AS VARCHAR) AS yes_token_id,
          COALESCE(l.trader_group, 'Excluded') AS trader_group,
          CASE WHEN CAST(t.asset AS VARCHAR) = CAST(m.yes_token_id AS VARCHAR) THEN 1 ELSE 0 END AS is_yes_asset,
          CASE
            WHEN CAST(t.asset AS VARCHAR) = CAST(m.yes_token_id AS VARCHAR) THEN t.price
            ELSE (1.0 - t.price)
          END AS yes_implied_price,
          CASE
            WHEN CAST(t.asset AS VARCHAR) = CAST(m.yes_token_id AS VARCHAR)
              THEN CASE WHEN upper(t.side) = 'BUY' THEN t.amount_usdc ELSE -t.amount_usdc END
            ELSE CASE WHEN upper(t.side) = 'BUY' THEN -t.amount_usdc ELSE t.amount_usdc END
          END AS yes_direction_usdc,
          CASE WHEN oty.resolved_payout > 0.5 THEN 1.0 ELSE -1.0 END AS outcome_sign,
          ({week_pred}) AS is_eval
        FROM takers t
        JOIN outcome_tokens ot ON CAST(ot.token_id AS VARCHAR) = CAST(t.asset AS VARCHAR)
        JOIN markets m ON m.market_id = ot.market_id
        JOIN outcome_tokens oty
          ON CAST(oty.token_id AS VARCHAR) = CAST(m.yes_token_id AS VARCHAR)
        LEFT JOIN account_labels l ON l.user_id = t.user_id
        WHERE {universe_pred}
        ;
        """
    )

    span = con.execute(
        """
        SELECT market_id, MIN(ts) AS t0, MAX(ts) AS t1
        FROM fills_all_enriched
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
        FROM fills_all_enriched f
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
          SUM(f.yes_implied_price * f.amount_usdc) / NULLIF(SUM(f.amount_usdc), 0) AS mid_yes,
          MAX(f.outcome_sign) AS outcome_sign
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
          FROM fills_binned WHERE is_eval GROUP BY 1, 2
        )
        SELECT f.market_id, f.bin_id, f.trader_group,
          SUM(f.yes_direction_usdc) / NULLIF(MAX(b.vol_bin), 0) AS oib_yes,
          SUM(f.yes_direction_usdc * f.outcome_sign) / NULLIF(MAX(b.vol_bin), 0) AS oib_aligned,
          SUM(f.amount_usdc) / NULLIF(MAX(b.vol_bin), 0) AS vol_share
        FROM fills_binned f
        JOIN bin_vol b USING (market_id, bin_id)
        WHERE f.is_eval
        GROUP BY f.market_id, f.bin_id, f.trader_group;
        """
    ).df()

    gcols = ["Skilled_Winner", "Lucky_Winner", "Unlucky_Loser", "Unskilled_Loser", "Excluded"]
    pivot_oib = grp.pivot_table(
        index=["market_id", "bin_id"], columns="trader_group", values="oib_yes", aggfunc="sum"
    ).reindex(columns=gcols, fill_value=0.0)
    pivot_aligned = grp.pivot_table(
        index=["market_id", "bin_id"], columns="trader_group", values="oib_aligned", aggfunc="sum"
    ).reindex(columns=gcols, fill_value=0.0)

    pivot_vol = grp.pivot_table(
        index=["market_id", "bin_id"], columns="trader_group", values="vol_share", aggfunc="sum"
    ).reindex(columns=gcols, fill_value=0.0)

    df = mid.merge(pivot_oib, on=["market_id", "bin_id"], how="inner").merge(
        pivot_aligned.rename(columns={c: f"aligned_{c}" for c in gcols}),
        on=["market_id", "bin_id"],
        how="left",
    ).merge(
        pivot_vol.rename(columns={c: f"volshare_{c}" for c in gcols}),
        on=["market_id", "bin_id"],
        how="left",
    )
    df = df.fillna(0.0)

    # Match fixed lifecycle-bin horizons rather than shifting to the next
    # observed trade bin (which can skip arbitrary amounts of time).
    for horizon in (1, 5, 10):
        future = mid[["market_id", "bin_id", "mid_yes", "abs_err"]].copy()
        future["bin_id"] = future["bin_id"] - horizon
        future = future.rename(
            columns={"mid_yes": f"mid_yes_h{horizon}", "abs_err": f"abs_err_h{horizon}"}
        )
        df = df.merge(future, on=["market_id", "bin_id"], how="left")
        df[f"d_price_h{horizon}"] = df[f"mid_yes_h{horizon}"] - df["mid_yes"]
        df[f"d_err_h{horizon}"] = df[f"abs_err_h{horizon}"] - df["abs_err"]
        df[f"aligned_price_move_h{horizon}"] = df["outcome_sign"] * df[f"d_price_h{horizon}"]
    df["terminal_gap"] = ((df["outcome_sign"] + 1.0) / 2.0) - df["mid_yes"]
    df["d_err"] = df["d_err_h1"]

    df.rename(
        columns={
            "Skilled_Winner": "OIB_skilled",
            "Lucky_Winner": "OIB_lucky",
            "Unlucky_Loser": "OIB_unlucky",
            "Unskilled_Loser": "OIB_unskilled",
            "aligned_Skilled_Winner": "AOIB_skilled",
            "aligned_Lucky_Winner": "AOIB_lucky",
            "aligned_Unlucky_Loser": "AOIB_unlucky",
            "aligned_Unskilled_Loser": "AOIB_unskilled",
        },
        inplace=True,
    )
    vs = "volshare_Skilled_Winner"
    if vs in df.columns:
        df.rename(columns={vs: "volume_share_skilled"}, inplace=True)
    else:
        df["volume_share_skilled"] = 0.0

    for c in [
        "OIB_skilled", "OIB_lucky", "OIB_unlucky", "OIB_unskilled",
        "AOIB_skilled", "AOIB_lucky", "AOIB_unlucky", "AOIB_unskilled",
        "volume_share_skilled",
    ]:
        if c not in df.columns:
            df[c] = 0.0

    return df[
        [
            "market_id",
            "bin_id",
            "d_err",
            "d_err_h1",
            "d_err_h5",
            "d_err_h10",
            "d_price_h1",
            "d_price_h5",
            "d_price_h10",
            "aligned_price_move_h1",
            "aligned_price_move_h5",
            "aligned_price_move_h10",
            "terminal_gap",
            "mid_yes",
            "yes_payout",
            "OIB_skilled",
            "OIB_lucky",
            "OIB_unlucky",
            "OIB_unskilled",
            "AOIB_skilled",
            "AOIB_lucky",
            "AOIB_unlucky",
            "AOIB_unskilled",
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
    raw_oib = ["OIB_skilled", "OIB_lucky", "OIB_unlucky", "OIB_unskilled"]
    aligned_oib = ["AOIB_skilled", "AOIB_lucky", "AOIB_unlucky", "AOIB_unskilled"]
    models = [
        # Paper Task 6.2: signed YES flow predicts subsequent signed price changes.
        ("price_h1_raw_oib", "d_price_h1", raw_oib),
        ("price_h5_raw_oib", "d_price_h5", raw_oib),
        ("price_h10_raw_oib", "d_price_h10", raw_oib),
        ("terminal_gap_raw_oib", "terminal_gap", raw_oib),
        ("resolution_raw_oib", "yes_payout", [*raw_oib, "mid_yes"]),
        # Outcome alignment is required before pooling absolute-error changes
        # across YES- and NO-resolved markets.
        ("accuracy_h1_aligned_oib", "d_err_h1", aligned_oib),
        ("accuracy_h5_aligned_oib", "d_err_h5", aligned_oib),
        ("accuracy_h10_aligned_oib", "d_err_h10", aligned_oib),
        # Paper Task 6.3: informed-minority volume share and lifecycle accuracy.
        ("accuracy_h1_skilled_share", "d_err_h1", ["volume_share_skilled"]),
        ("accuracy_h5_skilled_share", "d_err_h5", ["volume_share_skilled"]),
        ("accuracy_h10_skilled_share", "d_err_h10", ["volume_share_skilled"]),
    ]
    fe_specs = [
        ("full_FE", True, True),
        ("no_bin_FE", True, False),
        ("no_market_FE", False, True),
    ]
    for model_name, outcome, regressors in models:
        y_raw = p[outcome].astype(float)
        X_raw = p[regressors].astype(float)
        mask = np.isfinite(y_raw) & np.isfinite(X_raw).all(axis=1)
        p_fit = p.loc[mask].set_index(["market_id", "bin_id"])
        y = p_fit[outcome].astype(float)
        X = p_fit[regressors].astype(float)
        if len(y) < 50:
            dbg.log("regression", f"fit_failed_{model_name}", "fewer than 50 observations")
            continue
        model_fe_specs = (
            [("bin_FE", False, True), ("pooled", False, False)]
            if model_name == "resolution_raw_oib"
            else fe_specs
        )
        for fe_name, ent, time in model_fe_specs:
            name = f"{model_name}_{fe_name}"
            try:
                mod = PanelOLS(
                    y,
                    X,
                    entity_effects=ent,
                    time_effects=time,
                    drop_absorbed=True,
                )
                res = mod.fit(cov_type="clustered", cluster_entity=True)
                tbl = pd.DataFrame(
                    {
                        "param": res.params,
                        "stderr": res.std_errors,
                        "tstat": res.tstats,
                        "pvalue": res.pvalues,
                    }
                )
                dbg.save_diagnostic_df(
                    f"regression_{name}",
                    tbl.reset_index().rename(columns={"index": "term"}),
                )
                dbg.log("regression", f"{name}_r2", float(res.rsquared))
                dbg.log("regression", f"{name}_nobs", int(res.nobs))
            except Exception as e:
                dbg.log("regression", f"fit_failed_{name}", repr(e))

    plot_dir = logs_path("oib_regression")
    plot_dir.mkdir(parents=True, exist_ok=True)
    scatter_base = p[np.isfinite(p["d_price_h1"])].copy()
    for col in raw_oib:
        if col not in scatter_base.columns:
            continue
        fig, ax = plt.subplots(figsize=(5, 4))
        ax.scatter(scatter_base[col], scatter_base["d_price_h1"], s=3, alpha=0.35)
        ax.set_xlabel(col)
        ax.set_ylabel("d_price_h1")
        ax.axhline(0, color="gray", lw=0.5)
        try:
            from statsmodels.nonparametric.smoothers_lowess import lowess

            sub = scatter_base[[col, "d_price_h1"]].dropna()
            if len(sub) > 20:
                z = lowess(sub["d_price_h1"], sub[col], frac=0.35)
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
        dbg.log("meta", "oib_eval_week", os.environ.get("OIB_EVAL_WEEK", "all").strip() or "all")
        dbg.log("meta", "oib_market_universe", os.environ.get("OIB_MARKET_UNIVERSE", "all").strip() or "all")
        register_order_flow_views(con)
        register_event_views(con)
        panel = build_panel(con)
        dbg.log("ingest", "panel_rows", len(panel))
        if len(panel):
            dbg.save_diagnostic_df("panel_head", panel.head(5000))
            cov = panel.groupby("market_id").size()
            dbg.log("quality", "avg_bins_per_market", float(cov.mean()))
            dbg.log("quality", "markets_in_panel", int(panel["market_id"].nunique()))
            dbg.log("quality", "d_err_h1_mean", float(panel["d_err_h1"].mean()))
            direction = con.execute(
                """
                SELECT
                  SUM(CASE WHEN is_yes_asset = 0 THEN amount_usdc ELSE 0 END)
                    / NULLIF(SUM(amount_usdc), 0) AS no_token_volume_share,
                  SUM(CASE WHEN is_eval AND trader_group = 'Skilled_Winner'
                           THEN amount_usdc ELSE 0 END)
                    / NULLIF(SUM(CASE WHEN is_eval THEN amount_usdc ELSE 0 END), 0)
                    AS skilled_eval_volume_share
                FROM fills_binned
                """
            ).fetchone()
            dbg.log("quality", "no_token_volume_share", float(direction[0] or 0))
            dbg.log("quality", "skilled_eval_volume_share", float(direction[1] or 0))
            coverage = con.execute(
                """
                WITH active AS (
                  SELECT DISTINCT market_id,
                    TRY_CAST(end_date AS TIMESTAMPTZ) AS end_ts
                  FROM fills_all_enriched f
                  JOIN markets m USING (market_id)
                  WHERE is_eval
                ),
                bounds AS (
                  SELECT to_timestamp(MAX(ts)) AS data_max FROM fills_all_enriched
                )
                SELECT COUNT(*) AS n,
                  SUM(CASE WHEN end_ts > data_max THEN 1 ELSE 0 END) AS after_data
                FROM active CROSS JOIN bounds
                """
            ).fetchone()
            ending_after_share = float((coverage[1] or 0) / max(coverage[0], 1))
            dbg.log("quality", "markets_ending_after_data_share", ending_after_share)
            dbg.assertion(
                "execution_price_proxy",
                "yellow",
                "mid_yes is transaction-price VWAP, not the paper's quote/midpoint history; "
                "directional OIB can exhibit bid-ask bounce.",
            )
            if ending_after_share > 0.25:
                dbg.assertion(
                    "price_horizon_coverage",
                    "yellow",
                    f"{ending_after_share:.1%} of evaluation-active markets end after the "
                    "last ingested fill, so terminal convergence is not observed.",
                )
        else:
            n_fe = con.execute(
                "SELECT COUNT(*) FROM fills_all_enriched WHERE is_eval"
            ).fetchone()[0]
            dbg.log("ingest", "fills_enriched_rows", int(n_fe))
            wk = con.execute(
                "SELECT week_index, COUNT(*) AS c FROM takers GROUP BY 1 ORDER BY 1"
            ).df()
            dbg.save_diagnostic_df("takers_week_index_at_oib", wk)
            ev = (os.environ.get("OIB_EVAL_WEEK", "all").strip().lower() or "all")
            if ev in ("train", "test") and n_fe == 0:
                dbg.assertion(
                    "oib_eval_week_empty",
                    "yellow",
                    "fills_enriched is empty: with OIB_EVAL_WEEK=train|test you need "
                    "ANALYSIS_BLOCK_TS_MIN/MAX (or ANALYSIS_USE_LAST_N_DAYS) so week_index "
                    "runs 0–3 from the anchor; without bounds every row has week_index=0, "
                    "so week_index=3 matches nothing.",
                )
            elif ev in ("train", "test") and n_fe > 0:
                dbg.assertion(
                    "oib_panel_empty_but_fills",
                    "yellow",
                    "fills_enriched has rows but panel is empty: often every market in this "
                    "week slice has MAX(ts)=MIN(ts), so lifecycle bins drop out (see span HAVING "
                    "MAX(ts) > MIN(ts) in build_panel). Try OIB_EVAL_WEEK=all/train or widen window.",
                )
        run_regressions(panel, dbg)


if __name__ == "__main__":
    main()
