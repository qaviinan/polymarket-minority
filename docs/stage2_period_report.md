# Stage 2 — recent period vs Oct–Nov 2024

## Extended window (2026-08-10 09:00Z → 2026-10-04 21:00Z)

The 13.5-day Gate A failure was missing resolution payouts, not fees. Markets cached before they closed had null `resolved_payout`; those NaNs became `p_skill=0`. This rerun keeps a market only when every outcome has a payout and the payouts sum to 1, scores only wallets with at least 5 events and 10 trades, and marks a wallet unscorable when the all-buy draw does not match realized profit.

Payouts are from the HuggingFace markets file (market end date, not the parent-event end). Combo trades are dropped. Sports and weather are not filtered, because that file has no Gamma tags. Labels: `logs/periods/recent_2026/resolved_window/account_classification.parquet`.

| | Value |
|---|---:|
| Resolved fills | 63,372,885 |
| Taker / participant volume | $2.75B / $5.44B |
| Taker accounts | 243,074 |
| Eligible (`n_trades≥10`, `K≥5`) | 75,731 |
| Nominal Skilled | 12,200 (16.1% of eligible) |
| FDR Skilled (BH q=0.05, winners only) | 10,410 (13.7%; 1,790 demoted) |
| Unskilled | 6,534 |
| Unscorable / `p_skill=0` | 0 / 0 |
| Minimum events among FDR Skilled | 6 |
| FDR Skilled taker PnL | $16.0M |
| Pre-fee residual | −0.15 bps |

Construct checks that failed on the 13.5-day run now pass. The Skilled share is still far above the paper's 2–6% minority, so these identities stay out of Stage 3/4. The rest of this file is the superseded 13.5-day run.

## 13.5-day run (superseded)

**Period:** `recent_2026`  
**Fill window:** 2026-09-21 09:00Z → 2026-10-04 21:00Z (**13.5 days**)

Goldsky was **not** called. Analysis uses the 27 already-verified R2 slices, re-filtered to drop Exchange-v3 combos and sports/esports/weather. A local parquet mirror (`data/periods/recent_2026/r2_mirror/`) was used after one R2 copy so Stage 1 prefixes were never overwritten.

A 13.5-day non-election window is not a replication of Oct–Nov 2024. It is a different market (bitcoin 27% of resolved volume) with real taker fees (~108 bps of volume vs ~0 in 2024). Both facts matter for interpretation.

## Universe

| | Value |
|---|---:|
| Raw slice rows | 7,440,429 |
| Rows after combo/theme filter | 5,005,689 |
| Excluded sports/weather markets | 78,054 |
| Distinct users (unfiltered) | 127,910 |
| Taker fills / USDC (filtered) | 1,847,443 / $128.5M |
| USDC in markets resolved by cutoff | $47.2M (**36.7%**) |
| Fill cutoff | 2026-10-04 20:59:59Z |
| Ingest QA | 0 day gaps, 0 duplicate fill IDs |

Primary labels use `PNL_MARKET_UNIVERSE=resolved_by_fill_cutoff` (market **and** event end ≤ cutoff), same as Stage 1 v2. 63.3% of filtered taker volume is still in markets that had not ended by the last fill, so the unrestricted table is sensitivity only. No 3-week/1-week calendar split: 13.5 days is too short.

## Apples-to-apples metrics (resolved-by-cutoff)

| Metric | Stage 1 (Oct–Nov 2024, 35d) | Stage 2 (Sep 21–Oct 4 2026, 13.5d) |
|---|---:|---:|
| Accounts in PnL table | 96,064 | 44,999 |
| Eligible (`n_trades≥10`, `K≥5`) | 11,465 | 5,934 |
| Nominal Skilled_Winner (all K) | 914 | 1,382 |
| Eligible Skilled_Winner | 914 (7.97%) | **1,329 (22.4%)** |
| FDR Skilled (`trader_group_fdr_q05`) | 484 | **1,083** |
| BH q=0.05 flags on eligible p_skill | 484 | 1,549 |
| Excess z vs discrete null | 16.5 | 91.6 |
| Gate A statistical | PASS | **FAIL** (construct) |
| Gate A accounting | PASS | **FAIL** (1.79 bps leak) |
| Top category volume | election / `trump` | **bitcoin 26.9%** |

The 22% eligible Skilled share is **not** a successful replication of the paper’s 2–6% minority. It is too large, and Gate A is supposed to reject it.

### Why Gate A fails (construct, not “no winners”)

1. **Fees are no longer cosmetic.** Sign-construct audit: 108 bps of volume in fees (yellow). Stage 1’s Oct–Nov 2024 fills had ~zero fees, so `event_gross = pnl + fee` was identical to PnL. Here the all-`+1` sign vector often does **not** reproduce `pnl_account.total_pnl` to the 1e-9 tolerance used in exact enumeration, so **261 Skilled accounts have `p_skill = 0`**. That also labels **53 accounts Skilled with K∈{2,3,4}**, which is mathematically impossible at α=0.05 (floor is `1/2^K`). Those two assertions are red, so statistical Gate A fails even though z=91.
2. **Maker/taker closure is 1.79 bps off** (Stage 1 passed). 100% of maker groups still map to a taker user; the leak is small but above the configured gate.
3. **BH on all eligible p_skill includes 468 losers** with tiny `p_skill` (degenerate/extreme nulls). Honest FDR Skilled count is **1,083**, not 1,549.

Treat the 2026 Skilled label as a **favorite-buying / short-horizon consistency flag**, not as an informed-minority identity. That matches Stage 1’s later construct finding.

## Wallet survival — the copy-trading number

Of **484** Stage 1 FDR Skilled wallets:

| | N | Share of 2024 FDR Skilled |
|---|---:|---:|
| Any fill in this 13.5-day window | 20 | 4.1% |
| Row in the 2026 resolved PnL table | 9 | 1.9% |
| Classified (not Excluded) | 4 | 0.8% |
| Retestable (`K≥5`, ≥10 trades) | 3 | 0.6% |
| Skilled again (nominal / FDR) | 2 | 0.4% |

2026 resolved-universe taker PnL for 2024 Skilled wallets that appear in the label table: **~$99** (mean ~$12). The two “Skilled again” wallets are `0x22db…` (+$65, `p_skill=0`, the exact-enumeration artifact) and `0xd404…` (+$0.29).

**Verdict:** following 2024 Skilled identities into 2026 is not implementable. Attrition is the finding. A clean negative is what the workplan asked for.

Non-election concentrated-winner watchlist (169 wallets from Stage 1): **7** appear in the window, **3** are reclassified.

## Out-of-sample alpha inside the window

Event-split persistence (seed 42, 9,394 events, 80/20): among users classified in both arms, P(Skilled test | Skilled train) = **54.6%** vs 26.8% base = **2.04×** (Stage 1 v2 was 3.05×). The lift is real and weaker, and the base rate is inflated by the same 22% Skilled share. It does not rescue copy-trading of 2024 wallets.

## What 2026 “Skilled” are doing

FDR Skilled (n=1,083) took **$13.5M** volume and made **$346k** taker PnL (ROI 2.6%). Lucky winners made more in dollars ($411k on $8.2M). Top 10 Skilled hold **$117k / 34%** of Skilled PnL (Stage 1 was 92% in the top 10 — this window is less single-wallet dominated).

| | Skilled | Lucky | Unskilled losers |
|---|---:|---:|---:|
| BUY share of volume | 90% | 78% | 80% |
| ≥50¢ favorite share of BUY volume | 96% | 87% | 76% |
| ≥90¢ favorite share of BUY volume | 84% | 62% | 47% |
| ≥50¢ favorites as share of net PnL | 60% | 65% | — |
| Outcome-aligned volume | 77% | 71% | 53% |
| US election volume | 5% | 3% | 7% |

Same pattern as Stage 1 after the Harris leak was removed: Skilled is mostly buying favorites that expire. Median account made **$18**. Calibrated (price-aware) null: only 16% of Skilled-rest beat p<0.05, and 53/1,073 survive BH — the sign-flip label is not a price-calibrated edge.

## OIB / price discovery (resolved universe, `OIB_EVAL_WEEK=all`)

13,432 markets in the panel; **0%** end after the last fill (unlike Stage 1). Skilled are 28% of eval volume.

- Volume-share of Skilled **raises** next-bin absolute error (h1 full FE β=+0.0067, p=7e-9; h10 β=+0.015, p=6e-6). That is the opposite of “skilled flow corrects prices.”
- Directional Skilled OIB at h1 is a small **+0.0028** (p=0.007) with full FE — consistent with bid–ask bounce on execution VWAP, not informational lead.
- Unskilled OIB is the stronger directional term in several specs, as in Stage 1.

OIB does not support using the 2026 Skilled label for toxicity or copy-trading.

## Verdict

1. **Copy-trading 2024 Skilled wallets is dead on this sample.** 4.1% still trade at all; 0.4% re-flag; dollar PnL is noise.
2. **The 2026 Skilled share is not a paper-style informed minority.** 22% eligible Skilled, Gate A fail on p=0 / K<5 / fee-aware identity, bitcoin-heavy mix, and favorite-buy PnL.
3. **A longer resolved window, or a fee-aware exact p-value, would be required** before treating any 2026 identity as Stage 3 input. Do not proceed to identity-based Stage 4 on these labels.

Falsifier: a later window where ≥20% of the 484 2024 FDR Skilled are retestable and beat the 2026 base rate after a fee-correct sign test.
