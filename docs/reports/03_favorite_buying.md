# Does buying favorites pay? (Oct–Nov 2024)

Not for a taker. Favorites at 90¢ and above are roughly fairly priced, and buying the favorite in every market earns about 0% at every horizon tested. The mispricing is in longshots: bets under 10¢ lose 50–97% of the money put in, and that money goes to the makers resting favorite bids.

"Skilled" wallets made money from favorites mainly because the label selects on outcome and because this window favored favorites, which explains 57% of their favorite PnL. The small remainder comes from markets whose outcome was public but not yet settled, and half of it belongs to one wallet.

The data are 988,109 taker fills ($231M) in 2,478 resolved markets, Oct 5 – Nov 4, 2024 (`src/analysis/favorite_calibration.py`). A sell at p counts as a buy of the complement at 1 − p. Fees paid totalled $15; the 2026 column applies today's taker fee of 5%·q(1−q). CIs come from a bootstrap over events.

![Favorite calibration](../figures/favorite_calibration.png)

## Calibration of every taker fill

| Price paid | Fills | Win rate vs price | ROI after fees (95% CI) | ROI at 2026 fees | Excess, each market once |
|---|---|---|---|---|---|
| <2¢ | 129k | 0.01% vs 0.33% | −96.5% (−99.5, −86.4) | −101% | −0.36 pp |
| 2–5¢ | 31k | 0.5% vs 3.2% | −85.3% (−95.5, −59.4) | −90% | −1.2 pp |
| 5–10¢ | 34k | 3.6% vs 7.3% | −50.6% (−83, +25) | −55% | −1.9 pp |
| 80–90¢ | 69k | 87.1% vs 85.5% | +1.9% (−4.8, +8.3) | +1.2% | −0.2 pp |
| 90–95¢ | 68k | 95.3% vs 92.6% | +3.0% (−5.4, +6.2) | +2.6% | −1.2 pp |
| 95–98¢ | 70k | 98.8% vs 96.7% | +2.2% (+0.4, +3.0) | +2.1% | −2.1 pp |
| 98–99¢ | 38k | 99.8% vs 98.6% | +1.3% (+1.0, +1.4) | +1.2% | −1.3 pp |
| ≥99¢ | 193k | 99.99% vs 99.7% | +0.26% (+0.16, +0.39) | +0.24% | −0.7 pp |

Weighted by dollars, favorites earn +0.3% to +3%. That gain is concentrated: fills at 90¢ or more returned +2.2% on the US election outcome (15 correlated events; Trump won), +0.9% on crypto (mostly the Trump-coin market), −1.7% on sports favorites, and 0.0% when bought live. Counted once per market, every bucket from 80¢ up does worse than its price.

## Buying the favorite in every market

At horizon h before resolution, take the last trade, buy whichever side is at 50¢ or more, and put equal dollars in each market. Last-trade prices are better than the ask, so this is optimistic.

| Horizon | Markets | ROI, all favorites | 90–95¢ | 95–98¢ | 98–99¢ | ≥99¢ |
|---|---|---|---|---|---|---|
| 1h | 2,425 | −0.3% | +2.4% (−8, +9) | −1.5% | −0.9% | −0.2% (−0.6, +0.1) |
| 6h | 2,359 | +1.3% | +2.2% | −3.3% | −0.1% | −0.2% |
| 24h | 1,831 | −0.1% | −0.4% | −2.5% | −0.1% | −1.8% (−3.7, −0.3) |
| 72h | 836 | +0.8% | −0.9% | −2.2% | −3.1% | +0.3% |
| 7d | 376 | +1.0% | −9.7% | −2.0% | −1.1% | −0.6% |

None of the totals differs from zero, and the 95–99¢ cells are negative at every horizon. The one significant positive cell, ≥99¢ at 72h (+0.34%), is 0.34¢ per share over about 3 days, before crossing the spread.

Favorites lose on information events:

| Upset | Favorite price | Losses on favorite fills |
|---|---|---|
| Vinícius Jr. to win the Ballon d'Or (Rodri won) | 92¢ | $135k |
| BTC above $62k on Oct 11 | 93¢ | $125k |
| Rodri *not* to win | 94¢ | $108k |
| Israel strikes Iran by Friday | 92¢ | $95k |
| Harris *not* on SNL | 95¢ | $94k |

## How "Skilled" wallets made money

First, the label selects on outcome. 7,975 accounts with at least 5 positions put at least 80% of their cost into fills at 90¢ or more. Together these favorite specialists net +$82k on $125M (+0.07%). Only 39% are profitable, and the median result is −$0.35. 441 of the 484 Skilled wallets are among them.

| Label | Accounts | Share profitable | Pooled ROI | Total PnL | Hit ≥1 upset |
|---|---|---|---|---|---|
| Skilled_Winner | 441 | 100% | +1.3% | +$192k | 1.6% |
| Lucky_Winner | 1,109 | 100% | +0.7% | +$475k | 11.8% |
| Unlucky_Loser | 2,602 | 0% | −1.3% | −$394k | 18.3% |
| Unskilled_Loser | 849 | 0% | −3.3% | −$162k | 5.3% |
| Excluded (too few trades) | 2,974 | 53% | −0.6% | −$30k | 8.7% |

The same strategy produced 1,550 "winners" and 3,451 "losers". Wallets that avoided an upset were labelled Skilled or Lucky; those that hit one, or paid the spread round-tripping, were labelled Unlucky or Unskilled. Under the price-calibrated null only 8 of the 484 survive ([methods](../methods.md#price-calibrated-null)).

Second, the window rewarded favorites. Skilled wallets earned $138.8k on fills at 90¢ or more (0.95% ROI). Other takers' ROI at the same price, to the cent, implies $78.6k of that (57%). This is the election-heavy effect from the calibration table, which the strategy test shows is not repeatable.

Third, the residual is small and concentrated. Matching on price, time to resolution, theme and live or pre-event leaves $56.7k. 0xfffe alone accounts for $29.4k. The other 483 wallets share $27.3k, a median of $0.26 each, with the top 5 holding 76%. 70% of Skilled favorite PnL comes from 10 markets.

| Market | Residual |
|---|---|
| Trump launch a coin | $12.8k |
| "Favorite to win on Polymarket day before election" | $12.8k |
| HBO documentary names Adam Back | $2.3k |

In these markets the outcome was public but not yet settled, which the time-to-resolution control can't capture. 87% of 0xfffe's passive fills there were against takers buying the losing side for under 1¢ ([report 2](02_who_are_the_skilled.md)).

## Implications

Taker favorite carry doesn't pay. Per market the edge is zero or negative, before paying the ask and before 2026 fees (0.05–0.45¢ per share at q ≥ 90¢). What is left is on the maker side: resting bids at 98–99.5¢ in markets that are decided but not yet settled, with longshot takers as the counterparty.

## Caveats

- One month of data. Dollar-weighted results lean on about 15 correlated election events.
- Maker fills are excluded from calibration. Taker PnL roughly mirrors maker PnL, because fees were close to zero.
