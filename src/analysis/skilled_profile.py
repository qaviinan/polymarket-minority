"""Descriptive profile of Skilled accounts versus other groups (Stage 1 FDR labels).

Covers market themes, trade direction, volume/PnL, price buckets, sell inventory,
category concentration, cross-account agreement on shared markets/events, and a
top-10 versus rest comparison. Restricted to markets resolved by the fill cutoff.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import numpy as np
import pandas as pd

from src.analysis.event_grouping import register_event_views, resolved_by_cutoff_sql
from src.analysis.multiple_testing import benjamini_hochberg
from src.analysis.partition import register_order_flow_views
from src.lib.debug import debug_step
from src.lib.duck import get_duck

from src.lib.period import logs_path

CACHE_PATH = logs_path("skilled_profile", "fills_cache.parquet")
LABEL_PATH = logs_path("multiple_testing", "account_classification_primary_fdr.parquet")
GROUP_COL = os.environ.get("SKILLED_PROFILE_LABEL_COL", "trader_group_fdr_q05").strip()
OUT_DIR = Path(os.environ.get("SKILLED_PROFILE_OUT_DIR", str(logs_path("skilled_profile"))).strip())
GROUPS = ["Skilled_Winner", "Lucky_Winner", "Unlucky_Loser", "Unskilled_Loser"]
PRICE_BINS = [0.0, 0.10, 0.25, 0.50, 0.75, 0.90, 1.0001]
PRICE_LABELS = ["<0.10", "0.10-0.25", "0.25-0.50", "0.50-0.75", "0.75-0.90", ">=0.90"]

ELECTION_OUTCOME_RE = re.compile(
    r"presidential election winner|popular vote|electoral college|which party wins|"
    r"win the presidency|senate control|house control|balance of power|"
    r"who will win (pennsylvania|michigan|wisconsin|georgia|arizona|nevada|"
    r"north carolina|ohio|texas|florida|minnesota|virginia|new hampshire|iowa|maine|"
    r"nebraska|new mexico|alaska)|presidential election$|senate election winner|"
    r"house election winner|governor election winner|margin of victory"
)
US_POLITICS_CATS = {
    "trump", "kamala", "vance", "us-election", "politics", "republican-party",
    "presidential-election", "2024-presidential-election", "uptspt-senate",
    "pennsylvania", "nevada", "georgia", "polling", "debate", "elections", "538",
    "us-politics", "democratic-party", "biden", "walz",
}
US_POLITICS_RE = re.compile(
    r"trump|harris|kamala|biden|walz|vance|debate|rally|rogan|polling|senate|governor|"
    r"congress|election day|democratic|republican|white house|cabinet|rfk"
)
CRYPTO_CATS = {
    "crypto", "btc", "bitcoin", "ethereum", "crypto-prices", "solana", "eth",
    "memecoins", "airdrops", "doge", "xrp",
}
CRYPTO_RE = re.compile(r"bitcoin|ethereum|solana|crypto|\bbtc\b|\beth\b|satoshi|memecoin|token|airdrop")
GEO_CATS = {
    "israel", "iran", "missile", "middle-east", "ukraine", "gaza", "geopolitics",
    "russia", "china", "lebanon", "hezbollah", "world",
}
GEO_RE = re.compile(r"israel|iran|ukraine|russia|gaza|hezbollah|netanyahu|missile|china|taiwan|kursk|lebanon")
SPORTS_CATS = {
    "sports", "epl", "esports", "games", "ufc", "nfl", "mlb", "soccer", "football",
    "chess", "todays-sports", "major-league-baseball", "nba", "cfb", "wnba", "uefa",
    "cs2", "baseball", "counter-strike", "nhl", "f1", "tennis", "golf", "boxing",
    "league-of-legends", "dota-2", "valorant", "mma", "basketball", "hockey",
}
SPORTS_RE = re.compile(
    r" vs\.? | v\. |nfl|nba|mlb|nhl|ufc|premier league|champions league|grand prix|"
    r"world series|super bowl|ballon d'or|worlds winner|league of legends|counter-strike"
)
GLOBAL_ELECTION_CATS = {"global-elections", "brazil", "canada", "uk", "japan", "mexico"}
POP_CATS = {
    "pop-culture", "movies", "box-office", "tweets-markets", "elon-musk", "twitter",
    "music", "celebrities", "awards", "youtube", "tiktok", "mention-markets",
}
MACRO_CATS = {"unemployment", "economy", "fed", "inflation", "business", "stocks", "finance"}
MACRO_RE = re.compile(r"fed |interest rate|unemployment|inflation|cpi|jobs report|gdp|s&p|nasdaq|stock")
WEATHER_CATS = {"helene", "weather", "hurricane", "science", "climate", "space"}


def classify_theme(category: str | None, title: str | None, question: str | None) -> str:
    c = (category or "").strip().lower()
    t = f"{title or ''} | {question or ''}".lower()
    if ELECTION_OUTCOME_RE.search(t):
        return "US election outcome"
    if c in CRYPTO_CATS:
        return "Crypto"
    if c in US_POLITICS_CATS:
        return "US campaign & politics props"
    if c in GEO_CATS:
        return "Geopolitics"
    if c in SPORTS_CATS:
        return "Sports & esports"
    if c in GLOBAL_ELECTION_CATS:
        return "Non-US elections & politics"
    if c in MACRO_CATS:
        return "Economy & markets"
    if c in WEATHER_CATS:
        return "Weather & science"
    if US_POLITICS_RE.search(t):
        return "US campaign & politics props"
    if CRYPTO_RE.search(t):
        return "Crypto"
    if GEO_RE.search(t):
        return "Geopolitics"
    if SPORTS_RE.search(t):
        return "Sports & esports"
    if "election" in t or "mayor" in t or "prime minister" in t:
        return "Non-US elections & politics"
    if MACRO_RE.search(t):
        return "Economy & markets"
    if c in POP_CATS:
        return "Pop culture & media"
    return "Other"


def _load_fills(con, labels: pd.DataFrame, refresh: bool) -> pd.DataFrame:
    cache = CACHE_PATH
    if cache.exists() and not refresh:
        return pd.read_parquet(cache)
    cache.parent.mkdir(parents=True, exist_ok=True)
    users = labels.loc[labels[GROUP_COL].isin(GROUPS), ["user_id"]]
    con.register("_users", users)
    cutoff = int(con.execute("SELECT MAX(CAST(block_timestamp AS BIGINT)) FROM takers").fetchone()[0])
    fills = con.execute(
        f"""
        SELECT lower(o.order_type) AS order_type, o.user_id,
          CAST(o.block_timestamp AS BIGINT) AS ts, o.transaction_hash,
          CAST(o.asset AS VARCHAR) AS asset, m.market_id, m.event_id,
          CASE WHEN CAST(o.asset AS VARCHAR) = CAST(m.yes_token_id AS VARCHAR) THEN 1 ELSE 0 END AS is_yes,
          upper(o.side) AS side, o.price, o.amount_usdc, o.amount_shares,
          COALESCE(o.fee, 0) AS fee, ot.resolved_payout AS payout,
          epoch(TRY_CAST(m.end_date AS TIMESTAMPTZ)) AS end_ts
        FROM order_filled o
        JOIN _users u ON u.user_id = o.user_id
        JOIN outcome_tokens ot ON CAST(ot.token_id AS VARCHAR) = CAST(o.asset AS VARCHAR)
        JOIN markets m ON m.market_id = ot.market_id
        WHERE {resolved_by_cutoff_sql("m", str(cutoff))}
        """
    ).df()
    con.unregister("_users")
    fills.to_parquet(cache, index=False)
    return fills


def _enrich(fills: pd.DataFrame, meta: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    f = fills.merge(meta, on="market_id", how="left")
    f = f.merge(labels[["user_id", GROUP_COL]].rename(columns={GROUP_COL: "group"}), on="user_id")
    buy = f["side"].eq("BUY")
    signed_shares = np.where(buy, f["amount_shares"], -f["amount_shares"])
    signed_usdc = np.where(buy, f["amount_usdc"], -f["amount_usdc"])
    f["signed_shares"] = signed_shares
    f["signed_usdc"] = signed_usdc
    f["pnl"] = signed_shares * f["payout"] - signed_usdc - f["fee"]
    f["aligned"] = (buy & (f["payout"] > 0.5)) | (~buy & (f["payout"] < 0.5))
    f["yes_dir_usdc"] = np.where(f["is_yes"].eq(1), signed_usdc, -signed_usdc)
    f["price_bucket"] = pd.cut(f["price"], PRICE_BINS, labels=PRICE_LABELS, right=False)
    f["hours_to_end"] = (f["end_ts"] - f["ts"]) / 3600.0
    return f


def _sell_inventory(f: pd.DataFrame) -> pd.DataFrame:
    """Share of taker SELL shares not covered by prior in-window buys (taker or maker)."""
    g = f.sort_values(["user_id", "asset", "ts"], kind="mergesort")
    prior = g.groupby(["user_id", "asset"])["signed_shares"].cumsum() - g["signed_shares"]
    is_sell = g["side"].eq("SELL") & g["order_type"].eq("taker")
    covered = np.minimum(g["amount_shares"], np.maximum(prior, 0.0))
    g = g.assign(
        sell_shares=np.where(is_sell, g["amount_shares"], 0.0),
        uncovered_sell_shares=np.where(is_sell, g["amount_shares"] - covered, 0.0),
    )
    return (
        g.groupby("user_id")[["sell_shares", "uncovered_sell_shares"]].sum().reset_index()
    )


def _account_table(f: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    t = f[f["order_type"].eq("taker")]
    mk = f[f["order_type"].eq("maker")]
    vol = t["amount_usdc"]
    t = t.assign(
        buy_usdc=np.where(t["side"].eq("BUY"), vol, 0.0),
        yes_usdc=np.where(t["is_yes"].eq(1), vol, 0.0),
        aligned_usdc=np.where(t["aligned"], vol, 0.0),
        election_usdc=np.where(t["is_us_election"], vol, 0.0),
        election_pnl=np.where(t["is_us_election"], t["pnl"], 0.0),
        pres_usdc=np.where(t["is_pres_winner"], vol, 0.0),
        pres_pnl=np.where(t["is_pres_winner"], t["pnl"], 0.0),
        fav_buy_usdc=np.where(t["side"].eq("BUY") & (t["price"] >= 0.5), vol, 0.0),
        hours_x_usdc=t["hours_to_end"].clip(lower=0) * vol,
    )
    acc = t.groupby("user_id").agg(
        group=("group", "first"),
        taker_volume=("amount_usdc", "sum"),
        taker_pnl=("pnl", "sum"),
        n_fills=("amount_usdc", "size"),
        n_markets=("market_id", "nunique"),
        n_events=("event_id", "nunique"),
        n_themes=("theme", "nunique"),
        buy_usdc=("buy_usdc", "sum"),
        yes_usdc=("yes_usdc", "sum"),
        aligned_usdc=("aligned_usdc", "sum"),
        election_usdc=("election_usdc", "sum"),
        election_pnl=("election_pnl", "sum"),
        pres_usdc=("pres_usdc", "sum"),
        pres_pnl=("pres_pnl", "sum"),
        fav_buy_usdc=("fav_buy_usdc", "sum"),
        hours_x_usdc=("hours_x_usdc", "sum"),
        first_ts=("ts", "min"),
        last_ts=("ts", "max"),
    )
    acc["buy_share"] = acc["buy_usdc"] / acc["taker_volume"]
    acc["yes_token_share"] = acc["yes_usdc"] / acc["taker_volume"]
    acc["aligned_share"] = acc["aligned_usdc"] / acc["taker_volume"]
    acc["election_volume_share"] = acc["election_usdc"] / acc["taker_volume"]
    acc["pres_winner_volume_share"] = acc["pres_usdc"] / acc["taker_volume"]
    acc["roi"] = acc["taker_pnl"] / acc["taker_volume"]
    acc["avg_fill_usdc"] = acc["taker_volume"] / acc["n_fills"]
    acc["vw_hours_to_end"] = acc["hours_x_usdc"] / acc["taker_volume"]
    acc["active_days"] = (acc["last_ts"] - acc["first_ts"]) / 86400.0

    theme_vol = t.pivot_table(index="user_id", columns="theme", values="amount_usdc", aggfunc="sum", fill_value=0.0)
    shares = theme_vol.div(theme_vol.sum(axis=1), axis=0)
    acc["theme_hhi"] = (shares**2).sum(axis=1)
    acc["top_theme"] = shares.idxmax(axis=1)
    acc["top_theme_share"] = shares.max(axis=1)

    ev = t.groupby(["user_id", "event_id"])["pnl"].sum().abs().rename("abs_pnl").reset_index()
    ev["share"] = ev["abs_pnl"] / ev.groupby("user_id")["abs_pnl"].transform("sum").replace(0, np.nan)
    acc["max_event_abs_pnl_share"] = ev.groupby("user_id")["share"].max()
    acc["event_pnl_hhi"] = (ev.assign(sq=ev["share"] ** 2)).groupby("user_id")["sq"].sum()

    maker = mk.groupby("user_id").agg(maker_volume=("amount_usdc", "sum"), maker_pnl=("pnl", "sum"))
    acc = acc.join(maker, how="left").fillna({"maker_volume": 0.0, "maker_pnl": 0.0})
    acc["maker_volume_share"] = acc["maker_volume"] / (acc["maker_volume"] + acc["taker_volume"])

    inv = _sell_inventory(f).set_index("user_id")
    acc = acc.join(inv, how="left")
    acc["uncovered_sell_share"] = acc["uncovered_sell_shares"] / acc["sell_shares"].replace(0, np.nan)
    acc = acc.join(labels.set_index("user_id")[["realized_pnl", "p_skill", "n_events_sim"]], how="left")
    return acc.reset_index()


def _group_summary(acc: pd.DataFrame, f: pd.DataFrame, group_col: str = "group") -> pd.DataFrame:
    t = f[f["order_type"].eq("taker")]
    rows = []
    total_pos_pnl = float(acc.loc[acc["taker_pnl"] > 0, "taker_pnl"].sum())
    for g, a in acc.groupby(group_col):
        ft = t[t["user_id"].isin(a["user_id"])]
        vol = float(ft["amount_usdc"].sum())
        sell_shares = float(a["sell_shares"].sum())
        rows.append(
            {
                group_col: g,
                "n_accounts": len(a),
                "taker_volume": vol,
                "taker_pnl": float(ft["pnl"].sum()),
                "pnl_share_of_all_positive_pnl": float(a.loc[a["taker_pnl"] > 0, "taker_pnl"].sum()) / total_pos_pnl,
                "roi": float(ft["pnl"].sum()) / vol,
                "median_account_volume": float(a["taker_volume"].median()),
                "median_account_pnl": float(a["taker_pnl"].median()),
                "median_account_roi": float(a["roi"].median()),
                "median_fills": float(a["n_fills"].median()),
                "median_markets": float(a["n_markets"].median()),
                "median_events": float(a["n_events"].median()),
                "buy_share_vol": float(ft.loc[ft["side"].eq("BUY"), "amount_usdc"].sum()) / vol,
                "median_account_buy_share": float(a["buy_share"].median()),
                "yes_token_share_vol": float(ft.loc[ft["is_yes"].eq(1), "amount_usdc"].sum()) / vol,
                "aligned_share_vol": float(ft.loc[ft["aligned"], "amount_usdc"].sum()) / vol,
                "median_account_aligned_share": float(a["aligned_share"].median()),
                "us_election_share_vol": float(ft.loc[ft["is_us_election"], "amount_usdc"].sum()) / vol,
                "pres_winner_share_vol": float(ft.loc[ft["is_pres_winner"], "amount_usdc"].sum()) / vol,
                "us_election_share_pnl": float(ft.loc[ft["is_us_election"], "pnl"].sum()) / float(ft["pnl"].sum()),
                "pres_winner_share_pnl": float(ft.loc[ft["is_pres_winner"], "pnl"].sum()) / float(ft["pnl"].sum()),
                "median_theme_hhi": float(a["theme_hhi"].median()),
                "median_n_themes": float(a["n_themes"].median()),
                "median_max_event_pnl_share": float(a["max_event_abs_pnl_share"].median()),
                "vw_hours_to_end": float((ft["hours_to_end"].clip(lower=0) * ft["amount_usdc"]).sum()) / vol,
                "median_avg_fill_usdc": float(a["avg_fill_usdc"].median()),
                "maker_volume_share": float(a["maker_volume"].sum()) / float(a["maker_volume"].sum() + vol),
                "uncovered_sell_share": float(a["uncovered_sell_shares"].sum()) / sell_shares if sell_shares else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def _theme_table(f: pd.DataFrame, group_col: str = "group") -> pd.DataFrame:
    t = f[f["order_type"].eq("taker")]
    th = t.groupby([group_col, "theme"]).agg(
        volume=("amount_usdc", "sum"), pnl=("pnl", "sum"), n_users=("user_id", "nunique"),
        n_markets=("market_id", "nunique"),
    ).reset_index()
    th["volume_share"] = th["volume"] / th.groupby(group_col)["volume"].transform("sum")
    th["roi"] = th["pnl"] / th["volume"]
    return th.sort_values([group_col, "volume"], ascending=[True, False])


def _direction_table(f: pd.DataFrame, group_col: str = "group") -> pd.DataFrame:
    t = f[f["order_type"].eq("taker")].assign(
        token=lambda d: np.where(d["is_yes"].eq(1), "YES", "NO"),
        aligned_usdc=lambda d: np.where(d["aligned"], d["amount_usdc"], 0.0),
    )
    d = t.groupby([group_col, "side", "token"]).agg(
        volume=("amount_usdc", "sum"), pnl=("pnl", "sum"), n_fills=("amount_usdc", "size"),
        aligned_vol=("aligned_usdc", "sum"),
    ).reset_index()
    d["volume_share"] = d["volume"] / d.groupby(group_col)["volume"].transform("sum")
    d["aligned_share"] = d["aligned_vol"] / d["volume"]
    d["roi"] = d["pnl"] / d["volume"]
    return d


def _price_table(f: pd.DataFrame, group_col: str = "group") -> pd.DataFrame:
    t = f[f["order_type"].eq("taker")]
    p = t.groupby([group_col, "side", "price_bucket"], observed=True).agg(
        volume=("amount_usdc", "sum"), pnl=("pnl", "sum")
    ).reset_index()
    p["volume_share"] = p["volume"] / p.groupby(group_col)["volume"].transform("sum")
    p["pnl_share"] = p["pnl"] / p.groupby(group_col)["pnl"].transform("sum")
    p["roi"] = p["pnl"] / p["volume"]
    return p


def _pairwise(con, f: pd.DataFrame, users: list[str], min_markets: int, exclude_events: set[str]) -> pd.DataFrame:
    t = f[f["order_type"].eq("taker") & f["user_id"].isin(users) & ~f["event_id"].isin(exclude_events)]
    um = t.groupby(["user_id", "market_id"])["yes_dir_usdc"].sum().reset_index()
    um = um[um["yes_dir_usdc"].abs() > 1e-9]
    yes_won = _yes_won(f)
    um["correct"] = (um["yes_dir_usdc"] > 0) == um["market_id"].map(yes_won).fillna(False)
    hit = um.groupby("user_id")["correct"].mean()
    ue = t.groupby(["user_id", "event_id"])["pnl"].sum().reset_index()
    con.register("_um", um)
    con.register("_ue", ue)
    pm = con.execute(
        f"""
        SELECT a.user_id AS user_a, b.user_id AS user_b,
          COUNT(*) AS shared_markets,
          AVG(CASE WHEN sign(a.yes_dir_usdc) = sign(b.yes_dir_usdc) THEN 1.0 ELSE 0.0 END) AS agreement,
          corr(a.yes_dir_usdc, b.yes_dir_usdc) AS flow_corr
        FROM _um a JOIN _um b ON a.market_id = b.market_id AND a.user_id < b.user_id
        GROUP BY 1, 2 HAVING COUNT(*) >= {min_markets}
        """
    ).df()
    pe = con.execute(
        """
        SELECT a.user_id AS user_a, b.user_id AS user_b,
          COUNT(*) AS shared_events, corr(a.pnl, b.pnl) AS event_pnl_corr
        FROM _ue a JOIN _ue b ON a.event_id = b.event_id AND a.user_id < b.user_id
        GROUP BY 1, 2 HAVING COUNT(*) >= 5
        """
    ).df()
    con.unregister("_um")
    con.unregister("_ue")
    ra = pm["user_a"].map(hit)
    rb = pm["user_b"].map(hit)
    pm["expected_agreement_indep"] = ra * rb + (1 - ra) * (1 - rb)
    return pm.merge(pe, on=["user_a", "user_b"], how="outer")


def _yes_won(f: pd.DataFrame) -> pd.Series:
    yes = f[f["is_yes"].eq(1)].groupby("market_id")["payout"].first()
    no = 1.0 - f[f["is_yes"].eq(0)].groupby("market_id")["payout"].first()
    return yes.combine_first(no) > 0.5


def _co_timing(con, f: pd.DataFrame, users: list[str], window_s: int) -> pd.DataFrame:
    t = f[f["order_type"].eq("taker") & f["user_id"].isin(users)][
        ["user_id", "asset", "side", "ts", "transaction_hash"]
    ].copy()
    t["bucket"] = t["ts"] // window_s
    con.register("_ct", t)
    out = con.execute(
        """
        SELECT a.user_id AS user_a, b.user_id AS user_b,
          COUNT(*) AS co_timed_fills,
          SUM(CASE WHEN a.transaction_hash = b.transaction_hash THEN 1 ELSE 0 END) AS same_tx_fills
        FROM _ct a JOIN _ct b
          ON a.asset = b.asset AND a.side = b.side AND a.bucket = b.bucket
         AND a.user_id < b.user_id
        GROUP BY 1, 2
        """
    ).df()
    con.unregister("_ct")
    return out


def _consensus(f: pd.DataFrame) -> pd.DataFrame:
    """Per group: does the group's net YES flow in each market point at the outcome?"""
    t = f[f["order_type"].eq("taker")]
    gm = t.groupby(["group", "market_id"]).agg(
        net_yes=("yes_dir_usdc", "sum"), vol=("amount_usdc", "sum"), n_users=("user_id", "nunique")
    ).reset_index()
    gm["yes_won"] = gm["market_id"].map(_yes_won(f)).fillna(False)
    gm = gm[gm["net_yes"].abs() > 1e-9]
    gm["correct"] = (gm["net_yes"] > 0) == gm["yes_won"]
    rows = []
    for g, d in gm.groupby("group"):
        multi = d[d["n_users"] >= 3]
        rows.append(
            {
                "group": g,
                "markets": len(d),
                "net_flow_correct_rate": float(d["correct"].mean()),
                "vol_weighted_correct_rate": float((d["correct"] * d["vol"]).sum() / d["vol"].sum()),
                "markets_3plus_users": len(multi),
                "correct_rate_3plus_users": float(multi["correct"].mean()) if len(multi) else float("nan"),
            }
        )
    return pd.DataFrame(rows)


def _calibrated_null(
    f: pd.DataFrame,
    users: list[str],
    n_iter: int,
    seed: int,
    chunk: int = 1000,
    refine_below: float = 0.01,
    refine_iter: int = 200_000,
) -> pd.DataFrame:
    """Price-calibrated null: a token pays 1 with probability equal to its entry price.

    Tokens in the same market are one draw: YES wins with the YES entry price and NO
    wins when YES does not. Positions without a market id stay independent.
    """
    t = f[f["order_type"].eq("taker") & f["user_id"].isin(users)]
    agg = {
        "shares": ("signed_shares", "sum"),
        "cost": ("signed_usdc", "sum"),
        "payout": ("payout", "first"),
        "fee": ("fee", "sum"),
    }
    if "market_id" in t.columns:
        agg["market_id"] = ("market_id", "first")
    if "is_yes" in t.columns:
        agg["is_yes"] = ("is_yes", "first")
    pos = t.groupby(["user_id", "asset"]).agg(**agg).reset_index()
    pos["random"] = pos["shares"].abs() > 1e-9
    pos["q"] = np.clip(
        np.where(pos["random"], pos["cost"] / pos["shares"].where(pos["random"], 1.0), 0.5),
        0.001, 0.999,
    )
    rng = np.random.default_rng(seed)
    rows = []
    for uid, g in pos.groupby("user_id", sort=False):
        observed = float((g["shares"] * g["payout"] - g["cost"] - g["fee"]).sum())
        fixed = float((-g.loc[~g["random"], "cost"] - g.loc[~g["random"], "fee"]).sum())
        r = g[g["random"]]
        shares = r["shares"].to_numpy()
        q = r["q"].to_numpy()
        base = fixed - float((r["cost"] + r["fee"]).sum())

        def _payoffs(m: int) -> np.ndarray:
            if "market_id" not in r.columns or r["market_id"].isna().all():
                return (rng.random((m, len(q))) < q).astype(float) @ shares
            is_yes = r["is_yes"].to_numpy() if "is_yes" in r.columns else np.ones(len(r))
            pays = np.zeros((m, len(r)))
            markets = r["market_id"].to_numpy()
            for mid in pd.unique(markets):
                ix = np.flatnonzero(markets == mid)
                yes = ix[is_yes[ix] == 1]
                q_m = float(np.mean(q[yes])) if len(yes) else float(1.0 - np.mean(q[ix]))
                q_m = float(np.clip(q_m, 0.001, 0.999))
                win = rng.random(m) < q_m
                for i in ix:
                    pays[:, i] = win if is_yes[i] == 1 else ~win
            return pays @ shares

        def exceedances(draws: int) -> int:
            hits = 0
            done = 0
            while done < draws:
                m = min(chunk, draws - done)
                hits += int((base + _payoffs(m) >= observed - 1e-9).sum())
                done += m
            return hits

        draws = n_iter
        p = (1 + exceedances(draws)) / (1 + draws)
        if p < refine_below:
            draws = refine_iter
            p = (1 + exceedances(draws)) / (1 + draws)
        rows.append(
            {
                "user_id": uid,
                "observed_pnl": observed,
                "null_expected_pnl": base + float((shares * q).sum()),
                "n_positions": int(len(r)),
                "n_draws": draws,
                "p_calibrated": p,
            }
        )
    return pd.DataFrame(rows)


def _plots(themes: pd.DataFrame, prices: pd.DataFrame, cohorts: list[str]) -> None:
    import matplotlib.pyplot as plt

    tv = themes.pivot_table(index="cohort", columns="theme", values="volume_share", fill_value=0.0)
    tv = tv.reindex([c for c in cohorts if c in tv.index])
    order = tv.sum().sort_values(ascending=False).index
    ax = tv[order].plot(kind="barh", stacked=True, figsize=(10, 4.5), colormap="tab20")
    ax.set_xlabel("Share of taker volume")
    ax.set_title("Market-theme mix by cohort (restricted universe)")
    ax.legend(bbox_to_anchor=(1.01, 1), loc="upper left", fontsize=8)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "theme_mix_by_cohort.png", dpi=130)
    plt.close()

    buys = prices[prices["side"].eq("BUY")]
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), sharey=True)
    for ax, col, title in (
        (axes[0], "volume_share", "Share of cohort taker volume"),
        (axes[1], "pnl_share", "Share of cohort net PnL"),
    ):
        pv = buys.pivot_table(index="price_bucket", columns="cohort", values=col, observed=True)
        pv = pv[[c for c in cohorts if c in pv.columns]]
        pv.plot(kind="bar", ax=ax, width=0.8)
        ax.set_title(f"BUY fills: {title}")
        ax.set_xlabel("Execution price")
        ax.axhline(0, color="gray", lw=0.5)
        ax.legend(fontsize=7)
    plt.tight_layout()
    plt.savefig(OUT_DIR / "buy_price_buckets_by_cohort.png", dpi=130)
    plt.close()


def _pair_summary(pairs: pd.DataFrame, label: str) -> dict:
    pm = pairs.dropna(subset=["agreement"])
    pe = pairs.dropna(subset=["event_pnl_corr"])
    return {
        "cohort": label,
        "pairs_with_shared_markets": int(len(pm)),
        "mean_agreement": float(pm["agreement"].mean()) if len(pm) else float("nan"),
        "median_agreement": float(pm["agreement"].median()) if len(pm) else float("nan"),
        "share_pairs_agreement_ge_0.9": float((pm["agreement"] >= 0.9).mean()) if len(pm) else float("nan"),
        "mean_expected_agreement_indep": float(pm["expected_agreement_indep"].mean()) if len(pm) else float("nan"),
        "mean_excess_agreement": float((pm["agreement"] - pm["expected_agreement_indep"]).mean()) if len(pm) else float("nan"),
        "median_flow_corr": float(pm["flow_corr"].median()) if len(pm) else float("nan"),
        "pairs_with_5_shared_events": int(len(pe)),
        "median_event_pnl_corr": float(pe["event_pnl_corr"].median()) if len(pe) else float("nan"),
        "share_event_pnl_corr_gt_0.5": float((pe["event_pnl_corr"] > 0.5).mean()) if len(pe) else float("nan"),
    }


def main() -> None:
    if not LABEL_PATH.exists():
        raise RuntimeError("Run coverage_audit and multiple_testing first (Stage 1 FDR labels missing)")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    refresh = os.environ.get("SKILLED_PROFILE_REFRESH", "").strip().lower() in {"1", "true", "yes"}
    top_n = int(os.environ.get("SKILLED_PROFILE_TOP_N", "10"))
    min_shared = int(os.environ.get("SKILLED_PROFILE_MIN_SHARED_MARKETS", "3"))
    co_window = int(os.environ.get("SKILLED_PROFILE_COTIME_WINDOW_S", "10"))
    seed = int(os.environ.get("SKILLED_PROFILE_SEED", "42"))
    calib_iter = int(os.environ.get("SKILLED_PROFILE_CALIBRATED_ITER", "5000"))

    with debug_step("skilled_profile") as dbg:
        con = get_duck()
        register_order_flow_views(con)
        register_event_views(con)
        labels = pd.read_parquet(LABEL_PATH)
        dbg.log("meta", "label_path", str(LABEL_PATH))
        dbg.log("meta", "group_column", GROUP_COL)
        dbg.log("meta", "label_scope", ",".join(sorted(labels["label_scope"].astype(str).unique())))

        meta = con.execute(
            """
            SELECT m.market_id, m.question, e.title AS event_title, e.category
            FROM markets m LEFT JOIN events e ON e.event_id = m.event_id
            """
        ).df()
        meta["theme"] = [
            classify_theme(c, t, q)
            for c, t, q in zip(meta["category"], meta["event_title"], meta["question"])
        ]
        meta["is_us_election"] = meta["theme"].isin(["US election outcome", "US campaign & politics props"])
        meta["is_pres_winner"] = meta["event_title"].fillna("").str.contains(
            "Presidential Election Winner 2024", regex=False
        )

        fills = _load_fills(con, labels, refresh)
        dbg.log("ingest", "fills_rows", len(fills))
        f = _enrich(fills, meta[["market_id", "event_title", "category", "theme", "is_us_election", "is_pres_winner"]], labels)
        n_unthemed = int(f["theme"].isna().sum())
        dbg.assertion(
            "theme_coverage",
            "green" if n_unthemed == 0 else "red",
            f"{n_unthemed} fills without market metadata/theme",
        )
        other_share = float(
            f.loc[f["order_type"].eq("taker") & f["theme"].eq("Other"), "amount_usdc"].sum()
            / f.loc[f["order_type"].eq("taker"), "amount_usdc"].sum()
        )
        dbg.log("quality", "other_theme_volume_share", other_share)
        dbg.assertion(
            "theme_other_share",
            "green" if other_share < 0.05 else "yellow",
            f"'Other' theme holds {other_share:.1%} of taker volume",
        )

        acc = _account_table(f, labels)
        pnl_gap = float((acc["taker_pnl"] - acc["realized_pnl"]).abs().max())
        dbg.log("quality", "max_abs_pnl_gap_vs_labels", pnl_gap)
        dbg.assertion(
            "pnl_reconciles_with_labels",
            "green" if pnl_gap < 1.0 else "yellow",
            f"Max |profile taker PnL - label realized_pnl| = {pnl_gap:.4f} USDC",
        )

        skilled = acc[acc["group"].eq("Skilled_Winner")].sort_values("taker_pnl", ascending=False)
        top_ids = set(skilled.head(top_n)["user_id"])
        acc["cohort"] = np.where(
            acc["user_id"].isin(top_ids), f"Skilled_top{top_n}",
            np.where(acc["group"].eq("Skilled_Winner"), "Skilled_rest", acc["group"]),
        )
        f = f.merge(acc[["user_id", "cohort"]], on="user_id", how="left")

        acc.to_parquet(OUT_DIR / "accounts.parquet", index=False)
        group_summary = _group_summary(acc, f, "group")
        cohort_summary = _group_summary(acc, f, "cohort")
        themes = _theme_table(f, "group")
        cohort_themes = _theme_table(f, "cohort")
        direction = _direction_table(f, "group")
        cohort_direction = _direction_table(f, "cohort")
        prices = _price_table(f, "group")
        cohort_prices = _price_table(f, "cohort")
        consensus = _consensus(f)
        for name, df in {
            "group_summary": group_summary,
            "cohort_summary": cohort_summary,
            "theme_by_group": themes,
            "theme_by_cohort": cohort_themes,
            "direction_by_group": direction,
            "direction_by_cohort": cohort_direction,
            "price_by_group": prices,
            "price_by_cohort": cohort_prices,
            "consensus_by_group": consensus,
        }.items():
            df.to_parquet(OUT_DIR / f"{name}.parquet", index=False)
        _plots(
            cohort_themes,
            cohort_prices,
            [f"Skilled_top{top_n}", "Skilled_rest", "Lucky_Winner", "Unlucky_Loser", "Unskilled_Loser"],
        )

        top_cols = [
            "user_id", "taker_pnl", "taker_volume", "roi", "n_fills", "n_markets", "n_events",
            "buy_share", "yes_token_share", "aligned_share", "election_volume_share",
            "pres_winner_volume_share", "pres_pnl", "top_theme", "top_theme_share", "theme_hhi",
            "max_event_abs_pnl_share", "maker_volume_share", "uncovered_sell_share",
            "avg_fill_usdc", "vw_hours_to_end", "active_days", "p_skill", "n_events_sim",
        ]
        skilled[top_cols].head(top_n).to_parquet(OUT_DIR / "top_skilled.parquet", index=False)
        skilled_pnl = float(skilled["taker_pnl"].sum())
        dbg.log("top", "top_n_pnl_share_of_skilled", float(skilled.head(top_n)["taker_pnl"].sum() / skilled_pnl))
        dbg.log("top", "top_n_volume_share_of_skilled", float(skilled.head(top_n)["taker_volume"].sum() / skilled["taker_volume"].sum()))

        skilled_ids = skilled["user_id"].tolist()
        rng = np.random.default_rng(seed)
        lucky_pool = acc[acc["group"].eq("Lucky_Winner") & (acc["n_events_sim"] >= 5)]["user_id"].to_numpy()
        lucky_ids = list(rng.choice(lucky_pool, size=min(len(skilled_ids), len(lucky_pool)), replace=False))
        unlucky_pool = acc[acc["group"].eq("Unlucky_Loser") & (acc["n_events_sim"] >= 5)]["user_id"].to_numpy()
        unlucky_ids = list(rng.choice(unlucky_pool, size=min(len(skilled_ids), len(unlucky_pool)), replace=False))
        pres_events = set(f.loc[f["is_pres_winner"], "event_id"].dropna().unique())

        pair_rows = []
        pair_tables = {}
        for label, ids, excl in (
            ("Skilled", skilled_ids, set()),
            ("Skilled_ex_pres_winner", skilled_ids, pres_events),
            ("Lucky_sample", lucky_ids, set()),
            ("Lucky_sample_ex_pres_winner", lucky_ids, pres_events),
            ("Unlucky_sample", unlucky_ids, set()),
        ):
            pairs = _pairwise(con, f, ids, min_shared, excl)
            pair_tables[label] = pairs
            pair_rows.append(_pair_summary(pairs, label))
        pair_summary = pd.DataFrame(pair_rows)
        pair_summary.to_parquet(OUT_DIR / "pair_summary.parquet", index=False)

        sk_pairs = pair_tables["Skilled"]
        top_pairs = sk_pairs[sk_pairs["user_a"].isin(top_ids) & sk_pairs["user_b"].isin(top_ids)]
        pair_summary_top = pd.DataFrame(
            [_pair_summary(top_pairs, f"Skilled_top{top_n}_within")]
        )
        pair_summary_top.to_parquet(OUT_DIR / "pair_summary_top.parquet", index=False)

        eligible_ids = labels.loc[
            labels[GROUP_COL].isin(GROUPS) & (labels["n_trades"] >= 10) & (labels["n_events_sim"] >= 5),
            "user_id",
        ].tolist()
        cal = _calibrated_null(f, eligible_ids, calib_iter, seed)
        cal = cal.merge(acc[["user_id", "group", "cohort"]], on="user_id", how="left")
        cal["bh_q05_calibrated"] = benjamini_hochberg(cal["p_calibrated"].to_numpy(), 0.05)
        cal.to_parquet(OUT_DIR / "calibrated_null_accounts.parquet", index=False)
        cal_summary = cal.groupby("cohort").agg(
            n_eligible=("user_id", "size"),
            share_p_lt_005=("p_calibrated", lambda s: float((s < 0.05).mean())),
            bh_q05_calibrated=("bh_q05_calibrated", "sum"),
            median_p_calibrated=("p_calibrated", "median"),
            median_null_expected_pnl=("null_expected_pnl", "median"),
        ).reset_index()
        cal_summary.to_parquet(OUT_DIR / "calibrated_null_summary.parquet", index=False)
        winners = cal[cal["observed_pnl"] > 0]
        dbg.log("calibrated", "eligible_accounts", int(len(cal)))
        dbg.log("calibrated", "winners_p_lt_005", int((winners["p_calibrated"] < 0.05).sum()))
        dbg.log("calibrated", "bh_q05_survivors", int(cal["bh_q05_calibrated"].sum()))
        sk_cal = cal[cal["group"].eq("Skilled_Winner")]
        dbg.log("calibrated", "skilled_retained_p_lt_005", float((sk_cal["p_calibrated"] < 0.05).mean()))
        dbg.log("calibrated", "skilled_retained_bh_q05", int(sk_cal["bh_q05_calibrated"].sum()))

        co = _co_timing(con, f, skilled_ids, co_window)
        links = sk_pairs.merge(co, on=["user_a", "user_b"], how="left").fillna(
            {"co_timed_fills": 0, "same_tx_fills": 0}
        )
        candidates = links[
            (links["shared_markets"] >= 10) & (links["agreement"] >= 0.9) & (links["co_timed_fills"] >= 20)
        ].sort_values("co_timed_fills", ascending=False)
        candidates.to_parquet(OUT_DIR / "linked_wallet_candidates.parquet", index=False)
        links.to_parquet(OUT_DIR / "skilled_pairs.parquet", index=False)
        dbg.log("links", "candidate_linked_pairs", int(len(candidates)))
        dbg.log(
            "links",
            "candidate_linked_wallets",
            int(len(set(candidates["user_a"]) | set(candidates["user_b"]))),
        )

        summary = {
            "label_path": str(LABEL_PATH),
            "group_column": GROUP_COL,
            "top_n": top_n,
            "fills_rows": int(len(fills)),
            "other_theme_volume_share": other_share,
            "max_abs_pnl_gap_vs_labels": pnl_gap,
            "n_skilled": int(len(skilled)),
            "top_n_pnl_share_of_skilled": float(skilled.head(top_n)["taker_pnl"].sum() / skilled_pnl),
            "candidate_linked_pairs": int(len(candidates)),
            "calibrated_eligible": int(len(cal)),
            "calibrated_bh_q05_survivors": int(cal["bh_q05_calibrated"].sum()),
            "skilled_calibrated_p_lt_005_share": float((sk_cal["p_calibrated"] < 0.05).mean()),
            "skilled_calibrated_bh_q05": int(sk_cal["bh_q05_calibrated"].sum()),
        }
        (OUT_DIR / "summary.json").write_text(json.dumps(summary, indent=2))
        dbg.log("export", "summary_path", str(OUT_DIR / "summary.json"))


if __name__ == "__main__":
    main()
