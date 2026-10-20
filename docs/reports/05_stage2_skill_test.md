# Stage 2: does the skilled minority show up in 2026?

[← All reports](../README.md)

> **Question.** Run the same skill test on 56 days of recent data (Aug 10 – Oct 4, 2026). Is
> there still a small skilled minority?
>
> **Answer.** The test runs cleanly, but 13.7% of eligible wallets come out "skilled" after
> false-discovery control. That is far above the paper's 2–6%, so the label is not picking out
> an informed minority, and these wallets are not used for follow-up work.

## Result (2026-08-10 → 2026-10-04)

**Construct checks: PASS** (0 unscorable, 0 `p_skill=0`, minimum Skilled K=6, pre-fee residual −0.15 bps).  
**Skilled share: still too high to treat as the paper's minority** (16.1% nominal, 13.7% after BH q=0.05, of 75,731 eligible wallets).  
**Proceed with identity-based Stage 3/4: NO.**

The 13.5-day failure below was a missing-payout bug, not a fee bug. Gamma pages cached while markets were still open left `resolved_payout` null, the sign test turned those NaNs into `p=0`, and classification stamped them Skilled, including 53 wallets with K<5. Fees cancel on the all-buy draw when profits are finite. This rerun drops any market without a complete payout vector (including 50/50 splits), refuses to score a wallet whose profits are missing or whose all-buy draw does not match realized profit, and labels K<5 as Excluded.

Resolved taker volume in the 56-day window is $2.75B across 63.4M fills. FDR Skilled is 10,410 wallets (1,790 nominal Skilled demoted), with $16.0M combined taker PnL. Sports and weather were not theme-filtered: payouts came from the HuggingFace markets file, which has no Gamma tags. Copy-trading survival was not recomputed on this window.

The first attempt used only 13.5 days (2026-09-21 → 2026-10-04) and failed because of the payout bug described above. Its full output is kept below as a record.

<details>
<summary><b>Superseded: 13.5-day run (2026-09-21 → 2026-10-04)</b></summary>

## Verdict (13.5-day run, superseded)

**Gate A: FAIL**  
**Gate B deliverables: COMPLETE**  
**Proceed with identity-based Stage 4: NO**

Statistical Gate A was red because exact-enumeration `p_skill` hit 0 (261 Skilled) and 53 Skilled had K<5. That was null payouts, not fees. Excess z=91 and 1,083 FDR flags were not a license to use the label. Accounting Gate A was red on a 1.79 bps pre-fee leak. See [stage2_period_report.md](06_stage2_vs_2024.md).

## Coverage and primary universe

Fills end at Unix timestamp `1791147599`.
51.0% of active markets and
63.3% of taker volume are attached to markets
resolving after that cutoff. The unrestricted headline is therefore sensitivity analysis only.
Primary inference uses markets whose market and parent-event end dates are both on or
before the fill cutoff.
The exact calendar split no longer clamps later fills into week 3:
0 rows after the seven-day
test interval are explicit overflow.

Unrestricted labels: Excluded=81060, Skilled_Winner=4467, Unskilled_Loser=4279, Unlucky_Loser=1525, Lucky_Winner=918.

Restricted primary labels:
Excluded=38022, Unlucky_Loser=2725, Lucky_Winner=1841, Skilled_Winner=1382, Unskilled_Loser=1029.

## Statistical evidence

Eligible traders (at least 10 fills and 5 events): **5,934**.
Nominal p<0.05 flags: **1,797**, against a
discreteness-adjusted null expectation of **286.2**
(z=91.57). BH survivors: **1,549**
at q=0.05 and **1,658** at q=0.10.
Storey pi0 is 0.802.
The honest nominal Skilled share is **30.28%** of the
eligible cohort; the BH q=0.05 survivor share is
**26.10%**.
Downstream identity-based analyses must use
`labels/account_classification_stage1_fdr_q05.parquet`, not all nominal flags.

## Construct and accounting

For K≤12 events the null is exactly enumerated; larger K uses 10,000 seeded draws with
the `(1+exceedances)/(1+draws)` convention. This both respects the 2^-K floor and prevents
zero Monte Carlo p-values.

Raw event flips satisfy the gross-PnL negation identity:
`True`. P-values are nonzero and the five-event discreteness
floor holds: `False`.
Maker/taker/fee settlement closes within the configured tolerance:
`False`. The measured pre-fee leak is
1.786498 bps of
participant volume, and
100.00%
of maker transaction/counterparty groups map to a taker user.

Wallet IDs are canonical addresses, but beneficial-owner linkage across proxy wallets is
not identifiable from `order_filled` alone. Complete-set mint/merge events are absent, but
their omission is settlement-PnL neutral because token payouts sum to one; direct transfers
and gas remain outside the ledger. Residual token positions are therefore expected.
The audit flags 29
high-volume/near-zero-PnL wallets and
26 high-frequency wallets;
these flags are reported rather than automatically excluded.

## Composition and sanity

Largest category: **bitcoin**, with 26.9% of restricted-universe
volume. Median largest-event share of absolute Skilled PnL:
28.9%.
The largest winner is classified **Skilled_Winner**.
6 of the top 20 winners are not
classified Skilled, illustrating the low-event/concentrated-whale limitation.
Detailed top-20 labels and top-five Skilled histories are saved under `logs/trader_sanity/`.

Top-five Skilled profiles:

- `0x38d812aff0b79f3bf5da2a477f780bcc163eea7c`: 2,448 fills, 236 events, $33,750 PnL, 55.8% BUY fills.
- `0x982a77e75498fa134607f598c861b8de9d0c6851`: 8,109 fills, 391 events, $23,503 PnL, 100.0% BUY fills.
- `0x44c1dfe43260c94ed4f1d00de2e1f80fb113ebc1`: 45 fills, 8 events, $11,407 PnL, 97.8% BUY fills.
- `0x81ca49ad9806de2d92782b873bfa7fe051943b87`: 10 fills, 5 events, $10,718 PnL, 80.0% BUY fills.
- `0x17559efac103ac7f361be37ec0b93888d4c55aac`: 1,160 fills, 127 events, $8,628 PnL, 100.0% BUY fills.

Top-five BUY-fill shares range from 55.8% to 100.0%; heavy-favorite fill shares range from 0.0% to 40.0%.
Gate A establishes non-random profitability; it does **not** by itself establish an
informational mechanism. That distinction is left to OIB/toxicity tests. Strategy,
category, and cross-wallet evidence for the Skilled cohort is in
`logs/skilled_profile/` and [the pipeline reference](../methods/pipeline.md) (Skilled-account analysis).

## What replicated and what did not

- Event-split persistence remains a supporting robustness result; it does not override Gate A.
- Directional OIB remains unsupported with execution-price VWAP and truncated price paths.
- The primary universe requires both the market and its parent event to be scheduled to end
  by the fill cutoff; market-only end dates admitted the Harris 2024 winner market.
- Any Stage 3 result must use frozen labels and strict temporal provenance. Stage 4 proceeds
  only if the Gate A line above is PASS.

</details>
