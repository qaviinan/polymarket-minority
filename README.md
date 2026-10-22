# Polymarket: crowd wisdom or informed minority?

Gómez-Cram, Guo, Jensen and Kung (2026) argue that Polymarket's accuracy comes from a small group of skilled traders, about 3% of accounts, while everyone else pays for it. This repo rebuilds their skill test on on-chain fills from Oct 5 – Nov 4, 2024 (255k wallets, $2.4B traded), then checks how the "skilled" wallets actually made their money.

| 2024, resolved markets only | Wallets |
|---|---:|
| Eligible (≥ 10 trades, ≥ 5 events) | 11,465 |
| Beat the sign test at p < 0.05 | 914 |
| Expected by chance | 539 |
| Survive Benjamini–Hochberg | 484 |
| Also beat the prices they paid | 8 |

The paper's test does find a minority of 484 wallets, 4.2% of eligible ones. But the test treats a win and a loss as equally likely, and most of these wallets buy contracts at 90¢ or more, where wins are nearly certain and pay a few cents. Once the null accounts for the price paid, 8 are left.

![Buy volume and PnL by price, by group](docs/figures/buy_price_buckets_by_cohort.png)

## Findings

1. Most "skilled" wallets are favorite buyers. Their buys at 90¢ or more make up about 88% of their taker volume ([report 1](docs/reports/01_stage1_skill_test.md), [report 2](docs/reports/02_who_are_the_skilled.md)).
2. Buying favorites isn't an edge for a taker. Favorites are about fairly priced, and buying the favorite in every market earns about 0% at horizons from 1 hour to 7 days. Bets under 10¢ lose 50–97% of their stake, and that money goes to makers ([report 3](docs/reports/03_favorite_buying.md)).
3. The few wallets that beat prices were fast, not early. They bought just after public news, traded live events, or bet pregame NFL favorites ([report 2](docs/reports/02_who_are_the_skilled.md)).
4. Big one-off winners are mostly election bettors, offset by a larger group of losers on the same coin flip. Outside the election there are hints of trading ahead of the price, but too few bets per wallet to prove anything ([report 4](docs/reports/04_concentrated_winners.md)).
5. In 56 days of 2026 data the test flags 13.7% of eligible wallets, too many to be a small informed group ([report 5](docs/reports/05_2026.md)).
6. Order flow from skilled wallets doesn't measurably improve prices. Every group shows the same pattern, which fits bid–ask bounce, since I only have execution prices and no quotes ([methods](docs/methods.md#price-discovery)).

## Running it

```bash
uv sync --extra dev
pytest
```

The tests run offline. The full pipeline needs a Cloudflare R2 bucket with the fills; see [methods](docs/methods.md#running-it).

`src/data` loads fills and market metadata, `src/analysis` holds the tests, and `docs/reports` holds the write-ups.

## Limitations

- One month of 2024 data, heavily influenced by the US election.
- No order book history, so the price-discovery tests use trade prices.
- One person can run many wallets, and on-chain data can't link them.
- 2026 fees and market rules differ from 2024 ([report 5](docs/reports/05_2026.md)).

MIT licensed.
