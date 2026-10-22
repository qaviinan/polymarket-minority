# Who are the skilled traders, really? (Oct–Nov 2024)

**Bottom line.** 39 wallets beat the price-calibrated null at BH q<0.05. 8 of them are also
among the 484 Skilled wallets from report 1. Their PnL comes from three businesses: buying after the news, trading
live events faster than the market, and pregame NFL favorites. In this sample none of them looks
like an insider who knew the outcome in advance. The single most "pre-news" trader in the top 10
(0x4dfd, Israel→Iran) narrowly missed the cut.

The money is very concentrated:

- Three wallets earn **96% of the ~$290k** survivor PnL.
- 13 wallets made more than $100; 18 made less than $10.
- 4 "Unlucky_Loser" survivors are round-trip artifacts with PnL of about zero.

Inputs are
`scripts/calibrated_skilled_report.py` and `logs/calibrated_skilled/`:

- `wallet_summary.csv`
- `positions.csv`
- `fills_enriched.parquet`
- `sports_inplay.csv`

The news times below come from public reporting (CoinDesk, CNN/Bloomberg, NBC/AP, ESPN/BBC,
NYT/JPost).

## 1. Scorecard (wallets with PnL > $100, plus the FDR-Skilled ones)

How to read the columns:

- **Bet SR** is the mean divided by the SD of per-position returns, not annualized; **t** is its
  t-stat.
- **Daily SR** is annualized daily mark-to-market. It is **inflated**: these wallets were active
  for only 6–33 days, and marks are sparse and jump to the payout. Don't compare it to fund
  Sharpes.
- **After end** is the share of PnL earned after the listed `endDate`. That date usually means
  game or rally time, not resolution; no wallet traded after UMA resolution.

| Wallet | Class | PnL (taker-only) | Volume | ROI | Pos (win%) | Avg eff. price | Bet SR (t) | Daily SR | After end | Edge type |
|---|---|---|---|---|---|---|---|---|---|---|
| 0xfffe4013 | Skilled top10 | $195.3k ($70.3k) | $26.1M | 0.7% | 14 (100%) | 0.92 | 1.10 (4.1) | 3.7 | 0% | Post-news resolution carry; 57% maker, filled mostly by ≤1¢ longshot buyers of the other side |
| 0xd1ecfa3e | Skilled top10 | $51.1k ($40.7k) | $217k | 23.5% | 16 (100%) | 0.71 | 1.30 (5.2) | 10.3 | 30% | Pregame NFL favorites |
| 0x45269642 | Skilled top10 | $31.7k ($12.4k) | $288k | 11.0% | 407 (77%) | 0.78 | 0.11 (2.3) | 15.2 | 92% | Live Trump-rally word counts |
| 0xf566870c | Lucky | $6.8k | $17.7k | 38.7% | 7 (86%) | 0.47 | 0.92 (2.4) | 8.8 | 0% | Pre-event culture calls |
| 0xc210c07f | Lucky | $2.4k | $11.3k | 21.0% | 8 (88%) | 0.46 | 0.56 (1.6) | 8.7 | 100% | Live soccer goal latency |
| 0xd9ea7aac | Lucky | $1.3k | $22.0k | 6.0% | 9 (89%) | 0.52 | 1.02 (3.1) | 3.6 | — | Crypto strike markets near expiry |
| 0x641d7356 | Lucky | $472 | $2.3k | 20.5% | 11 (91%) | 0.61 | 1.20 (4.0) | 17.6 | 48% | NFL in-play, Hurricane Milton category |
| 0x4d406974 | Skilled rest | $301 | $15.0k | 2.0% | 13 (100%) | 0.90 | 0.61 (2.2) | 3.7 | 0% | Political near-certainty carry |
| 0x5024b619 | Skilled rest | $229 | $2.0k | 11.4% | 10 (90%) | 0.57 | 0.61 (1.9) | 7.7 | — | Rogan/rally mention markets |
| 0x233fe1e3 | Skilled rest | $98 | $1.7k | 5.9% | 16 (100%) | 0.72 | 0.57 (2.3) | 16.5 | 87% in-play | Small in-play sports |
| 0x4ee6205c | Skilled rest | $62 | $0.8k | 7.9% | 9 (100%) | 0.73 | 1.02 (3.1) | 15.8 | 81% in-play | Small in-play sports |
| 0x44678d67 | Skilled rest | $1.38 | $171 | 0.8% | 25 (100%) | 0.86 | 0.62 (3.1) | 3.3 | — | Micro favorite carry |

The other survivors range from $139 down to −$19. They include 0x226490d5 (Rogan and crypto),
0x4260040d (mention markets), 0x47e35c28 (538 and pregame sports) and 0x2b07c1c1 (Israel
day-markets at 94–98¢). Most of them have fewer than $1k of volume.

## 2. What they traded, and what happened in the real world

### 0xfffe4013: buying certainty after the news, at 99¢ (about $195k)

This wallet buys a near-resolved YES/NO at 98.8–99.7¢ once the news is out. It holds for the
roughly 3–15 hours until UMA settles, and often posts as maker. On $26M of turnover it makes
0.7%. Large recycled capital and settlement mechanics are the edge, not forecasting.

Who filled its resting 99¢ bids? Its maker BUYs were matched to the taker in the same
transaction through `counterparty_id`. **87% were matched against takers buying the opposite
token for 0.6–1.2¢.** The exchange mints a YES+NO pair from the two bids: 0xfffe pays about 99¢
for YES and the taker pays about 1¢ for NO. Only 13% came from holders selling YES at 99¢ to
exit early.

On "Trump launch a coin", 817 wallets bought NO at about 0.84¢ (median ticket $14). That flow
funded $7.6M of 0xfffe's YES. This is the favorite–longshot bias in its purest form: the
counterparty buys lottery tickets on a reversal or a disputed resolution. 0xfffe carries that
dispute risk.

| Market | Fills (UTC) | Price | PnL | Real-world timing |
|---|---|---|---|---|
| Trump launch a coin before election — YES | Oct 16 23:37 → Oct 18 00:11 | 99.2¢ | +$101k net | WLFI sale went live **Oct 15 12:40 UTC**, 35–60 h earlier. Resolved Oct 18 00:22 |
| Israel military action against Iraq — YES | Oct 29 08:00 → Oct 30 00:18 | 99.4¢ | +$33k | Israel's Oct 26 strike (~23:00 UTC Oct 25) first hit radars in Iraq/Syria and used Iraqi airspace, 3 days earlier |
| HBO doc names Adam Back as Satoshi — NO (+ "someone else", Le Roux, Sassaman) | Oct 9 01:12 → 02:02 | 98.8–99.9¢ | +$15.6k | The film named Peter Todd. CNN/Bloomberg stories went up **01:00 UTC** at airtime; the first fill was 12 minutes later |
| "Favorite to win on Polymarket day before election" | Oct 28 → Nov 1 | 93–97¢ | +$26.3k | Days-ahead carry on a 95¢ favorite, the riskiest leg |
| Harris-Walz ticket, Biden resign, 25th Amendment, Trump-Vance ticket, Trump in jail | Oct 8 → Nov 3 | 98.9–99.5¢ | +$19k | Status-quo carry held 1–20 days |

### 0xd1ecfa3e: pregame NFL favorites (about $51k)

16 of 16 positions won. 95% of PnL ($48.5k) came **before kickoff**. It buys the favorite hours
ahead at 65–81¢ and holds to the final whistle.

| Game (date) | Entry | Price | Result | PnL |
|---|---|---|---|---|
| Ravens @ Bucs (MNF Oct 21) | 15:37–17:41 UTC, about 7 h pregame | 65¢ | Ravens 41–31 | +$11.7k |
| Giants vs Steelers (MNF Oct 28) | 20:19–23:36 UTC, pregame | 72¢ | Steelers 26–18 | +$9.4k |
| Broncos @ Ravens (Nov 3) | Nov 2 20:34 → Nov 3 16:12, pregame | 81¢ | Ravens 41–10 | +$6.2k |
| Commanders @ Ravens (Oct 13) | Oct 11 → 13, pregame | 74¢ | Ravens 30–23 | +$6.2k |
| Colts vs Vikings (SNF Nov 3) | 23:54 → 04:12 UTC; 29% of USDC after the 01:20 kickoff | 79¢ | Vikings 21–13 | +$6.4k |
| Titans vs Lions (Oct 27) | Bought Lions at 34¢ effective, sold at 99.9¢ in-game | — | Lions 52–14 | +$1.5k |

The 1h markout is −0.6¢: no immediate information. The 24h markout of +17¢ is just resolution.
Sixteen straight favorites at an average price of 71¢ wins all 16 with probability of about
0.4%. Whether these prices were cheap against sportsbook closing lines **has not been checked**.
That check is the test of skill versus luck plus carry.

### 0x45269642: counting Trump's words in real time (about $32k)

This wallet traded 407 positions across 39 events. 92% of its PnL ($29.1k) came after the listed
end, i.e. **while the rally or speech was underway**. It bought threshold markets as the running
count climbed.

| Market | Fills (UTC) | Price | PnL |
|---|---|---|---|
| "Border" 20+ times, Allentown PA rally (Oct 29, 7pm ET) | 23:50 → 01:15, live | 70¢ | +$2.1k |
| "China" 10+ times, PA rally (Oct 9 evening) | 00:35 → 01:40, live | 59¢ | +$2.0k |
| "God" 4+ times, Wisconsin | Nov 1 → 2 | 43¢ | +$1.9k |
| HBO doc identifies "someone else" or Sassaman | Oct 7 → 9, **pre-airing** | 68–71¢ | +$1.8k |
| Losers: "Biden" 8+ (NM), "Kamala" 20+ (GA), "too big to rig", "Mars" | live | 62–90¢ | −$1.1k each |

Its 1h markout is +5.1¢, the only wallet with clear short-horizon information. A 77% win rate
on 78¢ entries is a thin edge, and the per-bet Sharpe is 0.11. It is high-frequency live
processing of public audio, not inside information.

### Small accounts with interesting mechanisms

**0xc210c07f** made $2.4k from live soccer goals, using stale quotes right after each goal.

| Match | Fill (UTC) | Goal | Trade |
|---|---|---|---|
| El Clásico, Oct 26 (Real 0–4 Barça) | 20:13 | Lewandowski 54' (about 20:11–12) | Bought Barça at 33.6¢, sold 74.5¢ at 20:18 |
| Poland 3–3 Croatia, Oct 15 | 20:12 | Szymański's 68' equalizer | Bought "Croatia doesn't win" at about 30¢, sold 68¢ four minutes later |

This is a latency edge: TV/stadium feed beats the market maker. It is not a forecast.

**0xf566870c** made $6.8k from pre-event culture calls, the closest thing to genuine forecasting
in the group.

| Market | Trade | What happened |
|---|---|---|
| Harris on SNL | Effectively long YES at about 6¢: sold NO at 94¢ from Oct 9 to **Nov 1 04:00 UTC**; about $180 at risk | Appearance first reported Nov 2 8:23pm ET, and confirmed by her team only minutes before air. The bet came **1.5+ days before** any report; one hit at 6% |
| KSI "Thick Of It" debuts on Hot 100 | Bought YES at 19.9¢ on Oct 9, sold at 91.7¢ Oct 12–13 | Resolved YES Oct 15, after the chart release. Consistent with reading tracking-week streaming and sales data early, which is an alt-data-style edge |

**0xd9ea7aac** made $1.3k on daily and monthly ETH/BTC/SOL strike markets, bought within hours
of expiry. This is momentum or near-expiry carry with no news component.

**0x641d7356** made $472, mostly NFL in-play. It also bought Hurricane Milton "Cat 2 or lower" at
51¢ on Oct 9, the afternoon before landfall, when the forecast weakening was public.

**0x5024b619** made $229 on Rogan and rally mention markets. One example: Trump says "Hitler" on
Rogan, bought at 55¢ on Oct 25, the day the episode was recorded.

### Near-miss: 0x4dfd481c (top-10 #1, $91k, calibrated p=0.0004, not a BH survivor)

This is the only wallet whose timing looks like a **forecast ahead of the news**. Israel's strike
on Iran began about **23:00 UTC Oct 25**.

| Market | Fills (UTC) | Price | PnL |
|---|---|---|---|
| Israel military response against Iran in October — YES | Oct 9 → Oct 25 20:52, last buy about 2 h pre-strike | 44¢ avg | +$51k |
| Israel strike Iran on Friday Oct 25 — YES | Oct 25 09:43, 13 h pre-strike | **8.8¢** | +$7.8k on $760 |
| Strike by Sunday Oct 27 (paid out) | Oct 25 08:12–08:17 | 16¢ | +$8.9k on $2.0k |
| "By Friday" — YES (earlier weeks lost) and NO (won) | Oct 8 → 25 | 32¢ / 76¢ | +$38.5k combined |

Size went up on Oct 25 at single-digit and teen prices, hours before the strike. That is
consistent with an informed or very well-read geopolitical trader. The wallet had been
accumulating the October YES for two weeks, though, so a persistent thesis is equally plausible.

## 3. Edge taxonomy

| Edge | Who | Share of survivor PnL | Is it skill? | Replicable? |
|---|---|---|---|---|
| **Post-news resolution carry** (news public; buy 99¢, wait for UMA) | 0xfffe, 0x4d40, 0x2b07, 0x4467 | ~68% | Operational: capital, rule-reading, speed to the settled state | Yes, but low ROI (0.5–1% per 1–3 days); tail risk is a disputed resolution |
| **Pregame favorites** (NFL 65–81¢) | 0xd1ec, 0x47e3 | ~18% | Unproven; needs a sportsbook closing-line check | Tests directly against Odds API history |
| **Live in-event processing** (word counts, goals, in-play) | 0x4526, 0xc210, 0x5024, 0x641d, 0x233f, 0x4ee6 | ~12% | Yes, a latency or attention edge on public info | Needs a live feed + bot; capacity small |
| **Pre-event forecast** (streaming data, surprise appearances) | 0xf566 | ~2% | Possibly (KSI looks data-driven) | Alt-data angle (chart-tracking data) |
| **Inside/early information** | none among the 39; 0x4dfd is the candidate | 0% | — | Monitor, don't copy |

**Answer to "timing after news versus inside news".** About 70% of survivor PnL was made **after
the outcome was publicly known**. About 12% came **during** the event, on public information
processed faster. Most of the rest is pregame sports risk. None of the 39 shows the signature of
inside information: large size at long odds shortly before an unannounced event. The calibrated
null catches "prices were too low for near-certain things", not "knew the future". Copying
these wallets would not have worked.

## Caveats

- Only one month of data (Oct 5 – Nov 4, 2024), dominated by the election.
- Maker fills inflate total PnL relative to the taker-only test: 0xfffe has $195k total but $70k
  taker. Splits and merges are not in the fill data. That is why 0xf566's SNL position shows up
  as SELL NO; it is economically equivalent to buying YES.
- `gameStartTime` comes from Gamma and covers 67 markets; in-play shares are computed only for
  those markets.
