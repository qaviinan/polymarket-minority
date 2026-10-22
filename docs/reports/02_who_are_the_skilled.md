# Who are the skilled traders? (Oct–Nov 2024)

39 wallets beat the price-calibrated null after Benjamini–Hochberg correction (q < 0.05), and 8 of them are among the 484 Skilled wallets from [report 1](01_stage1_skill_test.md). They made their money by buying after the news, trading live events faster than the market, or betting pregame NFL favorites. None of them looks like an insider who knew the outcome in advance.

The money is concentrated. Three wallets earn 96% of the roughly $290k survivor PnL. 13 wallets made more than $100 and 18 made less than $10. The 4 Unlucky_Loser survivors are round-trip artifacts with PnL near zero. The most "pre-news" trader in the top 10, 0x4dfd (Israel and Iran), narrowly missed the cut.

Source: `scripts/calibrated_skilled_report.py`, with outputs in `logs/calibrated_skilled/`. News times are from public reporting.

## Scorecard

Survivors with PnL over $100, plus the Skilled ones. After end is the share of PnL earned after the listed `endDate`, which usually marks game or rally time. No wallet traded after UMA resolution.

| Wallet | Class | PnL (taker-only) | Volume | ROI | Pos (win%) | After end | Edge |
|---|---|---|---|---|---|---|---|
| 0xfffe4013 | Skilled top10 | $195.3k ($70.3k) | $26.1M | 0.7% | 14 (100%) | 0% | Post-news carry, 57% maker |
| 0xd1ecfa3e | Skilled top10 | $51.1k ($40.7k) | $217k | 23.5% | 16 (100%) | 30% | Pregame NFL favorites |
| 0x45269642 | Skilled top10 | $31.7k ($12.4k) | $288k | 11.0% | 407 (77%) | 92% | Live Trump-rally word counts |
| 0xf566870c | Lucky | $6.8k | $17.7k | 38.7% | 7 (86%) | 0% | Pre-event culture calls |
| 0xc210c07f | Lucky | $2.4k | $11.3k | 21.0% | 8 (88%) | 100% | Live soccer goal latency |
| 0xd9ea7aac | Lucky | $1.3k | $22.0k | 6.0% | 9 (89%) | | Crypto strikes near expiry |
| 0x641d7356 | Lucky | $472 | $2.3k | 20.5% | 11 (91%) | 48% | NFL in-play, Hurricane Milton |
| 0x4d406974 | Skilled rest | $301 | $15.0k | 2.0% | 13 (100%) | 0% | Political near-certainty carry |
| 0x5024b619 | Skilled rest | $229 | $2.0k | 11.4% | 10 (90%) | | Rogan/rally mention markets |
| 0x233fe1e3 | Skilled rest | $98 | $1.7k | 5.9% | 16 (100%) | 87% in-play | Small in-play sports |
| 0x4ee6205c | Skilled rest | $62 | $0.8k | 7.9% | 9 (100%) | 81% in-play | Small in-play sports |
| 0x44678d67 | Skilled rest | $1.38 | $171 | 0.8% | 25 (100%) | | Micro favorite carry |

The other survivors made between −$19 and $139, mostly on under $1k of volume. They include 0x47e35c28 (538 and pregame sports) and 0x2b07c1c1 (Israel day-markets at 94–98¢).

## What they traded

### 0xfffe4013: buying certainty after the news (about $195k)

This wallet buys a near-resolved outcome at 98.8–99.7¢ once the news is out and holds it for the 3–15 hours until UMA settles. On $26M of turnover it makes 0.7%. The edge is capital and settlement mechanics, not forecasting.

It often posts as maker. 87% of its maker buys were matched against takers buying the opposite token for 0.6–1.2¢, and only 13% against holders selling YES at 99¢. On "Trump launch a coin", 817 wallets bought NO at about 0.84¢ (median ticket $14), funding $7.6M of 0xfffe's YES. 0xfffe carries the risk of a disputed resolution.

| Market | Fills (UTC) | Price | PnL | Timing |
|---|---|---|---|---|
| Trump launch a coin before election (YES) | Oct 16 23:37 – Oct 18 00:11 | 99.2¢ | +$101k net | WLFI sale went live Oct 15 12:40 UTC, 35–60 h earlier |
| Israel military action against Iraq (YES) | Oct 29 08:00 – Oct 30 00:18 | 99.4¢ | +$33k | Israel's Oct 25–26 strike on Iran used Iraqi airspace, 3 days earlier |
| HBO doc names Adam Back as Satoshi (NO), plus related legs | Oct 9 01:12 – 02:02 | 98.8–99.9¢ | +$15.6k | The film named Peter Todd. Press stories went up at 01:00 UTC |
| "Favorite to win on Polymarket day before election" | Oct 28 – Nov 1 | 93–97¢ | +$26.3k | Days-ahead carry on a 95¢ favorite, the riskiest leg |
| Status-quo markets (Biden resign, Trump in jail, etc.) | Oct 8 – Nov 3 | 98.9–99.5¢ | +$19k | Held 1–20 days |

### 0xd1ecfa3e: pregame NFL favorites (about $51k)

All 16 positions won, and 95% of PnL ($48.5k) came before kickoff. It buys the favorite hours ahead at 65–81¢ and holds to the final whistle.

| Game | Entry | Price | Result | PnL |
|---|---|---|---|---|
| Ravens @ Bucs (Oct 21) | about 7 h pregame | 65¢ | Ravens 41–31 | +$11.7k |
| Giants vs Steelers (Oct 28) | pregame | 72¢ | Steelers 26–18 | +$9.4k |
| Broncos @ Ravens (Nov 3) | from the day before | 81¢ | Ravens 41–10 | +$6.2k |
| Commanders @ Ravens (Oct 13) | from 2 days before | 74¢ | Ravens 30–23 | +$6.2k |
| Colts vs Vikings (Nov 3) | 29% of USDC after kickoff | 79¢ | Vikings 21–13 | +$6.4k |

The 1-hour markout is −0.6¢ and the 24-hour markout of +17¢ is just resolution. Sixteen favorites at an average of 71¢ all win with probability of about 0.4%. I have not checked these prices against sportsbook closing lines, which would separate skill from luck.

### 0x45269642: counting Trump's words live (about $32k)

407 positions across 39 events. 92% of PnL ($29.1k) came during the rally or speech, from buying threshold markets as the running count climbed.

| Market | Fills (UTC) | Price | PnL |
|---|---|---|---|
| "Border" 20+ times, Allentown PA rally (Oct 29) | 23:50 – 01:15, live | 70¢ | +$2.1k |
| "China" 10+ times, PA rally (Oct 9) | 00:35 – 01:40, live | 59¢ | +$2.0k |
| "God" 4+ times, Wisconsin | Nov 1–2 | 43¢ | +$1.9k |
| Losers: "Biden" 8+, "Kamala" 20+, "too big to rig", "Mars" | live | 62–90¢ | −$1.1k each |

Its 1-hour markout of +5.1¢ is the only clear short-horizon signal among these wallets. But a 77% win rate on 78¢ entries is a thin edge (per-bet Sharpe 0.11), and the information is public audio.

### Smaller accounts

- 0xc210c07f made $2.4k by hitting stale quotes right after soccer goals. In El Clásico (Oct 26) it bought Barça at 33.6¢ at 20:13, just after Lewandowski's 54th-minute goal, and sold at 74.5¢ at 20:18.
- 0xf566870c made $6.8k from pre-event culture calls, the closest thing to forecasting in the group. It was effectively long "Harris on SNL" at about 6¢ (about $180 at risk) up to Nov 1, 1.5+ days before the appearance was first reported. It bought KSI's "Thick Of It" to debut on the Hot 100 at 19.9¢ on Oct 9 and sold at 91.7¢ before the Oct 15 chart release, which fits reading streaming data early.
- 0xd9ea7aac made $1.3k on crypto strike markets bought within hours of expiry.
- 0x641d7356 made $472, mostly NFL in-play, plus Hurricane Milton "Cat 2 or lower" at 51¢ after the forecast weakening was public.
- 0x5024b619 made $229 on mention markets, such as Trump saying "Hitler" on Rogan, bought at 55¢ the day the episode was recorded.

### Near miss: 0x4dfd481c

The top Skilled wallet by PnL ($91k) has calibrated p = 0.0004 but does not survive BH. Israel's strike on Iran began about 23:00 UTC on Oct 25.

| Market | Fills (UTC) | Price | PnL |
|---|---|---|---|
| Israel military response against Iran in October (YES) | Oct 9 – Oct 25 20:52, last buy about 2 h before the strike | 44¢ avg | +$51k |
| Israel strike Iran on Friday Oct 25 (YES) | Oct 25 09:43, 13 h before the strike | 8.8¢ | +$7.8k on $760 |
| Strike by Sunday Oct 27 | Oct 25 08:12–08:17 | 16¢ | +$8.9k on $2.0k |
| "By Friday", YES (earlier weeks lost) and NO (won) | Oct 8–25 | 32¢ / 76¢ | +$38.5k combined |

It added size on Oct 25 at single-digit and teen prices, hours before the strike. That fits an informed trader, but it had been building the October YES position for two weeks, so a long-held view is just as plausible.

## Edge types

| Edge | Wallets | Share of survivor PnL | Skill? |
|---|---|---|---|
| Post-news resolution carry | 0xfffe, 0x4d40, 0x2b07, 0x4467 | ~68% | Operational. ROI 0.5–1% per 1–3 days, with dispute risk |
| Pregame favorites (NFL, 65–81¢) | 0xd1ec, 0x47e3 | ~18% | Unproven until checked against closing lines |
| Live in-event trading | 0x4526, 0xc210, 0x5024, 0x641d, 0x233f, 0x4ee6 | ~12% | Latency on public information; small capacity |
| Pre-event forecast | 0xf566 | ~2% | Possibly (KSI looks data-driven) |
| Inside information | None of the 39 (0x4dfd is the candidate) | 0% | |

None of the 39 placed large bets at long odds shortly before an unannounced event, which is what inside information would look like. The calibrated null mostly picks up near-certain outcomes priced too low.

## Caveats

- Maker fills inflate total PnL relative to the taker-only test: 0xfffe has $195k in total but $70k as a taker.
- Splits and merges are not in the fill data, so 0xf566's SNL position shows up as selling NO.
- `gameStartTime` comes from Gamma and covers 67 markets. In-play shares are computed only for those.
