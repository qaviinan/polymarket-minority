"""Discreteness-aware eligible cohort, FDR, and the excess-over-chance statistics."""

from __future__ import annotations

import json
from math import sqrt

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from src.lib.debug import debug_step
from src.lib.duck import get_duck, r2_uri
from src.lib.period import logs_path


def benjamini_hochberg(pvalues: np.ndarray, q: float) -> np.ndarray:
    p = np.asarray(pvalues, dtype=float)
    keep = np.zeros(len(p), dtype=bool)
    if len(p) == 0:
        return keep
    order = np.argsort(p)
    ranked = p[order]
    passing = np.flatnonzero(ranked <= q * np.arange(1, len(p) + 1) / len(p))
    if len(passing):
        keep[order[: passing[-1] + 1]] = True
    return keep


def storey_pi0(pvalues: np.ndarray, lam: float = 0.5) -> float:
    p = np.asarray(pvalues, dtype=float)
    if len(p) == 0:
        return float("nan")
    return float(min(1.0, (p > lam).sum() / ((1.0 - lam) * len(p))))


def null_rejection_probability(k: int, alpha: float = 0.05) -> float:
    if k < 1:
        return 0.0
    if k <= 52:
        states = float(2**k)
        return float(np.floor(np.nextafter(alpha, 0.0) * states) / states)
    return alpha


def main() -> None:
    path = logs_path("coverage_audit", "account_classification_primary.parquet")
    if not path.exists():
        raise RuntimeError("Run coverage_audit first; primary restricted labels are missing")

    with debug_step("multiple_testing") as dbg:
        labels = pd.read_parquet(path)
        eligible = labels[
            (labels["n_trades"] >= 10)
            & (labels["n_events_sim"] >= 5)
            & labels["p_skill"].notna()
        ].copy()
        p = eligible["p_skill"].to_numpy(dtype=float)
        # One-sided: only accounts that made money are candidates for a skill rejection.
        # Losers are scored by p_loss, and a NaN p_skill must not count as a rejection.
        winner = eligible["total_pnl"].gt(0) & eligible["p_skill"].notna()
        eligible["bh_q05"] = False
        eligible["bh_q10"] = False
        if winner.any():
            eligible.loc[winner, "bh_q05"] = benjamini_hochberg(
                eligible.loc[winner, "p_skill"].to_numpy(), 0.05
            )
            eligible.loc[winner, "bh_q10"] = benjamini_hochberg(
                eligible.loc[winner, "p_skill"].to_numpy(), 0.10
            )
        eligible["null_reject_prob"] = eligible["n_events_sim"].astype(int).map(
            null_rejection_probability
        )

        observed = int((winner & eligible["p_skill"].lt(0.05)).sum())
        expected = float(eligible["null_reject_prob"].sum())
        variance = float(
            (eligible["null_reject_prob"] * (1.0 - eligible["null_reject_prob"])).sum()
        )
        z_excess = (observed - expected) / sqrt(max(variance, 1e-12))
        bh05 = int(eligible["bh_q05"].sum())
        bh10 = int(eligible["bh_q10"].sum())
        skilled = labels[labels["trader_group"] == "Skilled_Winner"]
        min_k = int(skilled["n_events_sim"].min()) if len(skilled) else 0

        dbg.log("cohort", "n_accounts_total", int(len(labels)))
        dbg.log("cohort", "n_eligible_k_ge_5", int(len(eligible)))
        dbg.log("cohort", "eligible_share", float(len(eligible) / max(len(labels), 1)))
        dbg.log("skill", "nominal_p_lt_005", observed)
        dbg.log("skill", "eligible_skilled_share", float(observed / max(len(eligible), 1)))
        dbg.log("skill", "discrete_null_expected", expected)
        dbg.log("skill", "excess_z", z_excess)
        dbg.log("skill", "bh_q05_survivors", bh05)
        dbg.log("skill", "bh_q10_survivors", bh10)
        dbg.log("skill", "storey_pi0", storey_pi0(p))
        dbg.log("skill", "min_events_among_skilled", min_k)
        dbg.save_diagnostic_df("eligible_accounts", eligible)
        fdr_flags = eligible[["user_id", "bh_q05", "bh_q10"]]
        labels_fdr = labels.merge(fdr_flags, on="user_id", how="left")
        for col in ("bh_q05", "bh_q10"):
            labels_fdr[col] = labels_fdr[col].astype("boolean").fillna(False).astype(bool)
        labels_fdr["trader_group_fdr_q05"] = labels_fdr["trader_group"]
        demote = (
            labels_fdr["trader_group"].eq("Skilled_Winner")
            & ~labels_fdr["bh_q05"]
        )
        labels_fdr.loc[demote, "trader_group_fdr_q05"] = "Lucky_Winner"
        fdr_path = logs_path("multiple_testing", "account_classification_primary_fdr.parquet")
        fdr_path.parent.mkdir(parents=True, exist_ok=True)
        labels_fdr.to_parquet(fdr_path, index=False)
        con = get_duck()
        con.register("_labels_fdr", labels_fdr)
        fdr_uri = r2_uri("labels", "account_classification_stage1_fdr_q05.parquet")
        con.execute(
            f"COPY _labels_fdr TO '{fdr_uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)"
        )
        con.unregister("_labels_fdr")
        dbg.log("export", "fdr_labels_r2", fdr_uri)

        by_k = (
            eligible.assign(k_bucket=eligible["n_events_sim"].clip(upper=20))
            .groupby("k_bucket")
            .agg(
                n=("user_id", "size"),
                p_lt_005=("p_skill", lambda x: int((x < 0.05).sum())),
                skilled=("trader_group", lambda x: int((x == "Skilled_Winner").sum())),
            )
            .reset_index()
        )
        dbg.save_diagnostic_df("events_and_flags_by_k", by_k)

        fig, ax = plt.subplots(figsize=(7, 4))
        ax.hist(p, bins=np.linspace(0, 1, 41), alpha=0.8)
        ax.axvline(0.05, color="red", linewidth=1.5)
        ax.set(xlabel="p_skill (eligible K≥5)", ylabel="Accounts", title="Eligible skill p-values")
        fig.tight_layout()
        plot_path = logs_path("multiple_testing", "p_skill_histogram_k_ge_5.png")
        fig.savefig(plot_path, dpi=140)
        plt.close(fig)
        dbg.log("plot", "p_skill_histogram", str(plot_path))

        floor_ok = min_k >= 5 if len(skilled) else True
        dbg.assertion(
            "discreteness_floor",
            "green" if floor_ok else "red",
            f"Minimum Skilled event count is {min_k}",
        )
        no_zero = bool((eligible["p_skill"] > 0).all())
        dbg.assertion(
            "nonzero_empirical_pvalues",
            "green" if no_zero else "red",
            "All p-values are positive" if no_zero else "Zero p-values remain",
        )

        n_unscored = (
            int(labels["unscorable"].fillna(False).sum()) if "unscorable" in labels.columns else 0
        )
        unscored_ok = n_unscored / max(len(labels), 1) < 0.001
        dbg.assertion(
            "unscorable_share",
            "green" if unscored_ok else "red",
            f"{n_unscored} accounts unscorable ({n_unscored / max(len(labels), 1):.4%} of classified file)",
        )
        gate_pass = bool(floor_ok and no_zero and unscored_ok and bh05 > 0 and z_excess >= 2.576)
        verdict = {
            "gate": "A_statistical",
            "passed": gate_pass,
            "n_eligible": int(len(eligible)),
            "observed_nominal": observed,
            "eligible_skilled_share": float(observed / max(len(eligible), 1)),
            "expected_under_discrete_null": expected,
            "excess_z": z_excess,
            "bh_q05_survivors": bh05,
            "bh_q10_survivors": bh10,
            "storey_pi0": storey_pi0(p),
        }
        verdict_path = logs_path("multiple_testing", "gate_a_statistical.json")
        verdict_path.write_text(json.dumps(verdict, indent=2), encoding="utf-8")
        dbg.assertion(
            "gate_a_statistical",
            "green" if gate_pass else "red",
            (
                f"PASS: {bh05} BH q=0.05 survivors, excess z={z_excess:.2f}"
                if gate_pass
                else f"FAIL: BH q=0.05={bh05}, excess z={z_excess:.2f}"
            ),
        )


if __name__ == "__main__":
    main()
