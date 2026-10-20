from __future__ import annotations

import duckdb
import numpy as np
import pandas as pd

from src.analysis.event_grouping import resolved_by_cutoff_sql
from src.analysis.multiple_testing import (
    benjamini_hochberg,
    null_rejection_probability,
    storey_pi0,
)
from src.analysis.partition import week_index_for_ts
from src.analysis.sign_randomization import run_sign_randomization_core
from src.analysis.skilled_profile import _calibrated_null


def _one_user(events: list[float], *, n_trades: int = 10):
    ev = pd.DataFrame(
        {
            "user_id": ["u"] * len(events),
            "event_id": [f"e{i}" for i in range(len(events))],
            "event_pnl": events,
            "event_fees": [0.0] * len(events),
        }
    )
    acct = pd.DataFrame(
        {
            "user_id": ["u"],
            "total_pnl": [sum(events)],
            "n_trades": [n_trades],
            "n_events": [len(events)],
        }
    )
    return ev, acct


def test_exact_randomization_respects_discreteness_floor():
    ev4, acct4 = _one_user([1.0, 2.0, 4.0, 8.0])
    out4, _, _ = run_sign_randomization_core(
        ev4, acct4, np.random.default_rng(1), 100, exact_max_events=12, min_events=2
    )
    assert out4.iloc[0]["pvalue_method"] == "exact"
    assert out4.iloc[0]["p_skill"] == 1 / 16

    ev5, acct5 = _one_user([1.0, 2.0, 4.0, 8.0, 16.0])
    out5, _, _ = run_sign_randomization_core(
        ev5, acct5, np.random.default_rng(1), 100, exact_max_events=12
    )
    assert out5.iloc[0]["p_skill"] == 1 / 32


def test_monte_carlo_add_one_never_returns_zero():
    ev, acct = _one_user([float(2**i) for i in range(13)])
    out, _, _ = run_sign_randomization_core(
        ev, acct, np.random.default_rng(7), 100, exact_max_events=12
    )
    row = out.iloc[0]
    assert row["pvalue_method"] == "monte_carlo_add_one"
    assert row["p_skill"] >= 1 / 101
    assert row["p_loss"] >= 1 / 101


def test_bh_and_storey_helpers():
    p = np.array([0.001, 0.01, 0.20, 0.80])
    assert benjamini_hochberg(p, 0.05).tolist() == [True, True, False, False]
    assert 0 <= storey_pi0(p) <= 1


def test_discrete_null_rejection_probability():
    assert null_rejection_probability(4) == 0.0
    assert null_rejection_probability(5) == 1 / 32


def test_week_index_is_not_clamped_into_test():
    week = 604800
    assert week_index_for_ts(0, 0) == 0
    assert week_index_for_ts(3 * week, 0) == 3
    assert week_index_for_ts(4 * week, 0) == 4


def test_resolved_universe_requires_event_end_by_cutoff():
    con = duckdb.connect()
    con.execute(
        """
        CREATE TABLE markets AS SELECT * FROM (VALUES
          ('harris', 'pres', '2024-11-04T12:00:00Z'),
          ('coin', 'coin_ev', '2024-11-04T12:00:00Z'),
          ('later', 'later_ev', '2024-11-20T00:00:00Z')
        ) t(market_id, event_id, end_date);
        CREATE TABLE events AS SELECT * FROM (VALUES
          ('pres', '2024-11-05T12:00:00Z'),
          ('coin_ev', '2024-11-04T12:00:00Z'),
          ('later_ev', '2024-11-20T00:00:00Z')
        ) t(event_id, end_date);
        CREATE VIEW market_resolution AS
        SELECT m.market_id,
          GREATEST(epoch(TRY_CAST(m.end_date AS TIMESTAMPTZ)),
                   epoch(TRY_CAST(e.end_date AS TIMESTAMPTZ))) AS scheduled_end_ts
        FROM markets m LEFT JOIN events e ON e.event_id = m.event_id;
        CREATE TABLE outcome_tokens AS SELECT * FROM (VALUES
          ('a', 'harris', 1.0), ('b', 'harris', 0.0),
          ('c', 'coin', 1.0), ('d', 'coin', 0.0),
          ('e', 'later', NULL)
        ) t(token_id, market_id, resolved_payout);
        """
    )
    cutoff = int(pd.Timestamp("2024-11-04T19:08:45Z").timestamp())
    kept = con.execute(
        f"SELECT market_id FROM markets m WHERE {resolved_by_cutoff_sql('m', str(cutoff))}"
    ).fetchall()
    assert kept == [("coin",)]


def test_nan_pnl_is_unscorable_not_skilled():
    ev, acct = _one_user([1.0, float("nan"), 4.0, 8.0, 16.0])
    out, _, _ = run_sign_randomization_core(
        ev, acct, np.random.default_rng(1), 100, exact_max_events=12
    )
    row = out.iloc[0]
    assert bool(row["unscorable"])
    assert pd.isna(row["p_skill"])


def test_identity_holds_with_nonzero_fees():
    ev = pd.DataFrame(
        {
            "user_id": ["u"] * 5,
            "event_id": [f"e{i}" for i in range(5)],
            "event_pnl": [1.0, 2.0, 4.0, 8.0, 16.0],
            "event_fees": [0.1, 0.2, 0.4, 0.8, 1.6],
        }
    )
    acct = pd.DataFrame(
        {"user_id": ["u"], "total_pnl": [31.0], "n_trades": [10], "n_events": [5]}
    )
    out, _, _ = run_sign_randomization_core(
        ev, acct, np.random.default_rng(1), 100, exact_max_events=12
    )
    assert not bool(out.iloc[0]["unscorable"])
    assert out.iloc[0]["p_skill"] == 1 / 32


def test_fewer_than_five_events_are_excluded():
    from src.analysis.classification import assign_group

    row = pd.Series(
        {"n_trades": 40, "n_events_sim": 3, "n_events": 3, "realized_pnl": 100.0,
         "p_skill": 0.0, "p_loss": 1.0, "unscorable": False}
    )
    assert assign_group(row) == "Excluded"
    row["n_events_sim"] = 6
    assert assign_group(row) == "Skilled_Winner"


def test_calibrated_null_does_not_reward_favorite_streaks():
    def fills(user: str, price: float) -> pd.DataFrame:
        n = 12
        return pd.DataFrame(
            {
                "order_type": ["taker"] * n,
                "user_id": [user] * n,
                "asset": [f"{user}_a{i}" for i in range(n)],
                "signed_shares": [100.0] * n,
                "signed_usdc": [100.0 * price] * n,
                "payout": [1.0] * n,
                "fee": [0.0] * n,
            }
        )

    f = pd.concat([fills("fav", 0.95), fills("coin", 0.50)], ignore_index=True)
    out = _calibrated_null(f, ["fav", "coin"], 4000, 3).set_index("user_id")
    assert abs(out.loc["fav", "p_calibrated"] - 0.95**12) < 0.05
    assert out.loc["coin", "p_calibrated"] < 0.002
    assert abs(out.loc["fav", "null_expected_pnl"]) < 1e-6


def test_omitted_complete_set_mint_is_settlement_pnl_neutral():
    # Mint YES+NO for $1, sell YES at p, and retain NO. The fill-only short
    # position formula equals the full cash ledger for either resolution.
    price = 0.63
    for yes_payout in (0.0, 1.0):
        no_payout = 1.0 - yes_payout
        actual = -1.0 + price + no_payout
        fill_only = price - yes_payout
        assert actual == fill_only
