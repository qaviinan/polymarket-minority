"""Paper Task 5: random event-level train/test split, dual sign-randomization + classification, transition matrix."""

from __future__ import annotations

import os
from pathlib import Path

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


def _distinct_event_ids(con) -> list[str]:
    df = con.execute(
        """
        SELECT DISTINCT CAST(m.event_id AS VARCHAR) AS event_id
        FROM takers t
        JOIN outcome_tokens ot ON CAST(ot.token_id AS VARCHAR) = CAST(t.asset AS VARCHAR)
        JOIN markets m ON m.market_id = ot.market_id
        """
    ).df()
    return [str(x) for x in df["event_id"].tolist()]


def _create_takers_event_view(con, *, view_name: str, reg_name: str, event_ids: list[str]) -> None:
    con.register(reg_name, pd.DataFrame({"event_id": event_ids}))
    con.execute(
        f"""
        CREATE OR REPLACE VIEW {view_name} AS
        SELECT t.* FROM takers t
        JOIN outcome_tokens ot ON CAST(ot.token_id AS VARCHAR) = CAST(t.asset AS VARCHAR)
        JOIN markets m ON m.market_id = ot.market_id
        INNER JOIN {reg_name} e ON CAST(m.event_id AS VARCHAR) = CAST(e.event_id AS VARCHAR)
        """
    )


def _cleanup_arm(con, *, view_name: str, reg_name: str) -> None:
    for v in ("pnl_account", "pnl_user_asset", view_name):
        try:
            con.execute(f"DROP VIEW IF EXISTS {v}")
        except Exception:
            pass
    try:
        con.unregister(reg_name)
    except Exception:
        pass


def _export_classification(con, full: pd.DataFrame, labels_uri: str) -> None:
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
    con.register("_cl_evt", out)
    con.execute(f"COPY _cl_evt TO '{labels_uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
    con.unregister("_cl_evt")


def main() -> None:
    load_env()
    train_frac = float(os.environ.get("EVENT_OOS_TRAIN_FRAC", "0.8"))
    split_seed = int(os.environ.get("EVENT_OOS_SEED", "42"))
    sign_seed = int(os.environ.get("SIGN_RANDOMIZATION_SEED", "42"))
    n_iter = int(os.environ.get("SIGN_RANDOMIZATION_ITERATIONS", "10000"))
    export_r2 = os.environ.get("EVENT_OOS_EXPORT_R2", "true").strip().lower() not in {
        "0", "false", "no"
    }

    if not (0.0 < train_frac < 1.0):
        raise SystemExit("EVENT_OOS_TRAIN_FRAC must be strictly between 0 and 1")

    with debug_step("event_split_persistence") as dbg:
        dbg.log("meta", "event_oos_train_frac", float(train_frac))
        dbg.log("meta", "event_oos_split_seed", split_seed)
        dbg.log("meta", "sign_randomization_seed_base", sign_seed)
        dbg.log("meta", "n_iterations", n_iter)
        dbg.log("meta", "export_r2", export_r2)

        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)

        events = _distinct_event_ids(con)
        dbg.log("ingest", "n_distinct_events", len(events))
        if len(events) < 2:
            raise SystemExit("Need at least 2 distinct event_id values in takers × markets join")

        rng_split = np.random.default_rng(split_seed)
        order = np.array(events, dtype=object)
        rng_split.shuffle(order)
        n_train = max(1, int(len(order) * train_frac))
        if n_train >= len(order):
            n_train = len(order) - 1
        train_events = list(order[:n_train])
        test_events = list(order[n_train:])
        dbg.log("ingest", "n_train_events", len(train_events))
        dbg.log("ingest", "n_test_events", len(test_events))

        suffix = f"_seed{split_seed}"
        sig_train_uri = r2_uri("derived", f"sign_randomization_summary_event_train{suffix}.parquet")
        sig_test_uri = r2_uri("derived", f"sign_randomization_summary_event_test{suffix}.parquet")
        lbl_train_uri = r2_uri("labels", f"account_classification_event_train{suffix}.parquet")
        lbl_test_uri = r2_uri("labels", f"account_classification_event_test{suffix}.parquet")
        log_dir = logs_path("event_split_persistence") / f"seed_{split_seed}"
        log_dir.mkdir(parents=True, exist_ok=True)

        arms: list[tuple[str, list[str], str, str, str, int]] = [
            ("train", train_events, "takers_evt_train", "_evt_ids_train_arm", sig_train_uri, sign_seed),
            ("test", test_events, "takers_evt_test", "_evt_ids_test_arm", sig_test_uri, sign_seed + 1_000_003),
        ]

        classified: dict[str, pd.DataFrame] = {}

        for arm_name, eids, view_name, reg_name, sig_uri, arm_seed in arms:
            _cleanup_arm(con, view_name=view_name, reg_name=reg_name)
            _create_takers_event_view(con, view_name=view_name, reg_name=reg_name, event_ids=eids)
            register_pnl_views(con, taker_view=view_name)

            ev_df = load_event_pnl_matrix(con)
            acct = con.execute("SELECT user_id, total_pnl, n_trades, n_events FROM pnl_account").df()
            dbg.log(f"arm_{arm_name}", "n_event_pnl_rows", len(ev_df))
            dbg.log(f"arm_{arm_name}", "n_accounts_pnl", len(acct))

            rng = np.random.default_rng(arm_seed)
            out, m0, wall = run_sign_randomization_core(
                ev_df,
                acct,
                rng,
                n_iter,
                tqdm_desc=f"sign_event_{arm_name}",
                plot_users=None,
                plot_dir=None,
                dbg=None,
            )
            dbg.log(f"arm_{arm_name}", "sign_users_output_rows", len(out))
            dbg.log(f"arm_{arm_name}", "peak_sim_array_bytes_estimate", int(m0))
            dbg.log(f"arm_{arm_name}", "wall_seconds", float(wall))

            if export_r2:
                con.register("_sig_evt", out)
                con.execute(f"COPY _sig_evt TO '{sig_uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
                con.unregister("_sig_evt")
                dbg.log(f"export_{arm_name}", "sign_randomization_r2", sig_uri)

            full = build_classification_table(acct, out)
            classified[arm_name] = full[["user_id", "trader_group"]].copy()
            lbl_uri = lbl_train_uri if arm_name == "train" else lbl_test_uri
            if export_r2:
                _export_classification(con, full, lbl_uri)
                dbg.log(f"export_{arm_name}", "classification_r2", lbl_uri)

            vc = full["trader_group"].value_counts()
            dbg.save_diagnostic_df(f"group_counts_{arm_name}", vc.rename_axis("trader_group").reset_index(name="n"))

            _cleanup_arm(con, view_name=view_name, reg_name=reg_name)

        _cleanup_arm(con, view_name="takers_evt_train", reg_name="_evt_ids_train_arm")
        _cleanup_arm(con, view_name="takers_evt_test", reg_name="_evt_ids_test_arm")

        tr = classified["train"].rename(columns={"trader_group": "train_group"})
        te = classified["test"].rename(columns={"trader_group": "test_group"})
        mm = tr.merge(te, on="user_id", how="inner")
        dbg.log("transition", "n_users_in_both_arms", len(mm))

        ctab = pd.crosstab(mm["train_group"], mm["test_group"])
        ctab_path = log_dir / "transition_counts.parquet"
        ctab.to_parquet(ctab_path)
        dbg.log("diagnostic", "transition_counts_parquet", str(ctab_path))

        skilled_train = mm[mm["train_group"] == "Skilled_Winner"]
        n_st = len(skilled_train)
        if n_st > 0:
            p_persist = float((skilled_train["test_group"] == "Skilled_Winner").mean())
            dbg.log("metric", "n_skilled_winner_train", int(n_st))
            dbg.log("metric", "P_skilled_test_given_skilled_train", p_persist)

            both_cls = mm[(mm["train_group"] != "Excluded") & (mm["test_group"] != "Excluded")]
            st_cls = both_cls[both_cls["train_group"] == "Skilled_Winner"]
            base = float((both_cls["test_group"] == "Skilled_Winner").mean()) if len(both_cls) else float("nan")
            p_cls = float((st_cls["test_group"] == "Skilled_Winner").mean()) if len(st_cls) else float("nan")
            dbg.log("metric", "n_users_classified_both_arms", int(len(both_cls)))
            dbg.log("metric", "n_skilled_train_classified_test", int(len(st_cls)))
            dbg.log("metric", "P_skilled_test_given_skilled_train_classified", p_cls)
            dbg.log("metric", "base_P_skilled_test_classified_both", base)
            dbg.log("metric", "persistence_lift", p_cls / base if base and base > 0 else float("nan"))
        else:
            dbg.log("metric", "n_skilled_winner_train", 0)
            dbg.log("metric", "P_skilled_test_given_skilled_train", float("nan"))

        dbg.assertion(
            "event_split_complete",
            "green",
            "Wrote event-train/test sign summaries, labels, and transition_counts.parquet",
        )


if __name__ == "__main__":
    main()
