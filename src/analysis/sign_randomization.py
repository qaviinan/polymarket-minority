"""Event-level sign randomization (10k draws) and null summaries."""

from __future__ import annotations

import os
import time
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from tqdm import tqdm

from src.lib.debug import debug_step
from src.lib.duck import get_duck, r2_uri


def load_event_pnl_matrix(con) -> pd.DataFrame:
    """Rows: user_id, event_id, event_pnl."""
    return con.execute(
        """
        WITH ev AS (
          SELECT
            p.user_id,
            m.event_id,
            SUM(p.pnl) AS event_pnl
          FROM pnl_user_asset p
          JOIN outcome_tokens ot ON CAST(ot.token_id AS VARCHAR) = p.asset
          JOIN markets m ON m.market_id = ot.market_id
          GROUP BY p.user_id, m.event_id
        )
        SELECT * FROM ev
        """
    ).df()


def main() -> None:
    n_iter = int(os.environ.get("SIGN_RANDOMIZATION_ITERATIONS", "10000"))
    seed = int(os.environ.get("SIGN_RANDOMIZATION_SEED", "42"))
    rng = np.random.default_rng(seed)

    from src.analysis.partition import register_order_flow_views
    from src.analysis.event_grouping import register_event_views
    from src.analysis.pnl import register_pnl_views

    with debug_step("sign_randomization") as dbg:
        dbg.log("meta", "n_iterations", n_iter)
        dbg.log("meta", "random_seed", seed)
        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)
        register_pnl_views(con)

        ev_df = load_event_pnl_matrix(con)
        acct = con.execute("SELECT user_id, total_pnl, n_trades, n_events FROM pnl_account").df()
        dbg.log("ingest", "n_event_pnl_rows", len(ev_df))
        dbg.log("ingest", "n_accounts", len(acct))

        users = acct["user_id"].unique()
        rows_out: list[dict] = []

        plot_dir = Path("logs/sign_randomization")
        plot_dir.mkdir(parents=True, exist_ok=True)

        ec = ev_df.groupby("user_id").size()
        dbg.save_diagnostic_df("events_per_user_dist", ec.reset_index(name="n_events"))
        dbg.log("quality", "max_events_per_user", int(ec.max() if len(ec) else 0))

        sample_users = set(rng.choice(users, size=min(5, len(users)), replace=False))
        top5 = acct.nlargest(5, "total_pnl")["user_id"].tolist()
        plot_users = sample_users.union(set(top5))

        m0 = 0
        t0 = time.perf_counter()
        for uid in tqdm(users, desc="sign_randomization"):
            sub = ev_df[ev_df["user_id"] == uid]
            e_count = len(sub)
            if e_count < 2:
                continue
            c = sub["event_pnl"].to_numpy(dtype=np.float64)
            realized = float(acct.loc[acct["user_id"] == uid, "total_pnl"].iloc[0])
            r_mat = rng.choice([-1.0, 1.0], size=(n_iter, e_count))
            nulls = r_mat @ c
            null_mean = float(nulls.mean())
            null_std = float(nulls.std(ddof=1) or 1.0)
            p_skill = float((nulls >= realized).mean())
            p_loss = float((nulls <= realized).mean())
            bad_mean = abs(null_mean) > 0.01 * null_std

            rows_out.append(
                {
                    "user_id": uid,
                    "realized_pnl": realized,
                    "n_events_sim": e_count,
                    "n_trades": int(acct.loc[acct["user_id"] == uid, "n_trades"].iloc[0]),
                    "null_mean": null_mean,
                    "null_std": null_std,
                    "p_skill": p_skill,
                    "p_loss": p_loss,
                    "bad_null_mean": bad_mean,
                }
            )

            m0 = max(m0, nulls.nbytes + c.nbytes + r_mat.nbytes)

            if uid in plot_users:
                fig, ax = plt.subplots(figsize=(6, 4))
                ax.hist(nulls, bins=50, density=True, alpha=0.75)
                ax.axvline(realized, color="red", lw=2, label="realized")
                ax.set_title(f"Null PnL user {str(uid)[:12]}…")
                ax.legend()
                pth = plot_dir / f"null_hist_{str(uid)[:20]}.png"
                fig.savefig(pth, dpi=120, bbox_inches="tight")
                plt.close(fig)
                dbg.log("plot", "saved", str(pth))

        dbg.log("meta", "peak_sim_array_bytes_estimate", m0)
        dbg.log("meta", "wall_seconds", time.perf_counter() - t0)

        out = pd.DataFrame(rows_out)
        dbg.save_diagnostic_df("null_summary_per_user", out)
        bad_users = out[out["bad_null_mean"]]
        dbg.log("quality", "users_bad_null_mean", len(bad_users))
        if len(bad_users) > 0:
            dbg.assertion("null_mean_zero", "red", f"{len(bad_users)} users with |null_mean| > 0.01*std")
        else:
            dbg.assertion("null_mean_zero", "green", "All simulated null means within tolerance")

        uri = r2_uri("derived", "sign_randomization_summary.parquet")
        con.register("_sr", out)
        con.execute(f"COPY _sr TO '{uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
        con.unregister("_sr")
        dbg.log("export", "sign_randomization_summary_r2", uri)


if __name__ == "__main__":
    main()
