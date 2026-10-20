"""Stage 1.1: quantify truncation and compare unrestricted vs cutoff-valid labels."""

from __future__ import annotations

import os

import numpy as np
import pandas as pd

from src.analysis.classification import build_classification_table
from src.analysis.event_grouping import register_event_views
from src.analysis.partition import register_order_flow_views
from src.analysis.pnl import register_pnl_views
from src.analysis.sign_randomization import load_event_pnl_matrix, run_sign_randomization_core
from src.lib.debug import debug_step
from src.lib.duck import get_duck, load_env, r2_uri
from src.lib.period import logs_path
from src.lib.provenance import build_manifest, save_manifest


def _classify_universe(con, universe: str, *, n_iter: int, seed: int, exact_max: int):
    os.environ["PNL_MARKET_UNIVERSE"] = universe
    register_pnl_views(con, taker_view="takers")
    ev = load_event_pnl_matrix(con)
    acct = con.execute(
        "SELECT user_id, total_pnl, n_trades, n_events FROM pnl_account"
    ).df()
    sig, peak, wall = run_sign_randomization_core(
        ev,
        acct,
        np.random.default_rng(seed),
        n_iter,
        exact_max_events=exact_max,
        tqdm_desc=f"coverage_{universe}",
    )
    labels = build_classification_table(acct, sig)
    labels["label_scope"] = "all_window"
    labels["market_universe"] = universe
    return labels, sig, peak, wall


def main() -> None:
    load_env()
    n_iter = int(os.environ.get("SIGN_RANDOMIZATION_ITERATIONS", "10000"))
    seed = int(os.environ.get("SIGN_RANDOMIZATION_SEED", "42"))
    exact_max = int(os.environ.get("SIGN_RANDOMIZATION_EXACT_MAX_EVENTS", "12"))
    prior_universe = os.environ.get("PNL_MARKET_UNIVERSE")

    with debug_step("coverage_audit") as dbg:
        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)
        cutoff = int(con.execute("SELECT MAX(block_timestamp) FROM takers").fetchone()[0])
        dbg.log("coverage", "fill_cutoff_ts", cutoff)

        coverage = con.execute(
            f"""
            WITH joined AS (
              SELECT t.*, m.market_id, to_timestamp(mr.scheduled_end_ts) AS end_ts,
                (CASE WHEN upper(t.side)='BUY' THEN t.amount_shares ELSE -t.amount_shares END)
                  * ot.resolved_payout
                - (CASE WHEN upper(t.side)='BUY' THEN t.amount_usdc ELSE -t.amount_usdc END)
                - COALESCE(t.fee, 0) AS fill_pnl
              FROM takers t
              JOIN outcome_tokens ot
                ON CAST(ot.token_id AS VARCHAR)=CAST(t.asset AS VARCHAR)
              JOIN markets m ON m.market_id=ot.market_id
              LEFT JOIN market_resolution mr ON mr.market_id=m.market_id
            )
            SELECT
              COUNT(DISTINCT market_id) AS markets,
              COUNT(DISTINCT CASE WHEN epoch(end_ts) > {cutoff} THEN market_id END)
                AS markets_after_cutoff,
              SUM(amount_usdc) AS volume,
              SUM(CASE WHEN epoch(end_ts) > {cutoff} THEN amount_usdc ELSE 0 END)
                AS volume_after_cutoff,
              SUM(fill_pnl) AS pnl,
              SUM(CASE WHEN epoch(end_ts) > {cutoff} THEN fill_pnl ELSE 0 END)
                AS pnl_after_cutoff
            FROM joined
            """
        ).df()
        coverage["market_share_after_cutoff"] = (
            coverage["markets_after_cutoff"] / coverage["markets"]
        )
        coverage["volume_share_after_cutoff"] = (
            coverage["volume_after_cutoff"] / coverage["volume"]
        )
        coverage["abs_pnl_share_after_cutoff"] = (
            coverage["pnl_after_cutoff"].abs() / coverage["pnl"].abs().clip(lower=1e-12)
        )
        dbg.save_diagnostic_df("cutoff_exposure", coverage)
        dbg.log("coverage", "market_share_after_cutoff", float(coverage["market_share_after_cutoff"].iloc[0]))
        dbg.log("coverage", "volume_share_after_cutoff", float(coverage["volume_share_after_cutoff"].iloc[0]))

        results: dict[str, pd.DataFrame] = {}
        signs: dict[str, pd.DataFrame] = {}
        for i, universe in enumerate(("all", "resolved_by_fill_cutoff")):
            labels, sig, peak, wall = _classify_universe(
                con, universe, n_iter=n_iter, seed=seed + i * 1_000_003, exact_max=exact_max
            )
            results[universe] = labels
            signs[universe] = sig
            counts = labels["trader_group"].value_counts().rename_axis("trader_group")
            dbg.save_diagnostic_df(
                f"group_counts_{universe}", counts.reset_index(name="n")
            )
            dbg.log(universe, "n_accounts", int(len(labels)))
            dbg.log(universe, "peak_sim_bytes", int(peak))
            dbg.log(universe, "simulation_seconds", float(wall))

        comparison = pd.concat(
            [
                frame["trader_group"].value_counts().rename(universe)
                for universe, frame in results.items()
            ],
            axis=1,
        ).fillna(0).astype(int).reset_index(names="trader_group")
        dbg.save_diagnostic_df("classification_sensitivity", comparison)

        all_labels = results["all"][["user_id", "trader_group"]].rename(
            columns={"trader_group": "group_all"}
        )
        restricted_labels = results["resolved_by_fill_cutoff"].copy()
        transition = all_labels.merge(
            restricted_labels[["user_id", "trader_group"]].rename(
                columns={"trader_group": "group_restricted"}
            ),
            on="user_id",
            how="outer",
        )
        dbg.save_diagnostic_df(
            "label_transition",
            pd.crosstab(
                transition["group_all"].fillna("Absent"),
                transition["group_restricted"].fillna("Absent"),
            ).reset_index(),
        )

        out_dir = logs_path("coverage_audit")
        out_dir.mkdir(parents=True, exist_ok=True)
        restricted_labels.to_parquet(out_dir / "account_classification_primary.parquet", index=False)
        signs["resolved_by_fill_cutoff"].to_parquet(
            out_dir / "sign_randomization_primary.parquet", index=False
        )
        con.register("_stage1_labels", restricted_labels)
        labels_uri = r2_uri("labels", "account_classification_stage1_restricted.parquet")
        con.execute(
            f"COPY _stage1_labels TO '{labels_uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)"
        )
        con.unregister("_stage1_labels")
        dbg.log("export", "primary_labels_r2", labels_uri)

        manifest = build_manifest(
            con,
            step="coverage_audit",
            source_view="takers",
            label_scope="all_window",
            extra={
                "primary_market_universe": "resolved_by_fill_cutoff",
                "fill_cutoff_ts": cutoff,
                "n_iterations": n_iter,
                "exact_max_events": exact_max,
            },
        )
        save_manifest(out_dir / "manifest.json", manifest)
        dbg.log("meta", "manifest_id", manifest["manifest_id"])

        material = float(coverage["volume_share_after_cutoff"].iloc[0]) > 0.05
        dbg.assertion(
            "primary_universe_restricted",
            "green",
            "Primary Stage 1 labels exclude markets resolving after the fill cutoff",
        )
        if material:
            dbg.assertion(
                "unrestricted_headline_invalid",
                "red",
                f"{float(coverage['volume_share_after_cutoff'].iloc[0]):.1%} of volume is "
                "attached to post-cutoff markets; unrestricted labels are sensitivity only",
            )

    if prior_universe is None:
        os.environ.pop("PNL_MARKET_UNIVERSE", None)
    else:
        os.environ["PNL_MARKET_UNIVERSE"] = prior_universe


if __name__ == "__main__":
    main()
