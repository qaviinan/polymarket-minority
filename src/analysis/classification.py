"""Assign Skilled / Lucky / Unlucky / Unskilled from sign-randomization p-values."""

from __future__ import annotations

import os

import pandas as pd

from src.lib.debug import debug_step
from src.lib.duck import get_duck, r2_uri

from src.analysis.multiple_testing import benjamini_hochberg
from src.analysis.partition import minority_train_test_split_enabled


def excluded_reason(row: pd.Series) -> str | None:
    """Why an account gets no skill label. Fewer than 5 events cannot separate skill from luck."""
    if bool(row.get("unscorable")):
        return "unscorable"
    n = row.get("n_trades", 0) or 0
    k = row.get("n_events_sim")
    if pd.isna(k):
        k = row.get("n_events", 0)
    k = 0 if pd.isna(k) else k
    if n < 10:
        return "insufficient_trades"
    if k < 5:
        return "insufficient_events"
    if pd.isna(row.get("p_skill")) or pd.isna(row.get("p_loss")):
        return "unscorable"
    return None


def assign_group(row: pd.Series) -> str:
    if excluded_reason(row) is not None:
        return "Excluded"
    r = row["realized_pnl"]
    if r > 0:
        return "Skilled_Winner" if row["p_skill"] < 0.05 else "Lucky_Winner"
    if r < 0:
        return "Unskilled_Loser" if row["p_loss"] < 0.05 else "Unlucky_Loser"
    return "Excluded"


def build_classification_table(acct: pd.DataFrame, sig: pd.DataFrame) -> pd.DataFrame:
    """Merge account totals with sign-randomization outputs and assign ``trader_group``."""
    keep = ["user_id", "n_events_sim", "null_mean", "null_std", "p_skill", "p_loss", "bad_null_mean"]
    if "unscorable" in sig.columns:
        keep.append("unscorable")
    sig2 = sig[keep].copy()
    full = acct.merge(sig2, on="user_id", how="left")
    full["realized_pnl"] = full["total_pnl"]
    if "unscorable" not in full.columns:
        full["unscorable"] = False
    full["unscorable"] = full["unscorable"].fillna(False).astype(bool)
    full["n_events_sim"] = full["n_events_sim"].fillna(0)
    # Leave missing p-values missing. Filling them with 1 would hide an unscorable account
    # and filling them with 0 would label it skilled.
    full["excluded_reason"] = full.apply(excluded_reason, axis=1)
    full["trader_group"] = full.apply(assign_group, axis=1)
    return full


def main() -> None:
    with debug_step("classification") as dbg:
        con = get_duck()
        acct_uri = r2_uri("derived", "pnl_account.parquet")
        sig_uri = r2_uri("derived", "sign_randomization_summary.parquet")
        acct = con.execute(f"SELECT * FROM read_parquet('{acct_uri}')").df()
        sig = con.execute(f"SELECT * FROM read_parquet('{sig_uri}')").df()
        dbg.log("ingest", "n_accounts", len(acct))
        dbg.log("ingest", "n_sign_randomization_rows", len(sig))
        expected_scope = "train_weeks_0_2" if minority_train_test_split_enabled() else "all_window"
        expected_universe = os.environ.get("PNL_MARKET_UNIVERSE", "all").strip().lower() or "all"
        for name, frame in (("pnl_account", acct), ("sign_randomization_summary", sig)):
            if "label_scope" not in frame.columns:
                raise RuntimeError(
                    f"{name} has no label_scope provenance. Rerun "
                    "`python -m src.analysis.sign_randomization` before classification."
                )
            if "market_universe" not in frame.columns:
                raise RuntimeError(f"{name} has no market_universe provenance; rerun sign_randomization")
            universes = set(frame["market_universe"].dropna().astype(str))
            if universes != {expected_universe}:
                raise RuntimeError(
                    f"{name} universe {sorted(universes)} does not match {expected_universe!r}"
                )
            scopes = set(frame["label_scope"].dropna().astype(str))
            if scopes != {expected_scope}:
                raise RuntimeError(
                    f"{name} scope {sorted(scopes)} does not match current expected scope "
                    f"{expected_scope!r}. Rerun sign_randomization with the current .env."
                )
        if minority_train_test_split_enabled():
            dbg.log(
                "meta",
                "account_classification_note",
                "Labels are train-only (takers_train / first 3 week_index buckets); "
                "use OIB_EVAL_WEEK=test for out-of-time fills.",
            )

        full = build_classification_table(acct, sig)
        full["trader_group_nominal"] = full["trader_group"]
        fdr_q_raw = os.environ.get("CLASSIFICATION_FDR_Q", "").strip()
        fdr_q = float(fdr_q_raw) if fdr_q_raw else None
        if fdr_q is not None:
            elig_mask = (
                (full["n_trades"] >= 10)
                & (full["n_events_sim"] >= 5)
                & (full["total_pnl"] > 0)
                & full["p_skill"].notna()
            )
            survives = pd.Series(False, index=full.index)
            survives.loc[elig_mask] = benjamini_hochberg(
                full.loc[elig_mask, "p_skill"].to_numpy(), fdr_q
            )
            demote = full["trader_group"].eq("Skilled_Winner") & ~survives
            full.loc[demote, "trader_group"] = "Lucky_Winner"
            dbg.log("skill", "fdr_q", fdr_q)
            dbg.log("skill", "fdr_demoted_skilled", int(demote.sum()))
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
        eligible = full[(full["n_trades"] >= 10) & (full["n_events_sim"] >= 5)]
        dbg.log("metric", "eligible_k_ge_5_count", int(len(eligible)))
        dbg.log(
            "metric",
            "skilled_winner_share_of_eligible",
            float(skilled_n / len(eligible)) if len(eligible) else float("nan"),
        )
        skilled_min_k = full.loc[
            full["trader_group"] == "Skilled_Winner", "n_events_sim"
        ].min()
        if pd.notna(skilled_min_k) and int(skilled_min_k) < 5:
            dbg.assertion(
                "skilled_discreteness_floor",
                "red",
                f"Skilled account found with only {int(skilled_min_k)} events",
            )
        else:
            dbg.assertion(
                "skilled_discreteness_floor",
                "green",
                "All Skilled accounts have at least five independently flippable events",
            )

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
                "trader_group_nominal",
            ]
        ].rename(columns={"total_pnl": "realized_pnl"})
        out["fdr_q"] = fdr_q if fdr_q is not None else float("nan")
        out["label_scope"] = expected_scope
        out["market_universe"] = expected_universe
        uri = r2_uri("labels", "account_classification.parquet")
        con.register("_cl", out)
        con.execute(f"COPY _cl TO '{uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
        con.unregister("_cl")
        dbg.log("export", "account_classification_r2", uri)

        excl = int((full["trader_group"] == "Excluded").sum())
        dbg.log("quality", "excluded_accounts", excl)


if __name__ == "__main__":
    main()
