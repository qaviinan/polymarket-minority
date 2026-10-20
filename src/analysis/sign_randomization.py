"""Event-level sign randomization (10k draws) and null summaries."""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from tqdm import tqdm

from src.lib.debug import debug_step
from src.lib.duck import get_duck, load_env, r2_uri
from src.lib.period import logs_path


def load_event_pnl_matrix(con) -> pd.DataFrame:
    """Rows: user_id, event_id, event_pnl, event_fees."""
    return con.execute(
        """
        WITH ev AS (
          SELECT
            p.user_id,
            m.event_id,
            SUM(p.pnl) AS event_pnl,
            SUM(p.fees) AS event_fees
          FROM pnl_user_asset p
          JOIN outcome_tokens ot ON CAST(ot.token_id AS VARCHAR) = p.asset
          JOIN markets m ON m.market_id = ot.market_id
          GROUP BY p.user_id, m.event_id
        )
        SELECT * FROM ev
        """
    ).df()


def run_sign_randomization_core(
    ev_df: pd.DataFrame,
    acct: pd.DataFrame,
    rng: np.random.Generator,
    n_iter: int,
    *,
    exact_max_events: int = 12,
    min_events: int = 5,
    tqdm_desc: str = "sign_randomization",
    plot_users: set[Any] | frozenset[Any] | None = None,
    plot_dir: Path | None = None,
    dbg: Any | None = None,
) -> tuple[pd.DataFrame, int, float]:
    """
    Paper-faithful event-level direction randomization.

    Per draw, each event gets s_k in {+1, -1}; all fills in that event flip BUY/SELL
    together (sizes, prices, fees fixed). Null PnL is recomputed as
    sum_k s_k * event_gross_k - total_event_fees where event_gross = event_pnl + event_fees.

    ``acct`` must align with ``ev_df`` (same window). Returns (summary_df, peak_bytes, wall_s).
    """
    users = acct["user_id"].unique()
    event_groups = {uid: sub for uid, sub in ev_df.groupby("user_id", sort=False)}
    acct_by_user = acct.set_index("user_id")
    rows_out: list[dict] = []
    m0 = 0
    t0 = time.perf_counter()
    for uid in tqdm(users, desc=tqdm_desc):
        account = acct_by_user.loc[uid]
        # Classification deterministically excludes these accounts, so
        # simulating them only adds runtime and cannot change a label.
        if int(account["n_trades"]) < 10:
            continue
        sub = event_groups.get(uid)
        if sub is None:
            continue
        e_count = len(sub)
        if e_count < min_events:
            continue
        gross = (sub["event_pnl"] + sub["event_fees"]).to_numpy(dtype=np.float64)
        user_total_fees = float(sub["event_fees"].sum())
        realized = float(account["total_pnl"])
        identity = float(np.nansum(gross) - user_total_fees) if np.isfinite(user_total_fees) else float("nan")
        tol = 1e-6 * max(1.0, abs(realized)) if np.isfinite(realized) else float("nan")
        broken = (
            not np.isfinite(gross).all()
            or not np.isfinite(realized)
            or not np.isfinite(identity)
            or abs(identity - realized) > tol
        )
        if broken:
            rows_out.append(
                {
                    "user_id": uid,
                    "realized_pnl": realized,
                    "n_events_sim": e_count,
                    "n_trades": int(account["n_trades"]),
                    "null_mean": float("nan"),
                    "null_std": float("nan"),
                    "p_skill": float("nan"),
                    "p_loss": float("nan"),
                    "pvalue_method": "unscorable",
                    "n_null_draws": 0,
                    "theoretical_p_floor": float(2.0 ** (-e_count)),
                    "bad_null_mean": True,
                    "unscorable": True,
                }
            )
            continue
        if e_count <= exact_max_events:
            n_draws = 1 << e_count
            states = np.arange(n_draws, dtype=np.uint64)[:, None]
            shifts = np.arange(e_count, dtype=np.uint64)[None, :]
            r_mat = 2.0 * ((states >> shifts) & 1).astype(np.float64) - 1.0
            pvalue_method = "exact"
        else:
            n_draws = n_iter
            r_mat = rng.choice([-1.0, 1.0], size=(n_draws, e_count))
            pvalue_method = "monte_carlo_add_one"
        nulls = r_mat @ gross - user_total_fees
        null_mean = float(nulls.mean())
        null_std = float(nulls.std(ddof=1) or 1.0)
        upper_count = int((nulls >= realized - tol).sum())
        lower_count = int((nulls <= realized + tol).sum())
        if pvalue_method == "exact":
            p_skill = float(upper_count / n_draws)
            p_loss = float(lower_count / n_draws)
        else:
            p_skill = float((upper_count + 1) / (n_draws + 1))
            p_loss = float((lower_count + 1) / (n_draws + 1))
        # Monte Carlo sample means fluctuate by null_std / sqrt(n_iter).
        # A fixed 1%-of-SD cutoff is only ~1 standard error at 10k draws and
        # therefore flags roughly one third of healthy simulations.
        bad_mean = (
            abs(null_mean - (-user_total_fees))
            > 3.0 * null_std / np.sqrt(n_draws)
        )

        rows_out.append(
            {
                "user_id": uid,
                "realized_pnl": realized,
                "n_events_sim": e_count,
                "n_trades": int(account["n_trades"]),
                "null_mean": null_mean,
                "null_std": null_std,
                "p_skill": p_skill,
                "p_loss": p_loss,
                "pvalue_method": pvalue_method,
                "n_null_draws": n_draws,
                "theoretical_p_floor": float(2.0 ** (-e_count)),
                "bad_null_mean": bad_mean,
                "unscorable": False,
            }
        )

        m0 = max(m0, nulls.nbytes + gross.nbytes + r_mat.nbytes)

        if plot_users is not None and plot_dir is not None and uid in plot_users:
            plot_dir.mkdir(parents=True, exist_ok=True)
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.hist(nulls, bins=50, density=True, alpha=0.75)
            ax.axvline(realized, color="red", lw=2, label="realized")
            ax.set_title(f"Null PnL user {str(uid)[:12]}…")
            ax.legend()
            pth = plot_dir / f"null_hist_{str(uid)[:20]}.png"
            fig.savefig(pth, dpi=120, bbox_inches="tight")
            plt.close(fig)
            if dbg is not None:
                dbg.log("plot", "saved", str(pth))

    out = pd.DataFrame(rows_out)
    return out, m0, time.perf_counter() - t0


def main() -> None:
    load_env()
    n_iter = int(os.environ.get("SIGN_RANDOMIZATION_ITERATIONS", "10000"))
    exact_max_events = int(os.environ.get("SIGN_RANDOMIZATION_EXACT_MAX_EVENTS", "12"))
    seed = int(os.environ.get("SIGN_RANDOMIZATION_SEED", "42"))
    rng = np.random.default_rng(seed)

    from src.analysis.partition import (
        MINORITY_SPLIT_ENV,
        minority_train_test_split_enabled,
        register_order_flow_views,
    )
    from src.analysis.event_grouping import register_event_views
    from src.analysis.pnl import register_pnl_views

    with debug_step("sign_randomization") as dbg:
        dbg.log("meta", "n_iterations", n_iter)
        dbg.log("meta", "exact_max_events", exact_max_events)
        dbg.log("meta", "random_seed", seed)
        dbg.log(
            "meta",
            "minority_label_split",
            MINORITY_SPLIT_ENV if minority_train_test_split_enabled() else "off",
        )
        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)
        register_pnl_views(con)

        ev_df = load_event_pnl_matrix(con)
        acct = con.execute("SELECT user_id, total_pnl, n_trades, n_events FROM pnl_account").df()
        label_scope = "train_weeks_0_2" if minority_train_test_split_enabled() else "all_window"
        market_universe = os.environ.get("PNL_MARKET_UNIVERSE", "all").strip().lower() or "all"
        dbg.log("ingest", "n_event_pnl_rows", len(ev_df))
        dbg.log("ingest", "n_accounts", len(acct))

        users = acct["user_id"].unique()
        plot_dir = logs_path("sign_randomization")
        plot_dir.mkdir(parents=True, exist_ok=True)

        ec = ev_df.groupby("user_id").size()
        dbg.save_diagnostic_df("events_per_user_dist", ec.reset_index(name="n_events"))
        dbg.log("quality", "max_events_per_user", int(ec.max() if len(ec) else 0))

        sample_users = set(rng.choice(users, size=min(5, len(users)), replace=False))
        top5 = acct.nlargest(5, "total_pnl")["user_id"].tolist()
        plot_users = sample_users.union(set(top5))

        out, m0, wall = run_sign_randomization_core(
            ev_df,
            acct,
            rng,
            n_iter,
            exact_max_events=exact_max_events,
            tqdm_desc="sign_randomization",
            plot_users=plot_users,
            plot_dir=plot_dir,
            dbg=dbg,
        )
        out["label_scope"] = label_scope
        out["market_universe"] = market_universe
        acct["label_scope"] = label_scope
        acct["market_universe"] = market_universe

        dbg.log("meta", "peak_sim_array_bytes_estimate", m0)
        dbg.log("meta", "wall_seconds", wall)

        dbg.save_diagnostic_df("null_summary_per_user", out)
        bad_users = out[out["bad_null_mean"]]
        dbg.log("quality", "users_bad_null_mean", len(bad_users))
        if len(bad_users) > 0:
            dbg.assertion(
                "null_mean_expected",
                "red",
                f"{len(bad_users)} users with null_mean far from -total_event_fees",
            )
        else:
            dbg.assertion(
                "null_mean_expected",
                "green",
                "All simulated null means near E[null] = -total_event_fees",
            )

        uri = r2_uri("derived", "sign_randomization_summary.parquet")
        con.register("_sr", out)
        con.execute(f"COPY _sr TO '{uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)")
        con.unregister("_sr")
        dbg.log("export", "sign_randomization_summary_r2", uri)

        # Keep classification inputs atomic: a sign run always publishes the
        # matching account and user-asset PnL rather than relying on a prior
        # separately configured pnl.py run.
        acct_uri = r2_uri("derived", "pnl_account.parquet")
        con.register("_acct_aligned", acct)
        con.execute(
            f"COPY _acct_aligned TO '{acct_uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)"
        )
        con.unregister("_acct_aligned")
        pua_uri = r2_uri("derived", "pnl_user_asset.parquet")
        con.execute(
            f"COPY (SELECT *, '{label_scope}' AS label_scope, "
            f"'{market_universe}' AS market_universe FROM pnl_user_asset) "
            f"TO '{pua_uri}' (FORMAT PARQUET, OVERWRITE_OR_IGNORE true)"
        )
        dbg.log("export", "aligned_pnl_account_r2", acct_uri)
        dbg.log("export", "aligned_pnl_user_asset_r2", pua_uri)


if __name__ == "__main__":
    main()
