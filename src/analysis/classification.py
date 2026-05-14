"""Assign Skilled / Lucky / Unlucky / Unskilled from sign-randomization p-values."""

from __future__ import annotations

import pandas as pd

from src.lib.debug import debug_step
from src.lib.duck import get_duck, r2_uri


def assign_group(row: pd.Series) -> str:
    if row.get("n_trades", 0) < 10 or pd.isna(row.get("n_events_sim")) or row.get("n_events_sim", 0) < 2:
        return "Excluded"
    r = row["realized_pnl"]
    if r > 0:
        return "Skilled_Winner" if row["p_skill"] < 0.05 else "Lucky_Winner"
    if r < 0:
        return "Unskilled_Loser" if row["p_loss"] < 0.05 else "Unlucky_Loser"
    return "Excluded"


def main() -> None:
    with debug_step("classification") as dbg:
        con = get_duck()
        acct_uri = r2_uri("derived", "pnl_account.parquet")
        sig_uri = r2_uri("derived", "sign_randomization_summary.parquet")
        acct = con.execute(f"SELECT * FROM read_parquet('{acct_uri}')").df()
        sig = con.execute(f"SELECT * FROM read_parquet('{sig_uri}')").df()
        dbg.log("ingest", "n_accounts", len(acct))
        dbg.log("ingest", "n_sign_randomization_rows", len(sig))

        sig2 = sig[
            ["user_id", "n_events_sim", "null_mean", "null_std", "p_skill", "p_loss", "bad_null_mean"]
        ].copy()
        full = acct.merge(sig2, on="user_id", how="left")
        full["realized_pnl"] = full["total_pnl"]

        full["n_events_sim"] = full["n_events_sim"].fillna(0)
        full["p_skill"] = full["p_skill"].fillna(1.0)
        full["p_loss"] = full["p_loss"].fillna(1.0)

        full["trader_group"] = full.apply(assign_group, axis=1)
        vc = full["trader_group"].value_counts()
        gcdf = vc.rename_axis("trader_group").reset_index(name="n")
        dbg.save_diagnostic_df("group_counts", gcdf)
        for g, c in vc.items():
            dbg.log("group", str(g), int(c))

        takers_only = full[full["trader_group"] != "Excluded"]
        denom = len(takers_only) if len(takers_only) else 1
        skilled_n = int((full["trader_group"] == "Skilled_Winner").sum())
        skilled_share = skilled_n / denom
        dbg.log("metric", "skilled_winner_share_of_classified", float(skilled_share))
        dbg.log("metric", "skilled_winner_count", skilled_n)

        if 0.02 <= skilled_share <= 0.06:
            dbg.assertion("skilled_share_band", "green", f"Skilled share {skilled_share:.2%} in 2–6% band (vs paper ~3.14%)")
        elif skilled_share < 0.02:
            dbg.assertion("skilled_share_band", "yellow", f"Skilled share {skilled_share:.2%} below 2%")
        else:
            dbg.assertion("skilled_share_band", "red", f"Skilled share {skilled_share:.2%} above 6% — check event-level randomization")

        # p-value histogram (use p_skill for winners, p_loss for losers)
        ps = []
        for _, r in full.iterrows():
            if r["trader_group"] == "Excluded":
                continue
            if r["realized_pnl"] > 0:
                ps.append(float(r["p_skill"]))
            elif r["realized_pnl"] < 0:
                ps.append(float(r["p_loss"]))
        dbg.save_diagnostic_df("pvalue_histogram_data", pd.DataFrame({"p": ps}))

        skilled = full[full["trader_group"] == "Skilled_Winner"].nlargest(10, "total_pnl")
        provenance: list[dict] = []
        pua = r2_uri("derived", "pnl_user_asset.parquet")
        mot = r2_uri("metadata", "outcome_tokens.parquet")
        mmk = r2_uri("metadata", "markets.parquet")
        ev_all = con.execute(
            f"""
            SELECT p.user_id, m.event_id, SUM(p.pnl) AS event_pnl
            FROM read_parquet('{pua}') p
            JOIN read_parquet('{mot}') ot ON CAST(ot.token_id AS VARCHAR) = CAST(p.asset AS VARCHAR)
            JOIN read_parquet('{mmk}') m ON m.market_id = ot.market_id
            GROUP BY p.user_id, m.event_id
            """
        ).df()
        for uid in skilled["user_id"].tolist():
            q = ev_all[ev_all["user_id"] == uid].sort_values("event_pnl", key=abs, ascending=False).head(30)
            provenance.append({"user_id": uid, "top_events_by_abs_pnl": q.to_dict("records")})
        dbg.save_diagnostic_json("top_skilled_provenance", provenance)

        out = full[
            [
                "user_id",
                "total_pnl",
                "n_trades",
                "n_events",
                "n_events_sim",
                "p_skill",
                "p_loss",
                "trader_group",
            ]
        ].rename(columns={"total_pnl": "realized_pnl"})
        uri = r2_uri("labels", "account_classification.parquet")
        con.register("_cl", out)
        con.execute(f"COPY _cl TO '{uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
        con.unregister("_cl")
        dbg.log("export", "account_classification_r2", uri)

        excl = int((full["trader_group"] == "Excluded").sum())
        dbg.log("quality", "excluded_accounts", excl)


if __name__ == "__main__":
    main()
