# Docs

[← Project README](../README.md)

## Reports

Read them in order. Each one opens with the question it asks and a short answer.

| # | Report | Question | Short answer |
|---|---|---|---|
| 1 | [Is there a skilled minority?](reports/01_stage1_skill_test.md) | Do some takers win more consistently than luck allows? | Yes: 484 of 11,465 wallets (4.2%) survive false-discovery control. |
| 2 | [Who are the skilled traders, really?](reports/02_who_are_the_skilled.md) | Who still wins once the null accounts for the price paid? | 39 wallets. They act on public news or live events, not inside information. |
| 3 | [Does buying favorites pay?](reports/03_favorite_buying.md) | Most "skilled" wallets buy at 90¢+. Can a taker copy that? | No. Favorites are fairly priced; the edge sits with makers. |
| 4 | [Few bets, big wins](reports/04_concentrated_winners.md) | What about wallets with one to three large, focused bets? | Mostly election luck. A small non-election group did trade ahead of the price. |
| 5 | [Does it show up in 2026?](reports/05_stage2_skill_test.md) | Same test on 56 days of recent data. | 13.7% flagged, far too many to be an informed minority. |
| 6 | [2026 vs 2024](reports/06_stage2_vs_2024.md) | Do the 2024 winners keep winning? | No. 4% still trade and 0.4% are flagged again. |

Reports 1 and 5 are written by `python -m src.analysis.stage1_gate`, and report 6 by
`python -m src.analysis.stage2_compare`. Rerunning those overwrites hand edits.

## Methods

- [Pipeline reference](methods/pipeline.md): every table and step from raw fills to the
  regressions, with schemas and the reason for each choice.
- [Setup and runbook](methods/setup.md): credentials, storage, and the order to run things.
- [Paper task list](methods/paper_tasks.md): the paper's methodology broken into replication
  tasks.

## Notes

Working documents that build on the findings. They are not results.

- [Strategy v1](notes/strategy_v1.md): a market-making design informed by reports 1–4.
- [Alternative-data screen](notes/alt_data_screen.md): which live markets an outside data
  source could plausibly forecast.
- [Institutional map](notes/institutional_map.md): how money is made in prediction markets,
  and who is already doing it.

## Glossary

- **Taker / maker.** A taker crosses the spread to trade now; a maker rests a limit order.
  The skill test is run on takers only, as in the paper.
- **Event.** A group of related markets (for example, every candidate in one race). The skill
  test flips the sign of a wallet's PnL one *event* at a time, because markets in the same event
  are correlated.
- **K.** The number of distinct events a wallet traded. With K events the smallest possible
  p-value is 2⁻ᴷ, so wallets need K≥5 to be eligible.
- **Skilled / Lucky / Unlucky / Unskilled.** Winners or losers whose result is, or is not,
  significant under the sign test. *FDR Skilled* means it also survives Benjamini–Hochberg at
  q=0.05.
- **Price-calibrated null.** A stricter test that simulates each bet as winning with the
  probability implied by its price, so a streak of 95¢ favorites is not mistaken for skill.
- **OIB.** Order imbalance: net buying by a group in a time bin, used to test whether that
  group's flow moves prices toward the eventual outcome.
- **Resolved-by-cutoff universe.** Only markets whose market *and* parent event ended before
  the last fill. Without this restriction, open positions (above all the 2024 election) get
  scored before anyone knows the result.
