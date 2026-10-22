# Methods

## Data

- **2024:** Polymarket `OrderFilled` events on Polygon, stored in Cloudflare R2 as Parquet. The analysis window is Oct 5 – Nov 4, 2024.
- **2026:** the [SII-WANGZJ/Polymarket_data](https://huggingface.co/datasets/SII-WANGZJ/Polymarket_data) release on Hugging Face, Aug 10 – Oct 4, 2026 (`src/data/hf_fills.py`). `scripts/validate_hf_sample.py` compares it row by row with the on-chain fills over a window covered by both.
- **Metadata:** markets, events and payouts come from the Gamma API (`src/data/load_markets.py`).

PnL is scored only on markets whose market *and* parent event ended before the last fill. Otherwise open positions, above all the 2024 presidential market, get scored before the result is known.

## Pipeline

| Step | Module |
|---|---|
| Maker and taker views | `src.analysis.partition` |
| Realised PnL per wallet, settled at payout | `src.analysis.pnl` |
| Event-level sign test | `src.analysis.sign_randomization` |
| Labels | `src.analysis.classification` |
| Resolved-market universe and BH correction | `src.analysis.coverage_audit`, `src.analysis.multiple_testing` |
| Maker + taker + fees net to zero | `src.analysis.market_closure` |
| Price-calibrated null, wallet profiles | `src.analysis.skilled_profile` |
| Favorite calibration (report 3) | `src.analysis.favorite_calibration` |
| Concentrated winners (report 4) | `src.analysis.concentrated_winners` |
| Order imbalance regressions | `src.analysis.oib_regression` |
| Out-of-sample persistence | `src.analysis.event_split_persistence` |

## Skill test

For each taker, PnL is summed by event (an event groups related markets, such as all candidates in one race). Under the null of no skill, each event's direction is flipped with probability ½; fees are paid either way. The p-value is the share of flips that do at least as well as the real PnL. With 12 or fewer events every sign vector is enumerated; above that, 10,000 seeded draws with p = (1 + exceedances) / (1 + draws).

A wallet is eligible with at least 10 trades and 5 events, since with K events the smallest possible p-value is 2⁻ᴷ. Winners with p < 0.05 are Skilled, other winners Lucky; losers split into Unskilled and Unlucky the same way. Benjamini–Hochberg at q = 0.05 is applied across eligible wallets.

## Price-calibrated null

The sign test treats a 95¢ win and a 5¢ loss as equally likely. Twelve straight wins at 95¢ give p = 2⁻¹² under the sign test, but happen 54% of the time by chance. The calibrated null pays each position 1 with probability equal to its entry price, so expected PnL is zero. Each wallet gets 5,000 draws, refined to 200,000 when p < 0.01.

| Group (eligible) | Wallets | Calibrated p < 0.05 | Survive BH |
|---|---:|---:|---:|
| Skilled, top 10 by PnL | 10 | 80% | 3 |
| Skilled, rest | 474 | 4.9% | 5 |
| Lucky | 3,524 | 7.4% | 27 |
| Unlucky | 5,529 | 0.7% | 4 |
| Unskilled | 1,928 | 0.1% | 0 |
| All | 11,465 | 2.9% | 39 |

Positions are drawn independently and traders pay the spread, so this test is slightly conservative.

## Out-of-sample persistence

Events are split 80/20 at random and wallets are labelled on each half separately (5 seeds). P(Skilled on test | Skilled on train) is 14.4% against a 4.7% base rate, a 3.05× lift (range 2.71–3.31×).

## Price discovery

Each market's life is split into bins. Order imbalance by group is regressed on the next bin's change in price and in absolute pricing error, with market and bin fixed effects and standard errors clustered by market. Labels come from the first three weeks; flow is measured in the fourth.

| Regression | Skilled β | p | Lucky β | Unlucky β | Unskilled β |
|---|---:|---:|---:|---:|---:|
| `d_err_h1 ~ volume_share_skilled` | +0.0214 | 0.128 | | | |
| `d_err_h5 ~ volume_share_skilled` | +0.0514 | 0.093 | | | |
| `d_err_h1 ~ outcome-aligned OIB` | +0.0472 | 0.0003 | +0.0062 | +0.0150 | +0.0097 |
| `d_price_h1 ~ OIB` | −0.0190 | 0.175 | −0.0053 | −0.0153 | −0.0082 |
| `yes_payout ~ mid + OIB` | +0.0261 | 0.401 | −0.0146 | −0.0384 | +0.0240 |

The signs are the same for every group, which points to bid–ask bounce: without quote data, prices here are execution VWAPs, and buys print near the ask.

## Running it

Needs Python 3.11+ and a Cloudflare R2 bucket. Copy `.env.example` to `.env` and fill in the R2 keys.

```bash
uv sync --extra dev
python -m src.lib.duck --healthcheck
python -m src.data.load_markets
python -m src.analysis.partition
python -m src.analysis.pnl
python -m src.analysis.sign_randomization
python -m src.analysis.classification
python -m src.analysis.coverage_audit
python -m src.analysis.multiple_testing
python -m src.analysis.skilled_profile
```

`bash scripts/run_stage2_analysis.sh` runs the 2026 period. `pytest` runs offline.
