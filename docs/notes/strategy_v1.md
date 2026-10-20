# Strategy v1: data-informed, reward-subsidised market making

[← Docs index](../README.md)

> **Working note.** A strategy design built on the findings, not a tested result.

**Status:** design, 2026-10-05. Nothing is backtested yet; Section 5 is the validation plan.
**Inputs:** Stage 1 results ([v1_pipeline.md](../methods/pipeline.md) §4, §5.8, §5.9), the alt-data
screen ([alt_data_market_screen.md](alt_data_screen.md)), and live Gamma/CLOB snapshots
(`logs/alt_data_screen/raw_events.json`, `logs/alt_data_screen/reward_markets.csv`).

## 1. What the evidence supports (premises, checked)

**P1. "Skilled winners" are mostly heavy-favorite buyers, and favorite buying is not shown to
beat prices.** This is correct in substance, with one correction: the evidence says favorite
buying wins *often*, not that it *pays* after accounting for price.
- FDR Skilled put 88–95% of BUY volume at ≥90¢ (§4, §5.7).
- Under the price-calibrated null (§5.8), only 8 of 484 FDR Skilled survive BH, and 2.9% of
  all 11,465 eligible accounts reach p<0.05. That is *below* the 5% expected by chance.
  Consistent favorite buying produces a high win rate, but no evidence of excess return over
  the entry price.
- The real edge sits in a handful of wallets (8–39 depending on definition): live in-event
  mention traders (37% of Skilled PnL comes from fills after the listed end), single-episode
  geopolitical calls, and near-certainty carry (§5.9).
- An unconditional calibration of all taker fills by price bucket has **not** been run, so
  "favorites pay off consistently" (a favorite–longshot bias) remains untested here.

**P2. Makers fund liquid, two-sided markets for rewards.** Correct in direction, but the
"2.5%" figure is wrong. In `prediction_markets_institutional_map.md`, 2.5% is the Skilled
*trader share*, not a reward rate. Actual maker income has three parts (Polymarket docs,
checked 2026-10-05):
- **Liquidity rewards:** a fixed daily USDC pool per market, split by a quadratic score
  `((v−s)/v)²·size` on quotes within `rewardsMaxSpread` of the size-adjusted midpoint,
  sampled every minute. Quotes must be two-sided when the midpoint is <10¢ or >90¢.
  **$161k/day across 18,509 markets.** Concentrated: top 100 = $36k/day, median $3/day.
- **Maker rebates:** 15–25% of taker fees. Taker fee = `C × rate × p × (1−p)`, rate 4–7%
  by category; geopolitics is fee-free. Makers pay no fees.
- **Holding rewards:** on about 0.8% of markets (the "Earn 3.25%" tag).

**P3. Most markets are not tradable, and the last trade price is meaningless.** Confirmed on
70,218 open markets:
- 31% have a one-sided or empty book.
- Of two-sided books: median spread 6¢, 41% wider than 10¢, only 25% at ≤2¢.
- 75% of two-sided markets had **zero** 24h volume.
- 10.5% have a last trade price outside the current bid–ask.

**P4. Cheap data has edges, so work backwards from data.** The approach is right, but the two
examples need care:
- *Spotify valence* (Edmans, Fernandez-Perez, Garel & Indriawan 2022, JFE): a 1-SD rise in
  music sentiment means **+8.1 bp** same-week market return and −7.0 bp the next week, not a
  1-SD move in returns. A 2026 replication finds the reversal **disappears in 2021–2024**.
  The effect is far below Polymarket's taker fee (≈1¢ on a 50¢ contract) on SPY ladders.
- *Weather*: the mood literature finds that **colder** temperatures go with *higher* returns
  (Cao & Wei 2005), the opposite of your claim. The business-activity channel is real but
  works by industry and in both directions: extreme temperatures move earnings in 24 of 59
  industries, and analysts miss it in 7 (Addoum, Ng & Ortiz-Bobea 2023, JFE). On Polymarket,
  weather data's best use is **direct**: markets that resolve on a weather station.

## 2. The strategy in one paragraph

Act as the **market maker, not the taker**, in reward-subsidised markets where cheap public
data gives a better fair value than the crowd. Rewards and rebates pay you to provide
liquidity. The data-driven fair value protects you from the adverse selection that pushes
other makers out. Stage 1 wallet labels tell you whose flow is toxic. The universe filter
keeps you out of dead books. Take liquidity only when the model's edge clears fees plus the
spread.

## 3. Sleeves

### A. Core: weather station markets (daily high/low temperature)

*Why:* this is the largest reward pool of any archetype, **$14.4k/day over 388 markets**
(about $37/market/day, ≈$5M/yr), with a 3¢ median spread and about $450k/day of volume. It
resolves on one NOAA or Wunderground station reading, which free data forecasts well.
Weather fee rate is 5%, with a 25% maker rebate.

- **Fair value:** for each city-day, a bracket probability vector from ensemble numerical
  weather prediction (ECMWF open data, GFS/GEFS, ICON via Open-Meteo). Apply station-specific
  bias correction trained on past forecast-vs-station errors. Update intraday from METAR
  observations, since the running max is a hard floor. Brackets are mutually exclusive
  (neg-risk), so the vector must sum to 1.
- **Quoting:** two-sided quotes inside `rewardsMaxSpread` in every bracket with meaningful
  probability. Centre on the model when it agrees with the book midpoint, because reward
  scoring uses the *book* midpoint, not yours. Skew size, not price, when they disagree
  modestly.
- **Toxicity rules:**
  1. Cancel or reprice just before scheduled METAR releases (usually around :50–:00 each
     hour) and model runs (00/06/12/18Z). Bots snipe stale quotes right after new data.
  2. Pull a bracket once the observed running max makes it impossible.
  3. Widen against wallets flagged in Section 4.
- **Inventory:** cap net exposure per city-day across all brackets; hedge one bracket's
  inventory with adjacent brackets.
- **Taker overlay:** buy or sell only when
  `|model − executable price| > rate·p(1−p) + half-spread + model error buffer`.

### B. Slow-information reward harvest (data-center moratoria, legal/personnel, product releases)

*Why:* **$12k/day over 29 data-center moratorium markets** with almost no volume. Legal and
personnel markets add $1.6k/day and product releases $4.2k/day. Outcomes move only on
discrete news, so most of the time no one has information to trade against you.

- **Fair value:** slow priors from LegiScan bill status, legislative calendars, and court
  dockets (CourtListener).
- **Main risk is jumps:** an automatic kill switch cancels all quotes on (a) any LegiScan or
  docket status change, (b) a scheduled hearing or vote day, or (c) a price move or fill from
  a flagged wallet. Keep inventory caps small; the reward is the return, not the position.

### C. Live mention markets (watch only at first)

$2k/day of rewards on just $12k/day of volume. This is where Stage 1's genuinely skilled
wallets earn their money, by watching rallies and calls live. Start by **measuring toxicity
only**. Quote outside live windows (before the event starts), and use prior-transcript word
base rates as fair value (alt-data screen, `speech_mentions`).

### D. Favorite carry (research gate, not deployed)

**Result (2026-10-05, [favorite_calibration_report.md](../reports/03_favorite_buying.md)): the gate
fails for taker carry.** The only surviving variant is maker bids at 98–99.5¢ in
decided-but-unsettled markets.

Deploy only if an unconditional calibration test shows ≥90¢ buys earn more than their price
after fees, by bucket and time to resolution, and beat holding-reward yield and capital
lock-up. The fee at 95¢ is small (5%·0.95·0.05 ≈ 0.24¢/share), but §5.8 suggests no excess
return.

## 4. Cross-cutting components

1. **Universe filter (P3):** quote or take only where the book is two-sided, spread is at most
   `rewardsMaxSpread`, top-of-book depth is at least `rewardsMinSize`, and the reward pool is
   at least a minimum $/day. Never use `lastTradePrice`; use executable prices only.
2. **Toxic-flow labels (P1):** counterparty identity is public on-chain.
   - *Toxic:* price-calibrated survivors (39 accounts in §5.8), live-event traders (fills
     after the listed end), and concentrated few-bet winners in discrete-news markets.
   - *Benign:* sign-flip "Skilled" favorite buyers (high win rate, no excess return).
   - Use these labels after each trade, via the public trades API: after a toxic fill, widen
     or pull in that market for N minutes.
   - Measure the dollar cost per dollar filled by wallet class (markout) before trusting the
     labels.
3. **Data-first research loop (P4):** for each cheap dataset, list the outcomes the
   literature says it predicts. Then map those to Polymarket resolution sources using the
   alt-data labels, and keep only (a) reward-subsidised markets or (b) markets where model
   edge exceeds fee plus spread. Spotify valence, for example, maps to `streaming_rank`
   (Spotify/Billboard markets, $1k/day rewards), not to equities.

## 5. Validation plan (order matters)

| Step | Question | Data (all free) | Output |
|---|---|---|---|
| 0 | Do favorites beat price, unconditionally, net of fees? | Existing R2 fills + resolutions (`favorite_buying.py`) | Calibration curve by price × time to end; go/no-go for sleeve D |
| 1 | Is the weather model better calibrated than the market? | Closed temperature markets (Gamma), CLOB `prices-history`, Open-Meteo historical forecasts, Iowa Mesonet METAR archive | Brier/log-loss of model vs market midpoint by lead time |
| 2 | What reward share and fills would my quotes get? | Websocket order-book recorder on target markets for 1–2 weeks | Simulated reward $/day, fill rate, markout vs competitors' depth |
| 3 | What does each wallet class cost a maker? | Stage 1 + Stage 2 maker fills with `counterparty_id` | Markout per $ filled by label; validate toxic-flow rules |
| 4 | Paper trading | Live books + model | P&L split: rewards, rebates, spread, adverse selection, inventory |
| 5 | Small live deployment | — | Same split, with real queue position |

**Kill criteria:**
- Step 1: the model is no better than the market midpoint at any lead time. Then sleeve A
  becomes pure reward harvesting with tight inventory limits.
- Step 2: reward share is too small after competitors' depth.
- Step 4: adverse selection exceeds rewards plus rebates.

## 6. Risks and open questions

- **Reward competition is unknown:** pool size is public, but the depth of other makers'
  scored quotes must be measured (step 2).
- **Resolution risk:** station outages fall back to Wunderground, and UMA disputes are
  possible.
- **Rule changes:** reward and fee programs change often; August's crypto pools already ended.
- **Jurisdiction and access eligibility** must be confirmed before any live trading.
