"""Stage 1.6: inspect top winners, concentrated whales, and Skilled trade histories."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from src.analysis.event_grouping import register_event_views, resolved_by_cutoff_sql
from src.analysis.partition import register_order_flow_views
from src.lib.debug import debug_step
from src.lib.duck import get_duck
from src.lib.period import logs_path


def main() -> None:
    label_path = logs_path("coverage_audit", "account_classification_primary.parquet")
    concentration_path = logs_path("composition_audit", "skilled_event_concentration.parquet")
    if not label_path.exists():
        raise RuntimeError("Run coverage_audit first")

    with debug_step("trader_sanity") as dbg:
        labels = pd.read_parquet(label_path)
        top20 = labels.nlargest(20, "total_pnl").copy()
        dbg.save_diagnostic_df("top20_winners_with_labels", top20)
        top_group = str(top20.iloc[0]["trader_group"]) if len(top20) else "none"
        dbg.log("whale", "largest_winner_group", top_group)
        dbg.log(
            "whale",
            "top20_not_skilled",
            int((top20["trader_group"] != "Skilled_Winner").sum()),
        )

        if concentration_path.exists():
            concentration = pd.read_parquet(concentration_path)
            top20 = top20.merge(concentration, on="user_id", how="left")
            dbg.save_diagnostic_df("top20_winners_with_concentration", top20)

        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)
        cutoff = int(con.execute("SELECT MAX(block_timestamp) FROM takers").fetchone()[0])
        skilled5 = labels[labels["trader_group"] == "Skilled_Winner"].nlargest(
            5, "total_pnl"
        )["user_id"].tolist()
        con.register("_skilled5", pd.DataFrame({"user_id": skilled5}))
        histories = con.execute(
            f"""
            SELECT t.user_id, t.block_timestamp, t.transaction_hash, t.asset,
              t.side, t.price, t.amount_usdc, t.amount_shares,
              m.market_id, m.event_id, m.question, e.category, ot.resolved_payout,
              (CASE WHEN upper(t.side)='BUY' THEN t.amount_shares ELSE -t.amount_shares END)
                * ot.resolved_payout
              - (CASE WHEN upper(t.side)='BUY' THEN t.amount_usdc ELSE -t.amount_usdc END)
              - COALESCE(t.fee,0) AS fill_pnl
            FROM takers t
            JOIN _skilled5 s USING (user_id)
            JOIN outcome_tokens ot
              ON CAST(ot.token_id AS VARCHAR)=CAST(t.asset AS VARCHAR)
            JOIN markets m ON m.market_id=ot.market_id
            LEFT JOIN events e ON e.event_id=m.event_id
            WHERE {resolved_by_cutoff_sql("m", str(cutoff))}
            ORDER BY t.user_id, t.block_timestamp
            """
        ).df()
        dbg.save_diagnostic_df("top5_skilled_trade_history", histories)
        histories["is_buy"] = histories["side"].str.upper().eq("BUY")
        histories["is_heavy_favorite_buy"] = histories["is_buy"] & histories["price"].ge(0.75)
        profiles = (
            histories.groupby("user_id")
            .agg(
                fills=("asset", "size"),
                events=("event_id", "nunique"),
                markets=("market_id", "nunique"),
                categories=("category", "nunique"),
                volume=("amount_usdc", "sum"),
                pnl=("fill_pnl", "sum"),
                buy_share=("is_buy", "mean"),
                heavy_favorite_fill_share=("is_heavy_favorite_buy", "mean"),
            )
            .reset_index()
            .sort_values("pnl", ascending=False)
        )
        dbg.save_diagnostic_df("top5_skilled_profiles", profiles)
        dbg.assertion(
            "concentrated_whale_classification",
            "yellow" if top_group != "Skilled_Winner" else "green",
            f"Largest restricted-universe winner is classified {top_group}; low-K "
            "concentrated winners may be Lucky/Excluded by construction.",
        )
        dbg.assertion(
            "manual_trade_review_required",
            "yellow",
            "Top-five Skilled histories exported for human review; wallet intent "
            "(skill/arbitrage/misclassified making) is not safely inferable automatically.",
        )
        con.unregister("_skilled5")


if __name__ == "__main__":
    main()
