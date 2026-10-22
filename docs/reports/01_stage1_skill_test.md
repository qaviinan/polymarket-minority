# Is there a skilled minority? (Oct–Nov 2024)

Taker fills from Oct 5 to Nov 4, 2024, scored only on markets that resolved before the last fill. Method: [methods](../methods.md#skill-test).

| | Wallets | Share of eligible |
|---|---:|---:|
| Eligible (≥ 10 trades, ≥ 5 events) | 11,465 | |
| Sign test p < 0.05 | 914 | 7.97% |
| Expected by chance (allowing for discrete p-values) | 539 | 4.70% |
| Survive Benjamini–Hochberg, q = 0.05 | 484 | 4.22% |
| Also survive the price-calibrated null | 8 | 0.07% |

The excess over chance is large (z = 16.5), so the profits are not random. Most of the 484 are favorite buyers, though ([report 3](03_favorite_buying.md)), and only 8 beat the prices they paid ([report 2](02_who_are_the_skilled.md)).

## Universe

87.8% of taker volume, and 28.9% of active markets, belong to markets that resolved after the last fill. Those are left out; including them would score open positions as if they had settled.

| Label | All markets | Resolved markets only |
|---|---:|---:|
| Skilled winner | 1,904 | 914 |
| Lucky winner | 19,524 | 3,904 |
| Unlucky loser | 50,939 | 8,523 |
| Unskilled loser | 17,682 | 1,928 |
| Excluded (too few trades or events) | 160,377 | 80,795 |

## Checks

- Maker PnL, taker PnL and fees net to zero within 0.001 bps of volume, and every maker fill maps to a taker.
- No p-value is zero, and none falls below the 2⁻ᴷ floor for its number of events.
- 139 high-volume wallets with near-zero PnL and 8 high-frequency wallets are flagged but kept.
- One person can run several proxy wallets, and on-chain data alone can't link them.

## Composition

The largest category is the US election, at 19.0% of volume. For the median Skilled wallet, its biggest event accounts for 35% of its absolute PnL. The single largest winner is labelled Lucky, and 13 of the top 20 winners are not labelled Skilled: big, concentrated bets on few events can't pass a test that needs at least five.

Top five Skilled wallets by PnL:

| Wallet | Fills | Events | PnL | Buys |
|---|---:|---:|---:|---:|
| `0x4dfd…6e4a` | 78 | 11 | $91,308 | 100% |
| `0x2ff0…46e3` | 2,979 | 273 | $70,296 | 59% |
| `0xfffe…f52c` | 60 | 9 | $70,267 | 98% |
| `0x0362…9661` | 688 | 71 | $45,388 | 90% |
| `0xd1ec…5d8e` | 84 | 16 | $40,713 | 98% |
