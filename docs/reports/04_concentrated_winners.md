# Few bets, big wins (Oct–Nov 2024)

Most wallets that won big on a few bets were election bettors (1,765 of 1,934, with 96.5% of their $19.1M). A larger group of similar wallets lost $34.9M, much of it on the same outcome. Outside the election, a small set of winners traded ahead of the price, led by the Ballon d'Or and Israel–Iran markets, but with 1–3 bets each no single account can be shown to be skilled.

These are wallets the skill test in [report 1](01_stage1_skill_test.md) can't see: a few bets in one category, with large wins or losses. The data are all order fills from Oct 5 to Nov 4, 2024 (255,566 accounts, $2.4B). Unlike the skill test, this report uses every market, including the presidential election, and scores each position at its final payout. Code: `python -m src.analysis.concentrated_winners`, outputs in `logs/concentrated_winners/`.

## Who these accounts are

Selection: at most 3 events (`CONC_MAX_EVENTS`), at most 50 fills (`CONC_MAX_FILLS`), at least 90% of volume in one theme (`CONC_MIN_THEME_SHARE`), and |PnL| of at least $1,000 (`CONC_MIN_PNL`).

| Population | Accounts | Net resolution PnL |
|---|---:|---:|
| Few-bet (≤3 events, ≤50 fills) | 160,143 | |
| …and single-category (≥90% one theme) | 137,152 | −$16.97M |
| …of which US election outcome theme | 109,568 | −$15.76M |
| …of which every other theme | 27,584 | −$1.21M |
| Concentrated winners (PnL ≥ $1k) | 1,934 | +$19.15M (median $2.7k) |
| Concentrated losers (PnL ≤ −$1k) | 3,643 | −$34.94M |
| Non-election winners | 169 | +$0.67M (median $2.0k) |
| Non-election losers | 375 | −$1.38M |

The skill test never sees these winners. 1,702 of them (88%) traded only markets that resolved after the data ends, and 221 more had fewer than 10 trades or 5 events. Only 9 got a skill-test label, all Lucky_Winner.

By main theme, 1,765 winners ($18.48M) bet on the US election outcome. The rest are spread across sports and esports (43), US campaign props (41), crypto (40), geopolitics (25), economy (14) and three other themes (6), each with $0.2M or less.

The election winners almost always bought Trump at about 55–65¢. The largest all look alike: one presidential-winner position of $0.1–2.0M in 2–30 fills, such as `0x051b16ff…` with +$1.17M on $2.0M. Their ROI of 0.5–0.8 implies Trump entry prices of about 55–67¢, and their calibrated p-values are 0.55–0.68.

## The losing side

Few-bet single-category accounts, by event. In the presidential market the losers outweigh the winners. It is one coin flip seen from both sides, and one binary outcome can't separate knowledge from luck.

| Event | Winners | Winner PnL | Losers | Loser PnL | Net |
|---|---:|---:|---:|---:|---:|
| Presidential Election Winner 2024 | 1,669 | +$17.19M | 2,854 | −$23.57M | −$6.74M |
| Popular Vote Winner 2024 | 173 | +$0.85M | 744 | −$8.34M | −$7.68M |
| Ballon d'Or Winner | 16 | +$0.08M | 15 | −$0.05M | +$0.02M |
| Israel military response vs Iran by Friday? | 17 | +$0.05M | 12 | −$0.01M | +$0.04M |
| Who will HBO doc identify as Satoshi? | 12 | +$0.05M | 68 | −$0.16M | −$0.20M |
| Will Trump launch a coin before the election? | 19 | +$0.05M | 76 | −$0.17M | −$0.28M |

This money lost on nearly every large event. The Ballon d'Or and Israel–Iran are two of the few where it came out ahead.

## Evidence that does not depend on who won

![Calibration and markouts](../figures/concentrated_calibration_markouts.png)

### Calibration

Net-long positions of the few-bet single-category accounts, excluding the election bloc (presidential winner, popular vote, winning party). Win rates and prices are share-weighted, and CIs come from a bootstrap over events.

| Entry price | Positions | Events | Avg price | Win rate | Edge per share (95% CI) |
|---|---:|---:|---:|---:|---|
| <10¢ | 12,955 | 512 | 1.5¢ | 0.1% | −1.4¢ (−2.2 to −1.0) |
| 10–30¢ | 5,748 | 430 | 19.9¢ | 8.4% | −11.5¢ (−18.3 to −0.8) |
| 30–50¢ | 5,786 | 454 | 38.6¢ | 15.9% | −22.8¢ (−29.7 to −8.2) |
| 50–70¢ | 7,406 | 472 | 60.3¢ | 42.6% | −17.7¢ (−32.7 to +3.6) |
| 70–90¢ | 4,935 | 434 | 81.5¢ | 69.0% | −12.5¢ (−37.2 to +8.7) |
| ≥90¢ | 39,348 | 597 | 96.5¢ | 98.5% | +2.0¢ (−0.2 to +3.6) |

Bets under 90¢ win far less often than their price implies; 30–50¢ bets win 16% of the time against a 39% price. This is the longshot pattern of one-shot retail bettors. The whole population nets −$17.0M against a price-fair expectation of −$0.26M, so any informed minority is a small subset of it.

### Markouts

A fill's timing-adjusted markout is the move in the token's hourly VWAP over the next h hours, in the trade's direction, minus the average move for all same-side taker flow in that token. Since both terms share the eventual outcome, a positive value means the trade came ahead of a repricing. Values are cents per share, with 95% CIs from a bootstrap over accounts.

| Group | Accounts | 24h | 72h |
|---|---:|---|---|
| Non-election winners | 453* | +0.91 (+0.25, +1.67) | +0.85 (+0.13, +1.66) |
| Non-election losers | 1,047* | −0.28 (−0.49, −0.09) | +0.15 (−0.27, +0.58) |
| Election-bloc winners | 1,722* | −0.32 (−0.83, +0.12) | −1.53 (−2.52, −0.48) |
| Election-bloc losers | 3,182* | +0.05 (−0.10, +0.21) | +0.26 (−0.05, +0.62) |

\*Accounts with fills in that bloc; some traded both. All taker flow in the same tokens has a raw markout of −0.03 to −0.07¢.

Election winners were not early. They bought Trump before his late-October dip, and the market moved against them for days before the result. They were right about a roughly 60% favorite that the market had already priced.

Non-election winners were early on average: +0.9¢ per share at 24h, against −0.3¢ for comparable losers. But of the 169 winners whose main theme isn't the election, 137 have measurable 24h markouts and only 49% of those are positive. A few discrete-news episodes drive the average. Winners were also picked for making money, which favors entries just before a repricing, so part of the markout is selection.

### Per-account tests

The price-calibrated null gives 14 winners at p < 0.05, 6 of them outside the election, and none survives BH at q = 0.05 across 137,152 accounts. With 1–3 bets the smallest attainable p-value is about the product of the entry prices, so the test has almost no power.

### Linked wallets

Wallets that trade the same token on the same side within 60 seconds, at least twice, form 16 clusters covering 37 winners. Almost all are pairs in busy election markets, which is weak evidence. The largest has 7 wallets (+$7.9k), and two pairs made $227k and $128k. Known election whales trade more than 50 fills, so this filter misses them.

## Case studies outside the election

### Ballon d'Or 2024: Rodri

The Rodri YES token traded at 5–10¢ through Oct 27 and briefly hit 19¢ around 23:00 UTC that night. It jumped from about 20¢ to 90¢ in the 14:00 UTC hour on Oct 28, when it became public that Vinícius Jr. would not win, several hours before the ceremony.

| Wallet | Entry | Stake | Avg price | PnL | Calibrated p |
|---|---|---:|---:|---:|---:|
| `0x7477a09c…` | Oct 18–27 | $1.45k | 5.4¢ | +$25.3k | 0.006 |
| `0x1596c2e9…` | Oct 28 11:23–13:36 UTC | $1.56k | 14¢ | +$25.5k | 0.023 |
| `0xe677bf38…` | Oct 27 11:04–18:25 | $0.52k | 5.7¢ | +$8.9k | 0.054 |
| `0x0cebfb9b…`, `0x6b1cdeae…` | Oct 27–28 | $6–9k | 15–17¢ | +$4.4k, +$6.3k | 0.15–0.17 |

`0x1596` bought in the 2.5 hours before the public repricing. Hourly volume in the Rodri token rose from $0.4–0.6k (09:00–10:00) to $1.2–3.9k in the 11:00–13:00 hours while the price crept from 10¢ to 20¢. This is the clearest pre-news footprint in the sample, and the Ballon d'Or has the largest timing-adjusted markout of any event (+14¢ per share at 24h). All of these wallets traded only this event.

### Israel's strike on Iran (Oct 25–26)

In "Israel military response against Iran by Friday?", five few-bet wallets bought YES at 8–20¢ between 10:55 and 22:03 UTC on Friday Oct 25, hours before the strikes began that night. They made $2.9k to $13.1k each; `0xe1d9aa3c…` made +$13.1k on $2.5k bought at 16¢. This fits advance knowledge, but news of Israeli preparations was also public that day, so the case is weaker than the Ballon d'Or. Several of these positions have no 24h markout because the token stopped trading at resolution.

### Other patterns

- Live-event trading at 40–75¢ (the LoL Worlds final, Trump rally mentions, NFL games), like the mention traders in [report 2](02_who_are_the_skilled.md).
- Election-linked side bets ("Trump wins every swing state?" at 20–30¢, "BTC $100k in 2024") are cheap tickets on the same Trump win, not independent evidence.
- HBO Satoshi documentary winners bought "someone else" (Peter Todd) at 54–66¢ before airing. Markouts are mixed to negative, consistent with a good read of public leaks.

`logs/concentrated_winners/watchlist_non_election.csv` ranks the 169 non-election winners by 24h timing-adjusted markout, cheapness of entry, and calibrated p. The top entries are three Ballon d'Or wallets, two Israel–Iran wallets, and a "Kamala on SNL" wallet that bought at 3.6¢ (+$5.5k).

## Next steps

Outcome-based tests can't work for one-shot bettors, so the next tests look at flow.

1. Score fills instead of accounts. Flag cheap fills (under 30¢) in discrete-news markets from wallets with few prior bets and large size relative to recent volume, then test out of sample whether they predict repricing over the next 1–24h.
2. Track the watchlist forward.
3. Pull each wallet's fills from before October 2024 to give the calibrated null more bets.
4. Link wallets by shared USDC deposit addresses, which aren't in this dataset.
5. For the election, test timing against public information rather than PnL. On that test the few-bet Trump winners were not early.

## Method notes

- PnL = (buy − sell shares) × payout − (buy − sell USDC) − fees, per account and token, taker plus maker, in-window fills only. Selling inventory bought before the window is scored as if the account had held instead.
- Markouts use hourly execution VWAP (no quotes) and forward-fill thin hours. Horizons past Nov 4 are dropped, which removes most election-day repricing.
- The calibrated null simulates positions in the same market jointly through the YES token, with 20,000 draws per account (`CONC_NULL_ITER`).
- `CONC_REFRESH=1` re-pulls the data from R2.
