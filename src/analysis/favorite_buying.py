"""Attribute settled trader PnL to buying favorites, underdogs, and selling."""

from __future__ import annotations

import os

import pandas as pd

from src.analysis.event_grouping import register_event_views, resolved_by_cutoff_sql
from src.analysis.partition import register_order_flow_views
from src.lib.debug import debug_step
from src.lib.duck import get_duck, r2_uri
from src.lib.period import logs_path


def _week_predicate(value: str) -> str:
    value = value.strip().lower()
    if value in ("", "all"):
        return "TRUE"
    if value == "train":
        return "t.week_index < 3"
    if value == "test":
        return "t.week_index = 3"
    raise RuntimeError("FAVORITE_ANALYSIS_WEEK must be all, train, or test")


def build_attribution(con, threshold: float, week: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return group/strategy totals and per-account attribution."""
    labels_uri = r2_uri("labels", "account_classification.parquet")
    predicate = _week_predicate(week)
    universe = os.environ.get("PNL_MARKET_UNIVERSE", "all").strip().lower() or "all"
    if universe not in {"all", "resolved_by_fill_cutoff"}:
        raise RuntimeError("PNL_MARKET_UNIVERSE must be all or resolved_by_fill_cutoff")
    universe_pred = (
        resolved_by_cutoff_sql("m", "(SELECT MAX(CAST(block_timestamp AS BIGINT)) FROM takers)")
        if universe == "resolved_by_fill_cutoff"
        else "TRUE"
    )
    con.execute(
        f"""
        CREATE OR REPLACE VIEW favorite_fill_pnl AS
        SELECT
          t.user_id,
          l.trader_group,
          t.amount_usdc,
          upper(t.side) AS side,
          t.price,
          CASE
            WHEN upper(t.side) = 'BUY' AND t.price >= {threshold} THEN 'favorite_buy'
            WHEN upper(t.side) = 'BUY' THEN 'underdog_buy'
            ELSE 'sell'
          END AS strategy,
          (
            CASE WHEN upper(t.side) = 'BUY' THEN t.amount_shares ELSE -t.amount_shares END
              * ot.resolved_payout
            - CASE WHEN upper(t.side) = 'BUY' THEN t.amount_usdc ELSE -t.amount_usdc END
            - COALESCE(t.fee, 0)
          ) AS settled_pnl
        FROM takers t
        JOIN outcome_tokens ot
          ON CAST(ot.token_id AS VARCHAR) = CAST(t.asset AS VARCHAR)
        JOIN markets m ON m.market_id = ot.market_id
        JOIN read_parquet('{labels_uri}') l ON l.user_id = t.user_id
        WHERE {predicate}
          AND {universe_pred}
          AND l.trader_group <> 'Excluded'
        """
    )
    grouped = con.execute(
        """
        SELECT trader_group, strategy,
          COUNT(*) AS n_fills,
          COUNT(DISTINCT user_id) AS n_users,
          SUM(amount_usdc) AS volume_usdc,
          SUM(settled_pnl) AS settled_pnl,
          SUM(GREATEST(settled_pnl, 0)) AS positive_pnl,
          SUM(LEAST(settled_pnl, 0)) AS negative_pnl
        FROM favorite_fill_pnl
        GROUP BY 1, 2
        ORDER BY 1, 2
        """
    ).df()
    accounts = con.execute(
        """
        SELECT user_id, trader_group,
          SUM(settled_pnl) AS total_pnl,
          SUM(CASE WHEN strategy = 'favorite_buy' THEN settled_pnl ELSE 0 END)
            AS favorite_buy_pnl,
          SUM(CASE WHEN strategy = 'underdog_buy' THEN settled_pnl ELSE 0 END)
            AS underdog_buy_pnl,
          SUM(CASE WHEN strategy = 'sell' THEN settled_pnl ELSE 0 END) AS sell_pnl,
          SUM(CASE WHEN strategy = 'favorite_buy' THEN amount_usdc ELSE 0 END)
            / NULLIF(SUM(CASE WHEN side = 'BUY' THEN amount_usdc ELSE 0 END), 0)
            AS favorite_share_buy_volume
        FROM favorite_fill_pnl
        GROUP BY 1, 2
        """
    ).df()
    return grouped, accounts


def summarize(grouped: pd.DataFrame, accounts: pd.DataFrame) -> pd.DataFrame:
    """One comparable row per trader group."""
    rows: list[dict] = []
    for group, g in grouped.groupby("trader_group"):
        by_strategy = g.set_index("strategy")

        def value(strategy: str, column: str) -> float:
            return float(by_strategy.loc[strategy, column]) if strategy in by_strategy.index else 0.0

        a = accounts[accounts["trader_group"] == group]
        winners = a[a["total_pnl"] > 0].copy()
        rows.append(
            {
                "trader_group": group,
                "n_accounts": int(len(a)),
                "net_pnl": float(g["settled_pnl"].sum()),
                "favorite_buy_pnl": value("favorite_buy", "settled_pnl"),
                "underdog_buy_pnl": value("underdog_buy", "settled_pnl"),
                "sell_pnl": value("sell", "settled_pnl"),
                "favorite_share_buy_volume": (
                    value("favorite_buy", "volume_usdc")
                    / max(
                        value("favorite_buy", "volume_usdc")
                        + value("underdog_buy", "volume_usdc"),
                        1e-12,
                    )
                ),
                "favorite_share_positive_fill_pnl": (
                    value("favorite_buy", "positive_pnl")
                    / max(float(g["positive_pnl"].sum()), 1e-12)
                ),
                "favorite_share_negative_fill_losses": (
                    abs(value("favorite_buy", "negative_pnl"))
                    / max(abs(float(g["negative_pnl"].sum())), 1e-12)
                ),
                "favorite_share_net_pnl": (
                    value("favorite_buy", "settled_pnl") / float(g["settled_pnl"].sum())
                    if abs(float(g["settled_pnl"].sum())) > 1e-12
                    else float("nan")
                ),
                "winner_accounts": int(len(winners)),
                "median_winner_favorite_share_net_pnl": (
                    float((winners["favorite_buy_pnl"] / winners["total_pnl"]).median())
                    if len(winners)
                    else float("nan")
                ),
            }
        )
    return pd.DataFrame(rows).sort_values("trader_group")


def main() -> None:
    with debug_step("favorite_buying") as dbg:
        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)
        week = os.environ.get("FAVORITE_ANALYSIS_WEEK", "all").strip().lower() or "all"
        thresholds = [
            float(x)
            for x in os.environ.get("FAVORITE_THRESHOLDS", "0.5,0.75,0.9").split(",")
        ]
        if any(not (0.5 <= x < 1.0) for x in thresholds):
            raise RuntimeError("FAVORITE_THRESHOLDS values must be in [0.5, 1.0)")
        dbg.log("meta", "favorite_analysis_week", week)
        dbg.log("meta", "favorite_thresholds", thresholds)
        dbg.log("meta", "market_universe", os.environ.get("PNL_MARKET_UNIVERSE", "all"))

        out_dir = logs_path("favorite_buying")
        out_dir.mkdir(parents=True, exist_ok=True)
        all_summaries: list[pd.DataFrame] = []
        for threshold in thresholds:
            grouped, accounts = build_attribution(con, threshold, week)
            summary = summarize(grouped, accounts)
            summary.insert(0, "threshold", threshold)
            all_summaries.append(summary)
            grouped.to_parquet(out_dir / f"group_strategy_week-{week}_p{threshold:g}.parquet")
            accounts.to_parquet(out_dir / f"accounts_week-{week}_p{threshold:g}.parquet")

        combined = pd.concat(all_summaries, ignore_index=True)
        combined.to_parquet(out_dir / f"summary_week-{week}.parquet")
        dbg.save_diagnostic_df(f"summary_week_{week}", combined)
        dbg.log("export", "favorite_summary", str(out_dir / f"summary_week-{week}.parquet"))


if __name__ == "__main__":
    main()
